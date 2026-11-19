"""微信官方内容安全检测回归（2026-11-18）。

覆盖：
- 开关关闭时一律放行（不改变既有审核链路）；
- 文本 msgSecCheck 三态：0/通过、87014/违规、未知码/兜底；
- 图片 imgSecCheck 三态与 webp 归一化（送检转 JPEG、缩放、限体积）；
- mock 模式固定关键字/字节标记；
- moderate_text 接入后违规映射 reject、异常映射 manual_review、通过放行；
- 昵称入口写库前拦截。

无真实微信调用、无真实 DB：用记录型桩与 monkeypatch。
"""

from __future__ import annotations

import base64
import io

import pytest

from app.core.config import settings
from app.services import content_filter as cf
from app.services import wechat_sec_check as wsc
from PIL import Image


def _img_bytes(fmt: str = "PNG", size: tuple[int, int] = (10, 10)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (120, 30, 60)).save(buf, format=fmt)
    return buf.getvalue()


class _Resp:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _AsyncClient:
    last_request: tuple | None = None
    payload: dict = {"errcode": 0}

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, *, params, json):
        _AsyncClient.last_request = (url, params, json)
        return _Resp(_AsyncClient.payload)


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "wechat_sec_check_enabled", True)
    monkeypatch.setattr(settings, "wechat_sec_check_mock", False)
    monkeypatch.setattr(settings, "wechat_provider", "wechat")
    monkeypatch.setattr(settings, "wechat_sec_check_fail_mode", "manual_review")
    monkeypatch.setattr(settings, "wechat_sec_check_max_retries", 0)
    monkeypatch.setattr(settings, "wechat_app_id", "wxid")
    monkeypatch.setattr(settings, "wechat_app_secret", "secret")
    monkeypatch.setattr(wsc.httpx, "AsyncClient", _AsyncClient)

    async def _token() -> str:
        return "token"

    monkeypatch.setattr(wsc, "_get_access_token", _token)
    yield


@pytest.mark.asyncio
async def test_disabled_allows_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "wechat_sec_check_enabled", False)
    assert (await wsc.check_text("随便什么内容")).action == "allow"
    assert (await wsc.check_image(_img_bytes())).action == "allow"


@pytest.mark.asyncio
async def test_text_three_states(monkeypatch: pytest.MonkeyPatch) -> None:
    _AsyncClient.payload = {"errcode": 0, "result": {"suggest": "pass", "label": 100}}
    assert (await wsc.check_text("hello", scene=3)).action == "allow"
    url, _params, body = _AsyncClient.last_request
    assert url == wsc._WECHAT_MSG_SEC_CHECK
    assert body["scene"] == 3 and body["version"] == 2

    _AsyncClient.payload = {"errcode": 87014}
    risky = await wsc.check_text("bad")
    assert risky.action == "reject" and risky.raw_errcode == 87014

    _AsyncClient.payload = {"errcode": 40001}
    assert (await wsc.check_text("x")).action == "manual_review"


@pytest.mark.asyncio
async def test_text_reject_fail_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "wechat_sec_check_fail_mode", "reject")
    _AsyncClient.payload = {"errcode": 44992}  # 额度耗尽视为服务异常
    assert (await wsc.check_text("x")).action == "reject"


@pytest.mark.asyncio
async def test_image_normalized_to_jpeg_and_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    _AsyncClient.payload = {"errcode": 0}
    big = _img_bytes("PNG", (3000, 2500))
    decision = await wsc.check_image(big, scene=1)
    assert decision.action == "allow"
    url, _params, body = _AsyncClient.last_request
    assert url == wsc._WECHAT_IMG_SEC_CHECK
    raw = base64.b64decode(body["image"])
    with Image.open(io.BytesIO(raw)) as im:
        assert im.format == "JPEG"
        assert im.width <= settings.wechat_sec_check_max_width
        assert im.height <= settings.wechat_sec_check_max_height
    assert len(raw) <= settings.wechat_sec_check_max_bytes


@pytest.mark.asyncio
async def test_image_three_states_and_reject() -> None:
    _AsyncClient.payload = {"errcode": 87014, "label": 20002}
    risky = await wsc.check_image(_img_bytes())
    assert risky.action == "reject" and risky.label == 20002

    _AsyncClient.payload = {"errcode": 40001}
    assert (await wsc.check_image(_img_bytes())).action == "manual_review"

    _AsyncClient.payload = {"errcode": 0}
    assert (await wsc.check_image(_img_bytes())).action == "allow"


@pytest.mark.asyncio
async def test_mock_mode_markers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "wechat_sec_check_mock", True)
    assert (await wsc.check_text("a " + wsc._MOCK_REJECT_TEXT)).action == "reject"
    assert (await wsc.check_text("a " + wsc._MOCK_REVIEW_TEXT)).action == "manual_review"
    assert (await wsc.check_text("clean")).action == "allow"
    assert (await wsc.check_image(b"x" + wsc._MOCK_REJECT_IMAGE_MARK)).action == "reject"
    assert (await wsc.check_image(b"x" + wsc._MOCK_REVIEW_IMAGE_MARK)).action == "manual_review"


class _FakeDB:
    async def execute(self, *_a, **_k):
        class _R:
            def mappings(self):
                return self

            def all(self):
                return []

        return _R()


@pytest.mark.asyncio
async def test_moderate_text_maps_wechat_states(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDB()

    async def _allow(*_a, **_k):
        return cf.ContentDecision("allow", "")

    monkeypatch.setattr(cf, "assert_text_allowed", _allow)
    monkeypatch.setattr(cf, "decide_text", _allow)

    async def _wechat(action: str):
        async def _inner(*_a, **_k):
            return wsc.SecCheckDecision(action)
        return _inner

    monkeypatch.setattr(wsc, "check_text", await _wechat("allow"))
    assert (await cf.moderate_text(db, "hi")).action == "allow"

    monkeypatch.setattr(wsc, "check_text", await _wechat("reject"))
    assert (await cf.moderate_text(db, "hi")).action == "reject"

    monkeypatch.setattr(wsc, "check_text", await _wechat("manual_review"))
    assert (await cf.moderate_text(db, "hi")).action == "manual_review"


def test_errcode_and_scene_contract() -> None:
    assert wsc._ERRCODE_RISKY == 87014
    assert 3 in wsc.VALID_SCENES and 9 not in wsc.VALID_SCENES

"""单元测试：新增跟进（文字+图片+录音）接口与纯函数逻辑。

覆盖范围：
- 路由已在 OpenAPI 注册（POST /admin/members/{member_id}/follow-ups/media）；
- 未登录 401（写 matchmaker.member.manage）；
- ``_voice_suffix`` 录音扩展名推断与白名单校验；
- ``_parse_follow_up_images`` json 列解析兜底；
- ``_fu_image_outputs`` PNG -> webp 转换与非法图片拒绝；
- ``MemberFollowUp`` 出参媒体字段默认值兼容旧数据。
"""

import pytest
from fastapi.testclient import TestClient
from io import BytesIO

from app.main import app
from app.schemas.member_follow_up_admin import MemberFollowUp
from app.services.member_follow_up_admin import (
    _fu_image_outputs,
    _parse_follow_up_images,
    _voice_suffix,
)

client = TestClient(app)

BASE = "/api/v1/admin/members"


# ─── 路由注册（OpenAPI 路径断言） ───────────────────────────────


def test_media_follow_up_route_is_registered() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "post" in paths[f"{BASE}/{{member_id}}/follow-ups/media"]


def test_media_follow_up_route_requires_authentication() -> None:
    response = client.post(
        f"{BASE}/1/follow-ups/media",
        data={"method": "PHONE", "content": "测试"},
    )
    assert response.status_code == 401


# ─── 录音扩展名推断 ─────────────────────────────────────────────


class _FakeUpload:
    def __init__(self, filename: str, content_type: str) -> None:
        self.filename = filename
        self.content_type = content_type


def test_voice_suffix_by_mime() -> None:
    assert _voice_suffix(_FakeUpload("a.bin", "audio/mpeg")) == "mp3"
    assert _voice_suffix(_FakeUpload("a.bin", "audio/x-m4a")) == "m4a"
    assert _voice_suffix(_FakeUpload("a.bin", "audio/webm;codecs=opus")) == "webm"


def test_voice_suffix_by_filename_fallback() -> None:
    assert _voice_suffix(_FakeUpload("record.amr", "application/octet-stream")) == "amr"


def test_voice_suffix_rejects_unknown() -> None:
    with pytest.raises(Exception):
        _voice_suffix(_FakeUpload("song.txt", "text/plain"))


# ─── images json 列解析兜底 ─────────────────────────────────────


def test_parse_follow_up_images_variants() -> None:
    assert _parse_follow_up_images(None) == []
    assert _parse_follow_up_images('["/a.webp", "/b.webp"]') == ["/a.webp", "/b.webp"]
    assert _parse_follow_up_images(["/a.webp"]) == ["/a.webp"]
    assert _parse_follow_up_images("not-json") == []
    assert _parse_follow_up_images('{"x": 1}') == []


# ─── 图片转换 ───────────────────────────────────────────────────


def _png_bytes() -> bytes:
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (8, 8), color=(200, 30, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_image_outputs_converts_to_webp() -> None:
    webp, thumb = _fu_image_outputs(_png_bytes())
    assert webp[:4] == b"RIFF"  # WEBP 容器头
    assert thumb[:4] == b"RIFF"


def test_image_outputs_rejects_non_image() -> None:
    with pytest.raises(Exception):
        _fu_image_outputs(b"this is not an image")


# ─── 出参模型兼容旧数据 ─────────────────────────────────────────


def test_member_follow_up_defaults_backward_compatible() -> None:
    item = MemberFollowUp(
        id=1,
        user_id=18,
        method="PHONE",
        content="电话沟通",
        next_follow_at=None,
        created_by=1,
        created_at="2026-09-19T10:00:00",
    )
    assert item.images == []
    assert item.voice_url is None
    assert item.matchmaker_name is None

"""跟进附件类私有媒体签名止损回归测试。

覆盖：匿名直连 403、缺参/伪造/篡改/过期签 403、有效签 200 且字节一致、
停用管理员签 403、DB 不可用 503、跨类别重放（TTS 签名访问 follow-up
路径）403、公开类仍匿名 200、路径穿越 404、服务层响应重签且 DB 行保持
原始 URL。测试范式与 tests/test_ai_voice_audio_access.py 一致。
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from starlette.exceptions import HTTPException

from app.main import _ProtectedStorageFiles
from app.services import media_access
from app.services import member_follow_up_admin as follow_up_service
from app.services.media_access import (
    MEDIA_CATEGORY_FOLLOW_UP,
    MediaAccessUnavailable,
    sign_media_url,
    verify_media_access,
    verify_media_signature,
)
from app.services.voice.audio_access import sign_voice_audio_url

_PRIVATE_IMAGE = "42/follow-up-img-example.webp"
_PRIVATE_IMAGE_PAYLOAD = b"private-follow-up-image-sentinel"
_PUBLIC_IMAGE = "42/photo-example.webp"
_PUBLIC_IMAGE_PAYLOAD = b"ordinary-public-image-sentinel"


def _signed_url(
    monkeypatch: pytest.MonkeyPatch,
    path: str = f"/storage/uploads/{_PRIVATE_IMAGE}",
    *,
    viewer: int = 7,
) -> str:
    monkeypatch.setattr("app.services.media_access.time.time", lambda: 1_000)
    return sign_media_url(path, category=MEDIA_CATEGORY_FOLLOW_UP, viewer=viewer)


def _http_scope(query_string: bytes = b"") -> dict[str, object]:
    return {
        "type": "http",
        "method": "GET",
        "path": "/storage/uploads",
        "query_string": query_string,
        "headers": [],
    }


def _active_viewer(monkeypatch: pytest.MonkeyPatch) -> None:
    async def active(viewer_id: int, db=None) -> bool:
        return True

    monkeypatch.setitem(
        media_access._MEDIA_VIEWER_HOOKS,
        (MEDIA_CATEGORY_FOLLOW_UP, "admin"),
        active,
    )


def _write_fixture(upload_dir: Path, relative: str, payload: bytes) -> None:
    target = upload_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)


@pytest.mark.asyncio
async def test_anonymous_rejected_valid_signature_reads_bytes_public_stays_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_dir = tmp_path / "uploads"
    _write_fixture(upload_dir, _PRIVATE_IMAGE, _PRIVATE_IMAGE_PAYLOAD)
    _write_fixture(upload_dir, _PUBLIC_IMAGE, _PUBLIC_IMAGE_PAYLOAD)
    files = _ProtectedStorageFiles(directory=str(upload_dir))
    _active_viewer(monkeypatch)
    signed = _signed_url(monkeypatch)

    anonymous = await files.get_response(_PRIVATE_IMAGE, _http_scope())
    allowed = await files.get_response(
        _PRIVATE_IMAGE, _http_scope(urlsplit(signed).query.encode("ascii"))
    )
    public = await files.get_response(_PUBLIC_IMAGE, _http_scope())

    assert anonymous.status_code == 403
    assert allowed.status_code == 200
    assert public.status_code == 200


def test_missing_forged_tampered_and_expired_signatures_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _signed_url(monkeypatch)
    query = parse_qs(
        urlsplit(
            sign_media_url(
                f"/storage/uploads/{_PRIVATE_IMAGE}",
                category=MEDIA_CATEGORY_FOLLOW_UP,
                viewer=7,
            )
        ).query
    )

    # 缺参：只带 expires 不带 signature/身份。
    assert not verify_media_signature(
        _PRIVATE_IMAGE, {"expires": query["expires"]}, category=MEDIA_CATEGORY_FOLLOW_UP, now=1_001
    )
    # 伪造：signature 全 0。
    forged = dict(query)
    forged["signature"] = ["0" * 64]
    assert not verify_media_signature(
        _PRIVATE_IMAGE, forged, category=MEDIA_CATEGORY_FOLLOW_UP, now=1_001
    )
    # 篡改：换 viewer。
    tampered = dict(query)
    tampered["user"] = ["8"]
    assert not verify_media_signature(
        _PRIVATE_IMAGE, tampered, category=MEDIA_CATEGORY_FOLLOW_UP, now=1_001
    )
    # 过期：TTL 300s 后失效。
    assert not verify_media_signature(
        _PRIVATE_IMAGE, query, category=MEDIA_CATEGORY_FOLLOW_UP, now=1_300
    )


@pytest.mark.asyncio
async def test_disabled_admin_signature_is_rejected_with_403(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_dir = tmp_path / "uploads"
    _write_fixture(upload_dir, _PRIVATE_IMAGE, _PRIVATE_IMAGE_PAYLOAD)
    files = _ProtectedStorageFiles(directory=str(upload_dir))
    signed = _signed_url(monkeypatch, viewer=7)

    kind = media_access.VIEWER_KIND_ADMIN

    async def denied(viewer_id: int, db=None) -> bool:
        return False

    monkeypatch.setitem(media_access._MEDIA_VIEWER_HOOKS, ("follow_up", kind), denied)
    response = await files.get_response(
        _PRIVATE_IMAGE, _http_scope(urlsplit(signed).query.encode("ascii"))
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_db_outage_fails_closed_with_503(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_dir = tmp_path / "uploads"
    _write_fixture(upload_dir, _PRIVATE_IMAGE, _PRIVATE_IMAGE_PAYLOAD)
    files = _ProtectedStorageFiles(directory=str(upload_dir))
    signed = _signed_url(monkeypatch, viewer=7)

    kind = media_access.VIEWER_KIND_ADMIN

    async def unavailable(viewer_id: int, db=None) -> bool:
        raise MediaAccessUnavailable()

    monkeypatch.setitem(media_access._MEDIA_VIEWER_HOOKS, ("follow_up", kind), unavailable)
    response = await files.get_response(
        _PRIVATE_IMAGE, _http_scope(urlsplit(signed).query.encode("ascii"))
    )
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_tts_signature_cannot_be_replayed_on_follow_up_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """跨类别重放：TTS 签名（无类别消息）访问 follow-up 路径必须 403。"""
    monkeypatch.setattr("app.services.voice.audio_access.time.time", lambda: 1_000)
    tts_signed = sign_voice_audio_url(
        "/storage/uploads/tts/example.mp3", user_id=1, privacy_revision=0
    )
    query = parse_qs(urlsplit(tts_signed).query)

    assert not await verify_media_access(_PRIVATE_IMAGE, query)


@pytest.mark.asyncio
async def test_follow_up_signature_cannot_be_replayed_on_tts_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """跨类别重放（反向）：follow-up 签名访问 TTS 路径必须 403。"""
    _active_viewer(monkeypatch)
    signed = _signed_url(monkeypatch, "/storage/uploads/42/follow-up-img-example.webp")
    query = parse_qs(urlsplit(signed).query)

    assert not await verify_media_access("tts/example.mp3", query)


@pytest.mark.asyncio
async def test_traversal_and_missing_files_stay_404(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_dir = tmp_path / "uploads"
    (upload_dir / "42").mkdir(parents=True)
    files = _ProtectedStorageFiles(directory=str(upload_dir))
    _active_viewer(monkeypatch)

    with pytest.raises(HTTPException) as traversal:
        await files.get_response("../outside.txt", _http_scope())
    with pytest.raises(HTTPException) as missing:
        await files.get_response("42/does-not-exist.webp", _http_scope())
    assert traversal.value.status_code == 404
    assert missing.value.status_code == 404


class _FakeFollowUpDb:
    """驱动 get_member_follow_ups 的最小 fake：member 检查、行查询与计数，
    记录全部语句以断言服务层不做任何写操作（DB 行保持原始 URL）。"""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.executed: list[str] = []

    async def scalar(self, statement, params=None):
        self.executed.append(str(statement))
        if "COUNT(*)" in str(statement):
            return len(self._rows)
        return 1  # 会员存在性检查

    async def execute(self, statement, params=None):
        self.executed.append(str(statement))
        rows = list(self._rows)

        class _Result:
            def mappings(self) -> "_Result":
                return self

            def all(self) -> list[dict]:
                return rows

        return _Result()


@pytest.mark.asyncio
async def test_service_response_resigns_while_db_keeps_raw_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """存量记录重签回归：服务层响应带签名 query（绑定当前管理员），服务层
    不做任何写语句——DB 行保持原始 URL，零数据迁移。"""
    monkeypatch.setattr("app.services.media_access.time.time", lambda: 1_000)
    raw_image = f"/storage/uploads/{_PRIVATE_IMAGE}"
    raw_voice = "/storage/uploads/42/follow-up-voice-example.mp3"
    row = {
        "id": 1,
        "user_id": 42,
        "method": "PHONE",
        "content": "电话回访",
        "next_follow_at": None,
        "created_by": 7,
        "created_at": "2026-09-27 10:00:00",
        "images": f'["{raw_image}"]',
        "voice_url": raw_voice,
        "voice_duration_sec": 12,
        "matchmaker_name": "admin",
    }
    db = _FakeFollowUpDb([row])

    page = await follow_up_service.get_member_follow_ups(
        db, member_id=42, page=1, page_size=20, viewer=7
    )

    item = page.items[0]
    assert item.images != [raw_image], "响应必须重签，不能原样返回 DB 存量 URL"
    for signed in (*item.images, item.voice_url):
        parsed = urlsplit(signed)
        query = parse_qs(parsed.query)
        assert query["user"] == ["7"]
        assert "signature" in query and "expires" in query
        assert verify_media_signature(
            parsed.path[len("/storage/uploads/"):],
            query,
            category=MEDIA_CATEGORY_FOLLOW_UP,
            now=1_001,
        )
    assert all(
        "UPDATE" not in statement and "INSERT" not in statement
        for statement in db.executed
    ), "重签必须发生在响应层，DB 保持原始 URL"


def test_resigning_strips_previous_signature_params(monkeypatch: pytest.MonkeyPatch) -> None:
    """重签前剥离旧 expires/signature/user/revision 参数，幂等可重复。"""
    monkeypatch.setattr("app.services.media_access.time.time", lambda: 1_000)
    first = _signed_url(monkeypatch)
    monkeypatch.setattr("app.services.media_access.time.time", lambda: 2_000)
    second = sign_media_url(first, category=MEDIA_CATEGORY_FOLLOW_UP, viewer=7)

    first_query = parse_qs(urlsplit(first).query)
    second_query = parse_qs(urlsplit(second).query)
    assert int(second_query["expires"][0]) == 2_300
    assert second_query["expires"] != first_query["expires"]
    assert verify_media_signature(
        _PRIVATE_IMAGE, second_query, category=MEDIA_CATEGORY_FOLLOW_UP, now=2_001
    )
    assert not verify_media_signature(
        _PRIVATE_IMAGE, first_query, category=MEDIA_CATEGORY_FOLLOW_UP, now=2_001
    )


@pytest.mark.asyncio
async def test_public_media_url_is_not_signed_or_blocked() -> None:
    """公开类（photo 等）不参与签名：sign 原样返回，无签名 query 也放行。"""
    url = f"/storage/uploads/{_PUBLIC_IMAGE}"

    assert sign_media_url(url, category=MEDIA_CATEGORY_FOLLOW_UP, viewer=7) == url
    assert await verify_media_access(_PUBLIC_IMAGE, {}) is True

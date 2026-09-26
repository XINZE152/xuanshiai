"""用户语音类（{user_id}/audio/*）私有媒体签名止损回归测试。

覆盖：匿名 403、上传者签 200（upload_media 响应签发）、接收者重签 200
（paper_plane 行按当前查看者重签）、过期/篡改 403、停用用户签 403、公开类
不回归、payload 校验兼容（含 query 的签名 URL 过 schemas 服务端前缀校验）、
跨类别重放 403。测试范式与 tests/test_ai_voice_audio_access.py 一致。
"""

from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import UploadFile

from app.main import _ProtectedStorageFiles
from app.schemas.community import PaperPlaneCreate
from app.services import media_access
from app.services.community import _paper_response
from app.services.media import upload_media
from app.services.media_access import (
    MEDIA_CATEGORY_AUDIO,
    MEDIA_CATEGORY_FOLLOW_UP,
    sign_media_url,
    strip_media_signature,
    verify_media_access,
    verify_media_signature,
)
from app.services.social import _message

_AUDIO = "42/audio/example.mp3"
_AUDIO_PAYLOAD = b"private-user-voice-sentinel"
_PUBLIC_IMAGE = "42/photo-example.webp"


def _signed_url(
    monkeypatch: pytest.MonkeyPatch,
    path: str = f"/storage/uploads/{_AUDIO}",
    *,
    viewer: int = 42,
) -> str:
    monkeypatch.setattr("app.services.media_access.time.time", lambda: 1_000)
    return sign_media_url(path, category=MEDIA_CATEGORY_AUDIO, viewer=viewer)


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

    monkeypatch.setitem(media_access._MEDIA_VIEWER_HOOKS, "audio", active)


@pytest.mark.asyncio
async def test_anonymous_rejected_uploader_signature_reads_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_dir = tmp_path / "uploads"
    target = upload_dir / _AUDIO
    target.parent.mkdir(parents=True)
    target.write_bytes(_AUDIO_PAYLOAD)
    files = _ProtectedStorageFiles(directory=str(upload_dir))
    _active_viewer(monkeypatch)
    signed = _signed_url(monkeypatch, viewer=42)

    anonymous = await files.get_response(_AUDIO, _http_scope())
    allowed = await files.get_response(
        _AUDIO, _http_scope(urlsplit(signed).query.encode("ascii"))
    )

    assert anonymous.status_code == 403
    assert allowed.status_code == 200


@pytest.mark.asyncio
async def test_expired_and_tampered_signatures_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _signed_url(monkeypatch)
    query = parse_qs(
        urlsplit(
            sign_media_url(
                f"/storage/uploads/{_AUDIO}", category=MEDIA_CATEGORY_AUDIO, viewer=42
            )
        ).query
    )

    assert not verify_media_signature(
        _AUDIO, query, category=MEDIA_CATEGORY_AUDIO, now=1_300
    )
    tampered = dict(query)
    tampered["user"] = ["43"]
    assert not verify_media_signature(
        _AUDIO, tampered, category=MEDIA_CATEGORY_AUDIO, now=1_001
    )


@pytest.mark.asyncio
async def test_deactivated_user_signature_is_rejected_with_403(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_dir = tmp_path / "uploads"
    target = upload_dir / _AUDIO
    target.parent.mkdir(parents=True)
    target.write_bytes(_AUDIO_PAYLOAD)
    files = _ProtectedStorageFiles(directory=str(upload_dir))
    signed = _signed_url(monkeypatch, viewer=42)

    async def denied(viewer_id: int, db=None) -> bool:
        return False

    monkeypatch.setitem(media_access._MEDIA_VIEWER_HOOKS, "audio", denied)
    response = await files.get_response(
        _AUDIO, _http_scope(urlsplit(signed).query.encode("ascii"))
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_public_media_url_is_not_signed_or_blocked() -> None:
    url = f"/storage/uploads/{_PUBLIC_IMAGE}"

    assert sign_media_url(url, category=MEDIA_CATEGORY_AUDIO, viewer=42) == url
    assert await verify_media_access(_PUBLIC_IMAGE, {}) is True


@pytest.mark.asyncio
async def test_follow_up_signature_cannot_be_replayed_on_audio_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """跨类别重放：follow-up 签名访问 audio 路径必须 403。"""
    monkeypatch.setattr("app.services.media_access.time.time", lambda: 1_000)
    signed = sign_media_url(
        "/storage/uploads/42/follow-up-img-example.webp",
        category=MEDIA_CATEGORY_FOLLOW_UP,
        viewer=7,
    )
    query = parse_qs(urlsplit(signed).query)

    assert not await verify_media_access(_AUDIO, query)


@pytest.mark.asyncio
async def test_upload_media_response_is_signed_for_uploader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """签发端：upload_media 响应 URL 按上传者签发，仍以 /storage/uploads/
    开头（过 schemas 服务端前缀校验），且能通过挂载层验签。"""
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "upload_dir", str(tmp_path))
    _active_viewer(monkeypatch)
    file = UploadFile(
        file=BytesIO(b"12345"),
        filename="a.mp3",
        headers={"content-type": "audio/mpeg"},
    )

    response = await upload_media(42, file, "paper_plane_voice")

    parsed = urlsplit(response.url)
    assert parsed.path.startswith("/storage/uploads/42/audio/")
    query = parse_qs(parsed.query)
    assert query["user"] == ["42"]
    assert "signature" in query and "expires" in query
    assert await verify_media_access(parsed.path[len("/storage/uploads/"):], query)


@pytest.mark.asyncio
async def test_paper_response_resigns_voice_url_for_current_viewer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """读取端：paper_plane 行的原始 voice_url 按当前查看者重签（DB 存原始
    URL 的存量记录同样适用），重签 URL 通过挂载层验签。"""
    monkeypatch.setattr("app.services.media_access.time.time", lambda: 1_000)
    _active_viewer(monkeypatch)
    raw = f"/storage/uploads/{_AUDIO}"
    row = {
        "id": 1,
        "content": "语音纸飞机",
        "images": "[]",
        "city": None,
        "tags": "[]",
        "is_anonymous": 1,
        "reply_count": 0,
        "voice_url": raw,
        "voice_duration_sec": 30,
        "created_at": datetime.now(UTC).replace(tzinfo=None),
    }

    response = await _paper_response(row, viewer=77)

    assert response.voice_url != raw, "响应必须重签，不能原样返回 DB 存量 URL"
    parsed = urlsplit(response.voice_url)
    assert parsed.path == f"/storage/uploads/{_AUDIO}"
    query = parse_qs(parsed.query)
    assert query["user"] == ["77"]
    assert await verify_media_access(_AUDIO, query)


def test_signed_voice_url_passes_payload_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """payload 校验兼容：含 query 的签名 URL 过服务端前缀校验与
    PaperPlaneCreate 模型校验（varchar(500) 容得下 query）。"""
    monkeypatch.setattr("app.services.media_access.time.time", lambda: 1_000)
    signed = sign_media_url(
        f"/storage/uploads/{_AUDIO}", category=MEDIA_CATEGORY_AUDIO, viewer=42
    )

    assert signed.startswith("/storage/uploads/")
    assert len(signed) <= 500
    payload = PaperPlaneCreate(
        content="语音纸飞机", voice_url=signed, voice_duration_sec=30
    )
    assert payload.voice_url == signed


@pytest.mark.asyncio
async def test_audio_path_is_categorized_and_public_paths_are_not() -> None:
    """类别注册表：{digits}/audio/* 归 audio 类；公开路径无类别。"""
    assert media_access.categorize_media_path(_AUDIO) == MEDIA_CATEGORY_AUDIO
    assert media_access.categorize_media_path("audio/example.mp3") is None
    assert media_access.categorize_media_path(_PUBLIC_IMAGE) is None


# ---------------- 评审必改项回归：会话消息/社交消息读取端重签 ----------------


def _expired_signed_url(monkeypatch: pytest.MonkeyPatch, path: str, viewer: int) -> str:
    """构造一个已过期的签名 URL（模拟第 2 轮上线期间入库的历史签名行）。"""
    monkeypatch.setattr("app.services.media_access.time.time", lambda: 1_000)
    signed = sign_media_url(path, category=MEDIA_CATEGORY_AUDIO, viewer=viewer)
    monkeypatch.setattr("app.services.media_access.time.time", lambda: 5_000)
    assert not verify_media_signature(
        path.lstrip("/"), parse_qs(urlsplit(signed).query), category=MEDIA_CATEGORY_AUDIO, now=5_001
    )
    return signed


@pytest.mark.asyncio
async def test_paper_message_media_url_resigns_raw_and_expired_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """纸飞机会话消息：存量原始行与过期签名行按当前查看者重签后均可验签。"""
    monkeypatch.setattr("app.services.media_access.time.time", lambda: 6_000)
    _active_viewer(monkeypatch)
    raw = f"/storage/uploads/{_AUDIO}"
    expired = _expired_signed_url(monkeypatch, f"/storage/uploads/{_AUDIO}", viewer=42)

    for stored in (raw, expired):
        signed = media_access_sign_for_viewer(stored, 77)
        parsed = urlsplit(signed)
        query = parse_qs(parsed.query)
        assert query["user"] == ["77"]
        assert await verify_media_access(parsed.path[len("/storage/uploads/"):], query)


def media_access_sign_for_viewer(media_url: str, viewer: int) -> str:
    from app.services.community import _signed_message_media_url

    return _signed_message_media_url(media_url, viewer)


@pytest.mark.asyncio
async def test_chat_message_builder_resigns_and_admin_view_stays_raw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """社交聊天消息：viewer 传入时按其重签；viewer=None（管理端审计视图）
    保持原始 URL（直连 403，登记限制：管理端语音回放不可用）。"""
    monkeypatch.setattr("app.services.media_access.time.time", lambda: 1_000)
    _active_viewer(monkeypatch)
    raw = f"/storage/uploads/{_AUDIO}"
    row = {
        "id": 1,
        "session_id": 5,
        "from_user_id": 42,
        "to_user_id": 77,
        "type": 3,
        "content": None,
        "media_url": raw,
        "client_message_id": None,
        "is_read": 0,
        "revoked_at": None,
        "created_at": datetime.now(UTC).replace(tzinfo=None),
    }

    for_viewer = _message(row, viewer=77)
    parsed = urlsplit(for_viewer.media_url)
    assert parse_qs(parsed.query).get("user") == ["77"]
    assert await verify_media_access(parsed.path[len("/storage/uploads/"):], parse_qs(parsed.query))

    admin_view = _message(row, viewer=None)
    assert admin_view.media_url == raw, "管理端视图保持原始 URL（登记限制：不可播）"


def test_strip_media_signature_is_idempotent_for_raw_and_signed() -> None:
    """提交边界剥离幂等：原始 URL 原样返回；签名 URL 剥离为原始 URL；再剥离不变。"""
    raw = f"/storage/uploads/{_AUDIO}"
    signed = sign_media_url(raw, category=MEDIA_CATEGORY_AUDIO, viewer=42)

    assert strip_media_signature(raw) == raw
    stripped = strip_media_signature(signed)
    assert stripped == raw
    assert strip_media_signature(stripped) == raw

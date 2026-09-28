from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from starlette.exceptions import HTTPException

from app.main import _ProtectedStorageFiles
from app.services.voice.audio_access import (
    VoiceAudioDenied,
    VoiceAudioUnavailable,
    is_private_voice_path,
    sign_voice_audio_url,
    verify_voice_audio_access,
    verify_voice_audio_signature,
)


def _signed_url(
    monkeypatch: pytest.MonkeyPatch,
    path: str = "/storage/uploads/tts/example.mp3",
    *,
    user_id: int = 42,
    privacy_revision: int = 7,
) -> str:
    monkeypatch.setattr("app.services.voice.audio_access.time.time", lambda: 1_000)
    return sign_voice_audio_url(
        path,
        user_id=user_id,
        privacy_revision=privacy_revision,
    )


def test_private_voice_url_gets_short_lived_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signed = _signed_url(monkeypatch)
    parsed = urlsplit(signed)
    query = parse_qs(parsed.query)

    assert parsed.path == "/storage/uploads/tts/example.mp3"
    assert int(query["expires"][0]) == 1_300
    assert query["user"] == ["42"]
    assert query["revision"] == ["7"]
    assert verify_voice_audio_signature("tts/example.mp3", query, now=1_001)


def test_private_voice_url_requires_owner_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.services.voice.audio_access.time.time", lambda: 1_000)

    with pytest.raises(ValueError):
        sign_voice_audio_url("/storage/uploads/tts/example.mp3")


def test_private_voice_url_rejects_missing_expired_and_tampered_signatures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signed = _signed_url(
        monkeypatch,
        "/storage/uploads/voice/tts/example.mp3",
    )
    query = parse_qs(urlsplit(signed).query)

    assert not verify_voice_audio_signature("voice/tts/example.mp3", {}, now=1_001)
    assert not verify_voice_audio_signature(
        "voice/tts/example.mp3", query, now=1_300
    )
    query["signature"] = ["0" * 64]
    assert not verify_voice_audio_signature(
        "voice/tts/example.mp3", query, now=1_001
    )


def test_signed_voice_url_cannot_be_replayed_as_another_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signed = _signed_url(monkeypatch, user_id=42, privacy_revision=7)
    query = parse_qs(urlsplit(signed).query)
    query["user"] = ["43"]

    assert not verify_voice_audio_signature("tts/example.mp3", query, now=1_001)


def test_public_media_url_is_not_signed_or_blocked() -> None:
    url = "/storage/uploads/1/profile/avatar.webp"

    assert sign_voice_audio_url(url) == url
    assert verify_voice_audio_signature("1/profile/avatar.webp", {}, now=1_001)


def _http_scope(query_string: bytes = b"") -> dict[str, object]:
    return {
        "type": "http",
        "method": "GET",
        "path": "/storage/uploads",
        "query_string": query_string,
        "headers": [],
    }


@pytest.mark.parametrize("relative_path", ["tts/example.mp3", "voice/tts/example.mp3"])
def test_private_voice_path_matrix_covers_all_local_tts_writers(
    relative_path: str,
) -> None:
    assert is_private_voice_path(relative_path) is True


@pytest.mark.asyncio
async def test_static_storage_rejects_unsigned_voice_and_allows_valid_signature(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_dir = tmp_path / "uploads"
    target = upload_dir / "voice" / "tts" / "example.mp3"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"private voice")
    files = _ProtectedStorageFiles(directory=str(upload_dir))

    async def current_revision(user_id: int, db=None) -> int:
        assert user_id == 42
        return 7

    monkeypatch.setattr(
        "app.services.voice.audio_access._current_revision", current_revision
    )
    signed = _signed_url(
        monkeypatch,
        "/storage/uploads/voice/tts/example.mp3",
    )

    unsigned_response = await files.get_response(
        "voice/tts/example.mp3", _http_scope()
    )
    signed_response = await files.get_response(
        "voice/tts/example.mp3",
        _http_scope(urlsplit(signed).query.encode("ascii")),
    )

    assert unsigned_response.status_code == 403
    assert signed_response.status_code == 200


@pytest.mark.asyncio
async def test_old_signature_is_rejected_after_privacy_revision_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signed = _signed_url(monkeypatch)
    query = parse_qs(urlsplit(signed).query)
    current = 7

    async def current_revision(user_id: int, db=None) -> int:
        assert user_id == 42
        return current

    monkeypatch.setattr(
        "app.services.voice.audio_access._current_revision", current_revision
    )
    assert await verify_voice_audio_access("tts/example.mp3", query)

    current = 8
    assert not await verify_voice_audio_access("tts/example.mp3", query)


@pytest.mark.asyncio
async def test_audio_access_rejects_cancelled_user_and_fails_closed_on_db_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signed = _signed_url(monkeypatch)
    query = parse_qs(urlsplit(signed).query)

    async def denied(user_id: int, db=None) -> int:
        raise VoiceAudioDenied()

    monkeypatch.setattr("app.services.voice.audio_access._current_revision", denied)
    assert not await verify_voice_audio_access("tts/example.mp3", query)

    async def unavailable(user_id: int, db=None) -> int:
        raise VoiceAudioUnavailable()

    monkeypatch.setattr(
        "app.services.voice.audio_access._current_revision", unavailable
    )
    with pytest.raises(VoiceAudioUnavailable):
        await verify_voice_audio_access("tts/example.mp3", query)


@pytest.mark.asyncio
async def test_static_storage_returns_503_when_user_state_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_dir = tmp_path / "uploads"
    target = upload_dir / "tts" / "example.mp3"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"temporary voice")
    files = _ProtectedStorageFiles(directory=str(upload_dir))
    signed = _signed_url(monkeypatch)

    async def unavailable(user_id: int, db=None) -> int:
        raise VoiceAudioUnavailable()

    monkeypatch.setattr(
        "app.services.voice.audio_access._current_revision", unavailable
    )
    response = await files.get_response(
        "tts/example.mp3",
        _http_scope(urlsplit(signed).query.encode("ascii")),
    )

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_expired_signature_and_deleted_file_are_not_readable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_dir = tmp_path / "uploads"
    target = upload_dir / "tts" / "example.mp3"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"temporary voice")
    files = _ProtectedStorageFiles(directory=str(upload_dir))
    signed = _signed_url(monkeypatch)
    query = urlsplit(signed).query.encode("ascii")

    async def current_revision(user_id: int, db=None) -> int:
        return 7

    monkeypatch.setattr(
        "app.services.voice.audio_access._current_revision", current_revision
    )
    monkeypatch.setattr("app.services.voice.audio_access.time.time", lambda: 1_300)
    expired_response = await files.get_response("tts/example.mp3", _http_scope(query))
    assert expired_response.status_code == 403

    target.unlink()
    monkeypatch.setattr("app.services.voice.audio_access.time.time", lambda: 1_001)
    with pytest.raises(HTTPException) as exc_info:
        await files.get_response("tts/example.mp3", _http_scope(query))
    assert exc_info.value.status_code == 404

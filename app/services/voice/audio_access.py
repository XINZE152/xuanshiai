"""短期签名的 AI 语音临时文件访问。

普通上传媒体仍由公共静态挂载提供；本地 TTS 音频只通过绑定用户
与隐私修订的短期 HMAC URL 读取。外部 provider URL 不在本模块信任边界内。
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import session_factory

AUDIO_URL_TTL_SECONDS = 300
_STORAGE_PREFIX = "/storage/uploads/"
_PRIVATE_PREFIXES = ("tts/", "voice/tts/")
_USER_QUERY_KEY = "user"
_REVISION_QUERY_KEY = "revision"


def _private_relative_path(audio_url: str) -> tuple[str, object] | None:
    """Return the private storage-relative path and parsed URL, if applicable."""
    try:
        parsed = urlsplit(audio_url)
    except ValueError:
        return None
    if parsed.scheme or parsed.netloc or not parsed.path.startswith(_STORAGE_PREFIX):
        return None
    relative = parsed.path[len(_STORAGE_PREFIX) :].lstrip("/")
    if not any(relative.startswith(prefix) for prefix in _PRIVATE_PREFIXES):
        return None
    return relative, parsed


def is_private_voice_path(relative_path: str) -> bool:
    """Whether a ``/storage/uploads`` relative path is AI voice temporary data."""
    normalized = relative_path.lstrip("/")
    return any(normalized.startswith(prefix) for prefix in _PRIVATE_PREFIXES)


class VoiceAudioUnavailable(Exception):
    """数据库无法确认用户状态，拒绝签发或下载。"""


class VoiceAudioDenied(Exception):
    """用户账号不再有效。"""


async def _current_revision(user_id: int, db: AsyncSession | None = None) -> int:
    if user_id <= 0:
        raise VoiceAudioDenied()
    if db is None and session_factory is None:
        raise VoiceAudioUnavailable()

    async def _read(session: AsyncSession) -> int:
        result = await session.execute(
            text(
                "SELECT u.status, COALESCE(r.privacy_revision, 0) AS privacy_revision "
                "FROM users u LEFT JOIN user_revision_state r ON r.user_id = u.id "
                "WHERE u.id = :user_id"
            ),
            {"user_id": user_id},
        )
        row = result.mappings().first()
        if row is None or int(row["status"]) != 1:
            raise VoiceAudioDenied()
        return int(row["privacy_revision"])

    try:
        if db is not None:
            return await _read(db)
        assert session_factory is not None
        async with session_factory() as session:
            return await _read(session)
    except VoiceAudioDenied:
        raise
    except Exception as exc:
        raise VoiceAudioUnavailable() from exc


def _signature(relative_path: str, expires: int, user_id: int, revision: int) -> str:
    message = f"{relative_path}\n{expires}\n{user_id}\n{revision}".encode("utf-8")
    return hmac.new(
        settings.secret_key.encode("utf-8"), message, hashlib.sha256
    ).hexdigest()


def sign_voice_audio_url(
    audio_url: str,
    *,
    user_id: int | None = None,
    privacy_revision: int | None = None,
    expires_seconds: int = AUDIO_URL_TTL_SECONDS,
) -> str:
    """Only sign local voice files for a known active owner and revision."""
    target = _private_relative_path(audio_url)
    if target is None:
        return audio_url
    if (
        user_id is None
        or privacy_revision is None
        or user_id <= 0
        or privacy_revision < 0
        or expires_seconds <= 0
    ):
        raise ValueError("invalid private voice URL signing context")
    relative, parsed = target
    expires = int(time.time()) + min(expires_seconds, AUDIO_URL_TTL_SECONDS)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key not in {"expires", "signature", _USER_QUERY_KEY, _REVISION_QUERY_KEY}
    ]
    query.extend(
        (
            ("expires", str(expires)),
            (_USER_QUERY_KEY, str(user_id)),
            (_REVISION_QUERY_KEY, str(privacy_revision)),
            ("signature", _signature(relative, expires, user_id, privacy_revision)),
        )
    )
    return urlunsplit(("", "", parsed.path, urlencode(query), parsed.fragment))


async def sign_voice_audio_for_user(
    audio_url: str, user_id: int, *, db: AsyncSession | None = None
) -> str:
    """Only query the database for local files; external URLs remain provider-owned."""
    if _private_relative_path(audio_url) is None:
        return audio_url
    revision = await _current_revision(user_id, db)
    return sign_voice_audio_url(audio_url, user_id=user_id, privacy_revision=revision)


def _single(query: Mapping[str, str | list[str] | None], key: str) -> str | None:
    raw = query.get(key)
    if isinstance(raw, list):
        return raw[0] if len(raw) == 1 else None
    return raw if isinstance(raw, str) else None


def _signed_identity(query: Mapping[str, str | list[str] | None]) -> tuple[int, int] | None:
    raw_user = _single(query, _USER_QUERY_KEY)
    raw_revision = _single(query, _REVISION_QUERY_KEY)
    if raw_user is None or raw_revision is None:
        return None
    if not raw_user.isascii() or not raw_revision.isascii():
        return None
    if not raw_user.isdecimal() or not raw_revision.isdecimal():
        return None
    user_id, revision = int(raw_user), int(raw_revision)
    if user_id <= 0 or str(user_id) != raw_user or str(revision) != raw_revision:
        return None
    return user_id, revision


def verify_voice_audio_signature(
    relative_path: str,
    query: Mapping[str, str | list[str] | None],
    *,
    now: int | None = None,
) -> bool:
    """Verify owner, revision, path and expiry without leaking failure detail."""
    normalized = relative_path.lstrip("/")
    if not is_private_voice_path(normalized):
        return True
    raw_expires = _single(query, "expires")
    raw_signature = _single(query, "signature")
    identity = _signed_identity(query)
    if raw_expires is None or raw_signature is None or identity is None:
        return False
    if not raw_expires.isascii() or not raw_expires.isdecimal():
        return False
    expires = int(raw_expires)
    current = int(time.time()) if now is None else now
    if str(expires) != raw_expires or expires <= current or expires > current + AUDIO_URL_TTL_SECONDS:
        return False
    expected = _signature(normalized, expires, *identity)
    return hmac.compare_digest(expected, raw_signature)


async def verify_voice_audio_access(
    relative_path: str, query: Mapping[str, str | list[str] | None]
) -> bool:
    """Fail closed on old signatures, revocation, cancellation or DB outage."""
    if not verify_voice_audio_signature(relative_path, query):
        return False
    if not is_private_voice_path(relative_path):
        return True
    identity = _signed_identity(query)
    if identity is None:
        return False
    user_id, revision = identity
    try:
        return await _current_revision(user_id) == revision
    except VoiceAudioDenied:
        return False


__all__ = [
    "AUDIO_URL_TTL_SECONDS",
    "VoiceAudioDenied",
    "VoiceAudioUnavailable",
    "is_private_voice_path",
    "sign_voice_audio_for_user",
    "sign_voice_audio_url",
    "verify_voice_audio_access",
    "verify_voice_audio_signature",
]

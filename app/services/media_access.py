"""类别化私有媒体访问：短期 HMAC 签名与挂载层验签注册表。

复用 TTS 语音短期签名机制（voice/audio_access.py）的语义与状态码口径：
私有类媒体直连旧 URL 或缺参/伪造/篡改/过期一律 403，查看者状态无法确认时
503 fail-closed（全链路无 401，与 main.py 挂载层 TTS 先例一致）。签名消息
纳入类别，防止跨类别重放；公开类媒体不受影响。外部 provider URL 不在本
模块信任边界内。深度归属校验仍由签发端既有 RBAC/会话校验负责，签名是
传输层止损而非替代。
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import session_factory
from app.services.voice.audio_access import (
    AUDIO_URL_TTL_SECONDS,
    VoiceAudioUnavailable,
    is_private_voice_path,
    verify_voice_audio_access,
)

logger = logging.getLogger(__name__)

MEDIA_URL_TTL_SECONDS = AUDIO_URL_TTL_SECONDS
_STORAGE_PREFIX = "/storage/uploads/"
_USER_QUERY_KEY = "user"
_REVISION_QUERY_KEY = "revision"
_KIND_QUERY_KEY = "kind"

MEDIA_CATEGORY_FOLLOW_UP = "follow_up"
MEDIA_CATEGORY_AUDIO = "audio"
MEDIA_CATEGORY_CERT = "cert"
MEDIA_CATEGORY_ADMIN = "admin"

# 查看者类型：user=登录用户（users 表语义），admin=后台账号
# （matchmaker_admin_account 表语义）。类型纳入签名消息防篡改。
VIEWER_KIND_USER = "user"
VIEWER_KIND_ADMIN = "admin"


class MediaAccessUnavailable(Exception):
    """数据库无法确认查看者状态，fail closed。"""


class MediaAccessDenied(Exception):
    """查看者账号不再有效。"""


def _match_follow_up_path(relative: str) -> bool:
    """跟进附件：``{member_id}/follow-up-img-*`` 与 ``{member_id}/follow-up-voice-*``。"""
    first, _, rest = relative.partition("/")
    if not (first.isascii() and first.isdigit()):
        return False
    return rest.startswith("follow-up-img-") or rest.startswith("follow-up-voice-")


def _match_audio_path(relative: str) -> bool:
    """用户语音：``{user_id}/audio/*``（media.upload_media 生成路径）。"""
    first, _, rest = relative.partition("/")
    if not (first.isascii() and first.isdigit()):
        return False
    return rest.startswith("audio/")


def _match_cert_path(relative: str) -> bool:
    """认证材料：与公开照片同目录，按文件名前缀区分
    ``{user_id}/education-cert-*``、``{user_id}/house-cert-*``、
    ``{user_id}/single-pledge-*``（certifications._store_certification_image 生成）。"""
    first, _, rest = relative.partition("/")
    if not (first.isascii() and first.isdigit()):
        return False
    return rest.startswith(("education-cert-", "house-cert-", "single-pledge-"))


def _match_admin_path(relative: str) -> bool:
    """后台资源：``admin/*``（matchmaker_staff_admin.common_upload 生成）。"""
    return relative.startswith("admin/")


@dataclass(frozen=True)
class MediaCategory:
    """一类私有媒体：路径匹配器 + 允许的查看者类型。

    ``default_kind`` 为签发端未显式指定类型时的缺省（cert 双语义无缺省，
    必须显式传 kind）。
    """

    name: str
    match: Callable[[str], bool]
    allowed_kinds: tuple[str, ...]
    default_kind: str | None


_MEDIA_CATEGORIES: tuple[MediaCategory, ...] = (
    MediaCategory(
        name=MEDIA_CATEGORY_FOLLOW_UP,
        match=_match_follow_up_path,
        allowed_kinds=(VIEWER_KIND_ADMIN,),
        default_kind=VIEWER_KIND_ADMIN,
    ),
    MediaCategory(
        name=MEDIA_CATEGORY_AUDIO,
        match=_match_audio_path,
        allowed_kinds=(VIEWER_KIND_USER,),
        default_kind=VIEWER_KIND_USER,
    ),
    MediaCategory(
        name=MEDIA_CATEGORY_CERT,
        match=_match_cert_path,
        allowed_kinds=(VIEWER_KIND_USER, VIEWER_KIND_ADMIN),
        default_kind=None,
    ),
    MediaCategory(
        name=MEDIA_CATEGORY_ADMIN,
        match=_match_admin_path,
        allowed_kinds=(VIEWER_KIND_ADMIN,),
        default_kind=VIEWER_KIND_ADMIN,
    ),
)


async def _admin_account_active(viewer_id: int, db: AsyncSession | None = None) -> bool:
    """跟进附件的查看者是签发管理员：后台账号必须仍处于启用状态。"""
    if viewer_id <= 0:
        raise MediaAccessDenied()
    if db is None and session_factory is None:
        raise MediaAccessUnavailable()

    async def _read(session: AsyncSession) -> bool:
        value = await session.scalar(
            text("SELECT status FROM matchmaker_admin_account WHERE id = :id"),
            {"id": viewer_id},
        )
        if value is None:
            raise MediaAccessDenied()
        return int(value) == 1

    try:
        if db is not None:
            return await _read(db)
        async with session_factory() as session:
            return await _read(session)
    except MediaAccessDenied:
        raise
    except Exception as exc:
        raise MediaAccessUnavailable() from exc


async def _user_active(viewer_id: int, db: AsyncSession | None = None) -> bool:
    """语音类查看者是登录用户：users.status 必须仍为 1（与
    audio_access._current_revision 同一状态查询模式，不含隐私修订联动）。"""
    if viewer_id <= 0:
        raise MediaAccessDenied()
    if db is None and session_factory is None:
        raise MediaAccessUnavailable()

    async def _read(session: AsyncSession) -> bool:
        value = await session.scalar(
            text("SELECT status FROM users WHERE id = :id"),
            {"id": viewer_id},
        )
        if value is None:
            raise MediaAccessDenied()
        return int(value) == 1

    try:
        if db is not None:
            return await _read(db)
        async with session_factory() as session:
            return await _read(session)
    except MediaAccessDenied:
        raise
    except Exception as exc:
        raise MediaAccessUnavailable() from exc


# 查看者状态钩子：签名通过后按 (类别, 查看者类型) 校验查看者当前是否仍有权读取。
_MEDIA_VIEWER_HOOKS: dict[tuple[str, str], Callable[[int, AsyncSession | None], Awaitable[bool]]] = {
    (MEDIA_CATEGORY_FOLLOW_UP, VIEWER_KIND_ADMIN): _admin_account_active,
    (MEDIA_CATEGORY_AUDIO, VIEWER_KIND_USER): _user_active,
    (MEDIA_CATEGORY_CERT, VIEWER_KIND_USER): _user_active,
    (MEDIA_CATEGORY_CERT, VIEWER_KIND_ADMIN): _admin_account_active,
    (MEDIA_CATEGORY_ADMIN, VIEWER_KIND_ADMIN): _admin_account_active,
}


def categorize_media_path(relative_path: str) -> str | None:
    """Return the private category owning a storage-relative path, else None."""
    category = _category_by_path(relative_path)
    return category.name if category else None


def _category_by_path(relative_path: str) -> MediaCategory | None:
    normalized = relative_path.lstrip("/")
    for category in _MEDIA_CATEGORIES:
        if category.match(normalized):
            return category
    return None


def _category_by_name(name: str) -> MediaCategory:
    for category in _MEDIA_CATEGORIES:
        if category.name == name:
            return category
    raise ValueError(f"unknown media category: {name}")


def _media_signature(
    category: str,
    kind: str,
    relative_path: str,
    expires: int,
    viewer: int,
    revision: int,
) -> str:
    message = f"{category}\n{kind}\n{relative_path}\n{expires}\n{viewer}\n{revision}".encode("utf-8")
    return hmac.new(
        settings.secret_key.encode("utf-8"), message, hashlib.sha256
    ).hexdigest()


def sign_media_url(
    media_url: str,
    *,
    category: str,
    viewer: int,
    kind: str | None = None,
    revision: int = 0,
    expires_seconds: int = MEDIA_URL_TTL_SECONDS,
) -> str:
    """Sign one local private media URL for a viewer; non-member URLs pass through.

    ``kind`` 为查看者类型（user/admin）；缺省取类别 default_kind，cert 类
    双语义必须显式指定。
    """
    try:
        parsed = urlsplit(media_url)
    except ValueError:
        return media_url
    if parsed.scheme or parsed.netloc or not parsed.path.startswith(_STORAGE_PREFIX):
        return media_url
    relative = parsed.path[len(_STORAGE_PREFIX) :].lstrip("/")
    category_def = _category_by_name(category)
    if categorize_media_path(relative) != category:
        return media_url
    resolved_kind = kind if kind is not None else category_def.default_kind
    if resolved_kind is None or resolved_kind not in category_def.allowed_kinds:
        raise ValueError(f"viewer kind {kind!r} is not allowed for category {category!r}")
    if viewer <= 0 or revision < 0 or expires_seconds <= 0:
        raise ValueError("invalid private media URL signing context")
    expires = int(time.time()) + min(expires_seconds, MEDIA_URL_TTL_SECONDS)
    # 重签前剥离旧签名参数（与 audio_access.sign_voice_audio_url 同一剥离逻辑）。
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key
        not in {
            "expires",
            "signature",
            _USER_QUERY_KEY,
            _REVISION_QUERY_KEY,
            _KIND_QUERY_KEY,
        }
    ]
    query.extend(
        (
            ("expires", str(expires)),
            (_KIND_QUERY_KEY, resolved_kind),
            (_USER_QUERY_KEY, str(viewer)),
            (_REVISION_QUERY_KEY, str(revision)),
            ("signature", _media_signature(category, resolved_kind, relative, expires, viewer, revision)),
        )
    )
    return urlunsplit(("", "", parsed.path, urlencode(query), parsed.fragment))


def strip_media_signature(media_url: str) -> str:
    """剥离签名 query 参数，返回入库用的原始 URL（幂等）。

    客户端提交的媒体 URL 可能是签名 URL（upload_media 响应）；提交边界统一
    剥离后入库，保证 DB 永远存原始 URL，读取端按当前查看者重签。
    """
    try:
        parsed = urlsplit(media_url)
    except ValueError:
        return media_url
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key
        not in {
            "expires",
            "signature",
            _USER_QUERY_KEY,
            _REVISION_QUERY_KEY,
            _KIND_QUERY_KEY,
        }
    ]
    return urlunsplit(("", "", parsed.path, urlencode(query), parsed.fragment))


def _single(query: Mapping[str, str | list[str] | None], key: str) -> str | None:
    raw = query.get(key)
    if isinstance(raw, list):
        return raw[0] if len(raw) == 1 else None
    return raw if isinstance(raw, str) else None


def _signed_media_identity(query: Mapping[str, str | list[str] | None]) -> tuple[int, int] | None:
    raw_viewer = _single(query, _USER_QUERY_KEY)
    raw_revision = _single(query, _REVISION_QUERY_KEY)
    if raw_viewer is None or raw_revision is None:
        return None
    if not raw_viewer.isascii() or not raw_revision.isascii():
        return None
    if not raw_viewer.isdecimal() or not raw_revision.isdecimal():
        return None
    viewer, revision = int(raw_viewer), int(raw_revision)
    if viewer <= 0 or revision < 0 or str(viewer) != raw_viewer or str(revision) != raw_revision:
        return None
    return viewer, revision


def verify_media_signature(
    relative_path: str,
    query: Mapping[str, str | list[str] | None],
    *,
    category: str,
    now: int | None = None,
) -> bool:
    """Verify viewer kind, viewer, path, category and expiry without leaking detail."""
    normalized = relative_path.lstrip("/")
    category_def = _category_by_name(category)
    raw_expires = _single(query, "expires")
    raw_signature = _single(query, "signature")
    raw_kind = _single(query, _KIND_QUERY_KEY)
    identity = _signed_media_identity(query)
    if raw_expires is None or raw_signature is None or identity is None:
        return False
    if raw_kind is None or raw_kind not in category_def.allowed_kinds:
        return False
    if not raw_expires.isascii() or not raw_expires.isdecimal():
        return False
    expires = int(raw_expires)
    current = int(time.time()) if now is None else now
    if str(expires) != raw_expires or expires <= current or expires > current + MEDIA_URL_TTL_SECONDS:
        return False
    viewer, revision = identity
    expected = _media_signature(category, raw_kind, normalized, expires, viewer, revision)
    return hmac.compare_digest(expected, raw_signature)


async def verify_media_access(
    relative_path: str, query: Mapping[str, str | list[str] | None]
) -> bool:
    """挂载层统一入口：TTS 类沿用既有语音签名链路，私有注册表类按类别验签，
    公开类直接放行；DB 不可用时抛 MediaAccessUnavailable（挂载层映射 503）。

    挂载层传入的 path 在 Windows 全栈下使用反斜杠分隔（生产 Linux 为正斜杠），
    匹配前必须归一化，否则私有前缀判定被绕过——这是 TTS 既有机制在本机全栈
    链路上的潜伏旁路，本模块在门面处统一归一化（TTS 模块本身零改动）。
    """
    normalized = relative_path.replace("\\", "/").lstrip("/")
    if is_private_voice_path(normalized):
        try:
            return await verify_voice_audio_access(normalized, query)
        except VoiceAudioUnavailable as exc:
            raise MediaAccessUnavailable() from exc
    category_def = _category_by_path(normalized)
    if category_def is None:
        return True
    raw_signature = _single(query, "signature")
    if raw_signature is None:
        # 未签名观测：私有类直连记录类别与相对路径（不记 query 不记内容），
        # 为 REVIEW §6 完整迁移提供证据。
        logger.warning(
            "unsigned_private_media_access category=%s path=%s",
            category_def.name,
            normalized,
        )
    if not verify_media_signature(normalized, query, category=category_def.name):
        return False
    identity = _signed_media_identity(query)
    raw_kind = _single(query, _KIND_QUERY_KEY)
    if identity is None or raw_kind is None:
        return False
    viewer, _revision = identity
    hook = _MEDIA_VIEWER_HOOKS.get((category_def.name, raw_kind))
    if hook is None:
        return False
    try:
        return await hook(viewer, None)
    except MediaAccessDenied:
        return False


__all__ = [
    "MEDIA_CATEGORY_ADMIN",
    "MEDIA_CATEGORY_AUDIO",
    "MEDIA_CATEGORY_CERT",
    "MEDIA_CATEGORY_FOLLOW_UP",
    "MEDIA_URL_TTL_SECONDS",
    "MediaAccessDenied",
    "MediaAccessUnavailable",
    "VIEWER_KIND_ADMIN",
    "VIEWER_KIND_USER",
    "categorize_media_path",
    "sign_media_url",
    "strip_media_signature",
    "verify_media_access",
    "verify_media_signature",
]

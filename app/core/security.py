"""Token and sensitive-value helpers used by authentication services."""

import hashlib
import hmac
import secrets
import base64
from datetime import UTC, datetime, timedelta

from jose import JWTError, jwt
from cryptography.fernet import Fernet
import bcrypt

from app.core.config import settings




def hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def create_token(
    user_id: int,
    session_id: int,
    token_type: str,
    expires_delta: timedelta,
    *,
    extra: dict[str, object] | None = None,
) -> str:
    now = datetime.now(UTC)
    payload: dict[str, object] = {
        "sub": str(user_id),
        "sid": str(session_id),
        "typ": token_type,
        "iat": now,
        "exp": now + expires_delta,
    }
    # A12-模拟登录：携带额外声明（如 imp=发起管理员 id）标记只读会话；
    # 普通登录不传 extra，行为与原来完全一致。
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, str]:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise ValueError("无效或已过期的访问令牌") from exc
    if payload.get("typ") != "access" or not payload.get("sub") or not payload.get("sid"):
        raise ValueError("无效的访问令牌")
    return payload


def impersonation_admin_id(token: str) -> int | None:
    """Return the admin id behind a read-only impersonation access token, else None.

    只读守卫专用：无效/过期令牌视为 None（交由各路由的鉴权依赖拒绝），
    普通 access token（无 imp 声明）也返回 None。
    """
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None
    if payload.get("typ") != "access":
        return None
    raw = payload.get("imp")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def create_live_ws_ticket(
    user_id: int,
    session_id: int,
    ticket_id: str,
    expires_seconds: int,
    user_session_id: int,
) -> str:
    """Create a short-lived, single-purpose JWT for a live WebSocket handshake.

    ``session_id`` is the *live session* (场次) identifier and is used to
    guarantee the ticket is only redeemable against the session it was issued
    for. ``user_session_id`` is the caller's *login session* (``user_session.id``)
    and is the only correct basis for the periodic keep-alive revocation check —
    conflating the two silently closes every connection. Keep them separate.
    """
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "sid": str(session_id),
        "uss": str(user_session_id),
        "jti": ticket_id,
        "typ": "live_ws_ticket",
        "iat": now,
        "exp": now + timedelta(seconds=expires_seconds),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_live_ws_ticket(token: str) -> dict[str, str]:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise ValueError("无效或已过期的直播 WebSocket 凭证") from exc
    if (
        payload.get("typ") != "live_ws_ticket"
        or not payload.get("sub")
        or not payload.get("sid")
        or not payload.get("uss")
        or not payload.get("jti")
    ):
        raise ValueError("无效的直播 WebSocket 凭证")
    return payload


def create_ai_ws_ticket(user_id: int, session_id: int, ticket_id: str, expires_seconds: int) -> str:
    """Create a short-lived ticket dedicated to AI voice WebSocket handshakes."""
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "sid": str(session_id),
        "jti": ticket_id,
        "typ": "ai_ws_ticket",
        "iat": now,
        "exp": now + timedelta(seconds=expires_seconds),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_ai_ws_ticket(token: str) -> dict[str, str]:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise ValueError("无效或已过期的 AI WebSocket 凭证") from exc
    if (
        payload.get("typ") != "ai_ws_ticket"
        or not payload.get("sub")
        or not payload.get("sid")
        or not payload.get("jti")
        or not isinstance(payload.get("iat"), int)
        or not isinstance(payload.get("exp"), int)
    ):
        raise ValueError("无效的 AI WebSocket 凭证")
    return payload


def random_token() -> str:
    return secrets.token_urlsafe(48)


def hash_passwordless_code(code: str) -> str:
    """Hash SMS codes before persistence; the raw code never enters the database."""
    return hash_token(code)


def hash_password(password: str) -> str:
    """Hash an account password; plaintext passwords must never be persisted."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against a bcrypt hash without exposing comparison details."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError, UnicodeError):
        return False


def lead_convert_password_fields() -> dict[str, str | None]:
    """Initial password columns for member accounts created by 一键入库.

    配置为空时返回 NULL 列值，新账号只有验证码/微信登录方式。
    """
    password = settings.lead_convert_initial_password.strip()
    if not password:
        return {"password": None, "password_algo": None}
    return {"password": hash_password(password), "password_algo": "bcrypt"}


def encrypt_sensitive(value: str) -> str:
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.secret_key.encode()).digest())
    return Fernet(key).encrypt(value.encode()).decode()


def decrypt_sensitive(value: str) -> str:
    """Decrypt a value written by :func:`encrypt_sensitive`; never log the result."""
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.secret_key.encode()).digest())
    return Fernet(key).decrypt(value.encode()).decode()


def mask_id_card(value: str) -> str:
    return f"{value[:4]}{'*' * max(0, len(value) - 8)}{value[-4:]}"


def payment_callback_signature(order_no: str, transaction_id: str, amount: float | None, secret: str) -> str:
    """Build the HMAC-SHA256 signature for a payment callback payload (D-21).

    The canonical payload is ``order_no|transaction_id|amount`` where amount uses
    two decimals (empty string when absent).  WeChat Pay v3 signs the raw
    notification body; this helper is the local stand-in so both ``mock`` and
    ``real`` modes share one verification pipeline and only the key source
    differs.
    """
    amount_part = "" if amount is None else f"{float(amount):.2f}"
    payload = f"{order_no}|{transaction_id}|{amount_part}"
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()

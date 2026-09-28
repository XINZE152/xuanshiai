"""Single-use AI voice WebSocket ticket verification."""

from sqlalchemy import text

from app.core.redis import consume_once, redis_client
from app.core.security import decode_ai_ws_ticket
from app.db.session import session_factory

AI_WS_TICKET_TTL_SECONDS = 60


class VoiceTicketDenied(Exception):
    """Invalid, used or inactive ticket/session (WebSocket close 1008)."""


class VoiceTicketUnavailable(Exception):
    """Redis or database unavailable (WebSocket close 1011)."""


async def authenticate_voice_ticket(ticket: str) -> int:
    """Consume the ticket before checking the active login and account."""
    try:
        payload = decode_ai_ws_ticket(ticket)
        user_id = int(payload["sub"])
        session_id = int(payload["sid"])
        ticket_id = payload["jti"]
        if not ticket or len(ticket) > 4096:
            raise ValueError("invalid ticket length")
        if user_id <= 0 or session_id <= 0 or not isinstance(ticket_id, str) or not ticket_id:
            raise ValueError("invalid ticket claims")
    except (ValueError, KeyError, TypeError) as exc:
        raise VoiceTicketDenied() from exc

    try:
        # consume_once has a development-only local fallback: never accept it for auth.
        ticket_key = f"ai:ws-ticket:{ticket_id}"
        await redis_client.ping()
        consumed = await consume_once(ticket_key, AI_WS_TICKET_TTL_SECONDS)
        if consumed and not await redis_client.get(ticket_key):
            raise RuntimeError("single-use ticket was not persisted in Redis")
        await redis_client.ping()
    except Exception as exc:
        raise VoiceTicketUnavailable() from exc
    if not consumed:
        raise VoiceTicketDenied()

    if session_factory is None:
        raise VoiceTicketUnavailable()
    try:
        async with session_factory() as db:
            result = await db.execute(
                text(
                    "SELECT 1 FROM users u JOIN user_session s ON s.user_id = u.id "
                    "WHERE u.id = :user_id AND u.status = 1 AND s.id = :session_id "
                    "AND s.status = 1 AND s.revoked_at IS NULL "
                    "AND s.access_expire_at > UTC_TIMESTAMP()"
                ),
                {"user_id": user_id, "session_id": session_id},
            )
            active = bool(result.scalar())
    except Exception as exc:
        raise VoiceTicketUnavailable() from exc
    if not active:
        raise VoiceTicketDenied()
    return user_id

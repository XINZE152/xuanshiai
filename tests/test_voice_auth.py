from __future__ import annotations

from typing import Any

import pytest

from app.core.security import create_ai_ws_ticket
from app.services.voice import auth as voice_auth
from app.services.voice.auth import (
    AI_WS_TICKET_TTL_SECONDS,
    VoiceTicketDenied,
    VoiceTicketUnavailable,
    authenticate_voice_ticket,
)


class _FakeRedis:
    def __init__(self, *, value: str | None = "1", ping_error: Exception | None = None) -> None:
        self.value = value
        self.ping_error = ping_error
        self.ping_count = 0
        self.get_keys: list[str] = []

    async def ping(self) -> bool:
        self.ping_count += 1
        if self.ping_error is not None:
            raise self.ping_error
        return True

    async def get(self, key: str) -> str | None:
        self.get_keys.append(key)
        return self.value


class _FakeResult:
    def __init__(self, value: Any) -> None:
        self.value = value

    def scalar(self) -> Any:
        return self.value


class _FakeDb:
    def __init__(self, *, active: Any) -> None:
        self.active = active
        self.statements: list[str] = []
        self.parameters: list[dict[str, Any]] = []

    async def __aenter__(self) -> "_FakeDb":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def execute(self, statement: Any, params: dict[str, Any]) -> _FakeResult:
        self.statements.append(str(statement))
        self.parameters.append(params)
        return _FakeResult(self.active)


def _ticket(*, ticket_id: str = "ticket-auth-test", expires_seconds: int = 60) -> str:
    return create_ai_ws_ticket(
        user_id=42,
        session_id=7,
        ticket_id=ticket_id,
        expires_seconds=expires_seconds,
    )


def _patch_backend(
    monkeypatch: pytest.MonkeyPatch,
    *,
    active: Any = 1,
    consumed: tuple[bool, ...] = (True,),
    redis: _FakeRedis | None = None,
) -> tuple[_FakeRedis, _FakeDb, list[tuple[str, int]]]:
    fake_redis = redis or _FakeRedis()
    fake_db = _FakeDb(active=active)
    consume_calls: list[tuple[str, int]] = []
    remaining = iter(consumed)

    async def _consume_once(key: str, ttl_seconds: int) -> bool:
        consume_calls.append((key, ttl_seconds))
        return next(remaining, False)

    monkeypatch.setattr(voice_auth, "redis_client", fake_redis)
    monkeypatch.setattr(voice_auth, "consume_once", _consume_once)
    monkeypatch.setattr(voice_auth, "session_factory", lambda: fake_db)
    return fake_redis, fake_db, consume_calls


@pytest.mark.asyncio
async def test_expired_real_ticket_is_denied_before_redis() -> None:
    with pytest.raises(VoiceTicketDenied):
        await authenticate_voice_ticket(_ticket(expires_seconds=-60))


@pytest.mark.asyncio
async def test_real_ticket_is_single_use(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_redis, fake_db, consume_calls = _patch_backend(
        monkeypatch,
        consumed=(True, False),
    )
    ticket = _ticket(ticket_id="single-use-ticket")
    assert await authenticate_voice_ticket(ticket) == 42
    with pytest.raises(VoiceTicketDenied):
        await authenticate_voice_ticket(ticket)

    assert fake_db.parameters[0] == {"user_id": 42, "session_id": 7}
    assert consume_calls == [
        ("ai:ws-ticket:single-use-ticket", AI_WS_TICKET_TTL_SECONDS),
        ("ai:ws-ticket:single-use-ticket", AI_WS_TICKET_TTL_SECONDS),
    ]
    assert fake_redis.ping_count == 4


@pytest.mark.asyncio
async def test_revoked_session_is_denied_before_accept(monkeypatch: pytest.MonkeyPatch) -> None:
    _, fake_db, consume_calls = _patch_backend(monkeypatch, active=0)

    with pytest.raises(VoiceTicketDenied):
        await authenticate_voice_ticket(_ticket(ticket_id="revoked-session-ticket"))

    assert consume_calls
    assert "s.status = 1" in fake_db.statements[0]
    assert "s.revoked_at IS NULL" in fake_db.statements[0]
    assert "s.access_expire_at > UTC_TIMESTAMP()" in fake_db.statements[0]


@pytest.mark.asyncio
async def test_blocked_account_is_denied_before_accept(monkeypatch: pytest.MonkeyPatch) -> None:
    _, fake_db, _ = _patch_backend(monkeypatch, active=0)

    with pytest.raises(VoiceTicketDenied):
        await authenticate_voice_ticket(_ticket(ticket_id="blocked-account-ticket"))

    assert "u.status = 1" in fake_db.statements[0]


@pytest.mark.asyncio
async def test_redis_unavailable_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    unavailable = _FakeRedis(ping_error=ConnectionError("redis unavailable"))
    _, _, consume_calls = _patch_backend(monkeypatch, redis=unavailable)

    with pytest.raises(VoiceTicketUnavailable):
        await authenticate_voice_ticket(_ticket(ticket_id="redis-down-ticket"))

    assert consume_calls == []
    assert unavailable.ping_count == 1

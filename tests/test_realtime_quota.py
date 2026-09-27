"""实时语音 v2 的额度与单会话守卫。

不连 Redis / MySQL：额度读写注入 fake redis，时钟由测试推进，
会话用可控起点代替真实上游。覆盖：

- 结算按本轮会话时长向上取整，没有进行中的会话不扣额度
- 看门狗按本轮时长判断单次上限，不按连接寿命
- 日额度 incrby 与 expire 同一次 Redis 调用
- 单会话名额的占用、空闲接管、释放，以及启动失败时路由会归还名额
- 实时门禁六种开关组合
"""

from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any

import pytest

from app.api.routes import voice_moxiang
from app.core import config
from app.services.voice.realtime import moxiang_bridge as bridge_mod
from app.services.voice.realtime.moxiang_bridge import (
    MoxiangRealtimeBridge,
    RealtimeRouteContext,
    _ACTIVE_REALTIME_SESSIONS,
    _CONSUME_MINUTES_LUA,
    _DAILY_QUOTA_TTL_SECONDS,
    acquire_session_slot,
    billable_minutes,
    consume_realtime_minutes,
    realtime_daily_minutes_used,
    realtime_gate_error,
    realtime_watchdog,
    release_session_slot,
)
from app.services.voice.realtime.session import RealtimeVoiceSession


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.expires: dict[str, int] = {}
        self.eval_calls: list[tuple[Any, ...]] = []
        self.incrby_calls = 0
        self.expire_calls = 0
        self.fail = False

    async def get(self, key: str) -> str | None:
        if self.fail:
            raise RuntimeError("redis down")
        value = self.values.get(key)
        return None if value is None else str(value)

    async def eval(self, script: str, numkeys: int, *args: Any) -> int:
        if self.fail:
            raise RuntimeError("redis down")
        self.eval_calls.append((script, numkeys, *args))
        key, amount, ttl = args
        self.values[key] = self.values.get(key, 0) + int(amount)
        self.expires[key] = int(ttl)
        return self.values[key]

    async def incrby(self, key: str, amount: int) -> int:
        self.incrby_calls += 1
        raise AssertionError("额度累计必须走 eval，不能再分两步 incrby")

    async def expire(self, key: str, ttl: int) -> None:
        self.expire_calls += 1
        raise AssertionError("额度累计必须走 eval，不能再分两步 expire")


class FakeClock:
    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeSession:
    """只提供看门狗和结算读取的两个属性。"""

    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self.started_at = clock.now
        self.last_activity = clock.now
        self.closed = False

    @property
    def session_elapsed_seconds(self) -> float:
        return self._clock.now - self.started_at

    @property
    def idle_seconds(self) -> float:
        return self._clock.now - self.last_activity

    async def aclose(self) -> None:
        self.closed = True


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.close_code: int | None = None

    async def send_text(self, payload: str) -> None:
        import json

        self.sent.append(json.loads(payload))

    async def close(self, code: int = 1000) -> None:
        self.close_code = code


def _context() -> RealtimeRouteContext:
    return RealtimeRouteContext(
        session_id="sess-1", subject="personal", narrative_context=""
    )


def _bridge(clock: FakeClock) -> MoxiangRealtimeBridge:
    return MoxiangRealtimeBridge(
        ws=FakeWebSocket(),
        user_id=7,
        context=_context(),
        poll_tasks=set(),
        emit=_noop_emit,
        submit_candidate=_noop_submit,
    )


async def _noop_emit(event: dict[str, Any]) -> None:
    return None


async def _noop_submit(text: str, client_turn_id: str) -> str:
    return "turn-1"


@pytest.fixture(autouse=True)
def _isolated_slots() -> Any:
    _ACTIVE_REALTIME_SESSIONS.clear()
    yield
    _ACTIVE_REALTIME_SESSIONS.clear()


@pytest.fixture
def redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    fake = FakeRedis()
    monkeypatch.setattr("app.core.redis.redis_client", fake)
    return fake


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(
        "app.services.voice.realtime.session.time.monotonic", fake.monotonic
    )
    return fake


def test_billable_minutes_rounds_up() -> None:
    assert billable_minutes(0) == 0
    assert billable_minutes(-3) == 0
    assert billable_minutes(1) == 1
    assert billable_minutes(59) == 1
    assert billable_minutes(60) == 1
    assert billable_minutes(61) == 2
    assert billable_minutes(4 * 60 + 1) == 5


@pytest.mark.asyncio
async def test_close_without_session_does_not_consume(
    redis: FakeRedis, clock: FakeClock
) -> None:
    bridge = _bridge(clock)
    clock.advance(11 * 60)
    await bridge.close_session()
    assert redis.eval_calls == []


@pytest.mark.asyncio
async def test_close_bills_session_duration_not_connection_age(
    redis: FakeRedis, clock: FakeClock
) -> None:
    bridge = _bridge(clock)
    clock.advance(30 * 60)
    session = FakeSession(clock)
    bridge.session = session  # type: ignore[assignment]
    clock.advance(4 * 60 + 1)
    await bridge.close_session()
    assert session.closed is True
    assert len(redis.eval_calls) == 1
    script, numkeys, key, minutes, ttl = redis.eval_calls[0]
    assert script == _CONSUME_MINUTES_LUA
    assert numkeys == 1
    assert ":7:" in key
    assert minutes == 5
    assert ttl == _DAILY_QUOTA_TTL_SECONDS
    assert redis.incrby_calls == 0
    assert redis.expire_calls == 0


@pytest.mark.asyncio
async def test_consume_is_best_effort_when_redis_fails(
    redis: FakeRedis,
) -> None:
    redis.fail = True
    await consume_realtime_minutes(7, 3)
    assert redis.values == {}


@pytest.mark.asyncio
async def test_daily_read_fails_closed(redis: FakeRedis) -> None:
    from app.services.voice.realtime.moxiang_bridge import _daily_key

    assert await realtime_daily_minutes_used(7) == 0
    redis.values[_daily_key(7)] = 4
    assert await realtime_daily_minutes_used(7) == 4
    redis.fail = True
    assert await realtime_daily_minutes_used(7) is None


@pytest.mark.asyncio
async def test_two_sessions_on_one_connection_bill_separately(
    redis: FakeRedis, clock: FakeClock
) -> None:
    bridge = _bridge(clock)
    first = FakeSession(clock)
    bridge.session = first  # type: ignore[assignment]
    clock.advance(10 * 60)
    await bridge.close_session()
    # 连接继续活着，第二条会话有自己的起点。
    clock.advance(15 * 60)
    second = FakeSession(clock)
    bridge.session = second  # type: ignore[assignment]
    clock.advance(10 * 60)
    await bridge.close_session()
    billed = [call[3] for call in redis.eval_calls]
    assert billed == [10, 10]
    assert sum(redis.values.values()) == 20


@pytest.mark.asyncio
async def test_watchdog_stops_at_session_limit(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    monkeypatch.setattr(bridge_mod.asyncio, "sleep", _instant_sleep)
    monkeypatch.setattr(
        config.settings, "ai_realtime_session_max_minutes", 10, raising=False
    )
    bridge = _bridge(clock)
    ws = FakeWebSocket()
    session = FakeSession(clock)
    session.last_activity = clock.now + 10**6
    bridge.session = session  # type: ignore[assignment]
    clock.advance(10 * 60)
    await realtime_watchdog(bridge, ws)
    assert ws.close_code == 1000
    assert ws.sent[-1]["code"] == "REALTIME_SESSION_LIMIT"


@pytest.mark.asyncio
async def test_watchdog_ignores_connection_age(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    sleeps = {"count": 0}

    async def limited_sleep(_seconds: float) -> None:
        sleeps["count"] += 1
        if sleeps["count"] >= 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(bridge_mod.asyncio, "sleep", limited_sleep)
    bridge = _bridge(clock)
    clock.advance(40 * 60)
    session = FakeSession(clock)
    session.last_activity = clock.now + 10**6
    bridge.session = session  # type: ignore[assignment]
    ws = FakeWebSocket()
    await realtime_watchdog(bridge, ws)
    assert ws.close_code is None
    assert ws.sent == []


@pytest.mark.asyncio
async def test_watchdog_idle_exit(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    monkeypatch.setattr(bridge_mod.asyncio, "sleep", _instant_sleep)
    monkeypatch.setattr(
        config.settings, "ai_realtime_idle_exit_seconds", 60, raising=False
    )
    bridge = _bridge(clock)
    session = FakeSession(clock)
    bridge.session = session  # type: ignore[assignment]
    clock.advance(60)
    ws = FakeWebSocket()
    await realtime_watchdog(bridge, ws)
    assert ws.close_code == 1000
    assert ws.sent[-1]["code"] == "REALTIME_SESSION_LIMIT"


@pytest.mark.asyncio
async def test_watchdog_skips_when_no_session(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    sleeps = {"count": 0}

    async def limited_sleep(_seconds: float) -> None:
        sleeps["count"] += 1
        clock.advance(30 * 60)
        if sleeps["count"] >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(bridge_mod.asyncio, "sleep", limited_sleep)
    bridge = _bridge(clock)
    ws = FakeWebSocket()
    await realtime_watchdog(bridge, ws)
    assert ws.close_code is None


async def _instant_sleep(_seconds: float) -> None:
    return None


def test_session_slot_acquire_release_and_idle_takeover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        config.settings, "ai_realtime_idle_exit_seconds", 60, raising=False
    )
    active = RealtimeVoiceSession.__new__(RealtimeVoiceSession)
    active._last_activity_ts = time.monotonic()
    assert acquire_session_slot(7) is True
    _ACTIVE_REALTIME_SESSIONS[7] = active
    assert acquire_session_slot(7) is False
    active._last_activity_ts = time.monotonic() - 61
    assert acquire_session_slot(7) is True
    release_session_slot(7)
    assert 7 not in _ACTIVE_REALTIME_SESSIONS
    release_session_slot(7)
    assert acquire_session_slot(7) is True


def test_failed_start_releases_slot_in_route() -> None:
    source = inspect.getsource(voice_moxiang.moxiang_master_conversation)
    start = source.index("started = await realtime_bridge.start_session()")
    window = source[start : start + 240]
    assert "if started is None:" in window
    assert "release_session_slot(user_id)" in window


def test_realtime_gate_error_matrix(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = config.settings
    monkeypatch.setattr(settings, "ai_realtime_voice_enabled", False, raising=False)
    assert realtime_gate_error() == "AI_FEATURE_DISABLED"

    monkeypatch.setattr(settings, "ai_realtime_voice_enabled", True, raising=False)
    monkeypatch.setattr(settings, "ai_realtime_voice_provider", "", raising=False)
    assert realtime_gate_error() == "AI_FEATURE_DISABLED"

    monkeypatch.setattr(
        settings, "ai_realtime_voice_provider", "other", raising=False
    )
    assert realtime_gate_error() == "AI_FEATURE_DISABLED"

    monkeypatch.setattr(
        settings, "ai_realtime_voice_provider", "senseaudio", raising=False
    )
    monkeypatch.setattr(settings, "ai_senseaudio_api_key", None, raising=False)
    assert realtime_gate_error() == "AI_FEATURE_DISABLED"

    monkeypatch.setattr(
        settings, "ai_senseaudio_api_key", _Secret("sk"), raising=False
    )
    assert realtime_gate_error() is None


class _Secret:
    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value

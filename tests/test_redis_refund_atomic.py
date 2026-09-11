"""Regression tests for atomic daily quota refunds."""

from __future__ import annotations

import pytest
from redis.exceptions import ConnectionError

from app.core import redis as redis_module


class RecordingRedis:
    def __init__(self, result: int = 1) -> None:
        self.result = result
        self.eval_calls: list[tuple[object, ...]] = []

    async def eval(self, *args: object) -> int:
        self.eval_calls.append(args)
        return self.result

    async def get(self, *_args: object) -> object:
        raise AssertionError("refund must not perform a separate GET")

    async def decr(self, *_args: object) -> object:
        raise AssertionError("refund must not perform a separate DECR")


class BrokenRedis:
    async def eval(self, *_args: object) -> int:
        raise ConnectionError("redis offline")


@pytest.mark.asyncio
async def test_refund_daily_uses_one_atomic_eval(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = RecordingRedis(result=1)
    monkeypatch.setattr(redis_module, "redis_client", fake)

    await redis_module.refund_daily("ai:assistant:7:2026-09-09")

    assert len(fake.eval_calls) == 1
    script, key_count, key = fake.eval_calls[0]
    assert script == redis_module.REFUND_DAILY_LUA
    assert key_count == 1
    assert key == "ai:assistant:7:2026-09-09"
    assert "GET" in str(script)
    assert "DECR" in str(script)


@pytest.mark.asyncio
async def test_refund_daily_keeps_local_fallback_when_redis_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(redis_module, "redis_client", BrokenRedis())
    monkeypatch.setattr(redis_module.settings, "environment", "development")

    key = "ai:assistant:local-fallback"
    redis_module._local_quota_counts[key] = 1
    try:
        await redis_module.refund_daily(key)
        assert redis_module._local_quota_counts[key] == 0
    finally:
        redis_module._local_quota_counts.pop(key, None)


@pytest.mark.asyncio
async def test_refund_daily_treats_atomic_noop_as_successful_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = RecordingRedis(result=0)
    monkeypatch.setattr(redis_module, "redis_client", fake)

    result = await redis_module.refund_daily("ai:assistant:7:empty")

    assert result is None
    assert len(fake.eval_calls) == 1

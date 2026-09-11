"""Real Redis integration coverage for atomic daily-quota refunds."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from redis.asyncio import Redis

from app.core.redis import refund_daily


def _key() -> str:
    return f"ai:redis-refund-integration:{uuid.uuid4().hex}"


@pytest.mark.asyncio
async def test_refund_concurrent_attempts_never_drive_value_below_zero(
    real_redis: Redis,
) -> None:
    key = _key()
    try:
        await real_redis.set(key, 1, ex=120)
        await asyncio.gather(*(refund_daily(key) for _ in range(32)))

        value = int(await real_redis.get(key) or 0)
        assert value >= 0
        assert value == 0
    finally:
        await real_redis.delete(key)


@pytest.mark.asyncio
async def test_refund_missing_or_zero_key_is_noop(real_redis: Redis) -> None:
    missing_key = _key()
    zero_key = _key()
    try:
        await real_redis.set(zero_key, 0, ex=120)

        await refund_daily(missing_key)
        await refund_daily(zero_key)

        assert await real_redis.exists(missing_key) == 0
        assert await real_redis.get(zero_key) == "0"
    finally:
        await real_redis.delete(missing_key, zero_key)


@pytest.mark.asyncio
async def test_refund_preserves_existing_ttl(real_redis: Redis) -> None:
    key = _key()
    try:
        await real_redis.set(key, 2, ex=120)
        ttl_before = await real_redis.ttl(key)

        await refund_daily(key)

        ttl_after = await real_redis.ttl(key)
        assert ttl_before > 0
        assert ttl_after > 0
        # DECR must not reset the expiry; elapsed time may reduce it slightly.
        assert ttl_after <= ttl_before
        assert await real_redis.get(key) == "1"
    finally:
        await real_redis.delete(key)

import asyncio

import pytest

from app.services.voice import stream_provider as mod


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.calls = 0

    async def get(self, key):
        self.calls += 1
        return self.values.get(key)

    async def set(self, key, value, ex=None):
        self.values[key] = value
        return True


@pytest.mark.asyncio
async def test_cache_key_fingerprints_secret_and_isolated_by_identity_region():
    key = mod._nls_token_cache_key("ak-one", "super-secret", "cn-shanghai")
    assert "super-secret" not in key
    assert "ak-one" in key
    assert key != mod._nls_token_cache_key("ak-one", "rotated", "cn-shanghai")
    assert key != mod._nls_token_cache_key("ak-one", "super-secret", "cn-hangzhou")


@pytest.mark.asyncio
async def test_token_cache_reuses_redis_and_refreshes_after_rotation(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(mod, "redis_client", fake)
    calls = 0

    async def fetch(**kwargs):
        nonlocal calls
        calls += 1
        return (f"token-{calls}", 3600)

    monkeypatch.setattr(mod, "_fetch_nls_token", fetch)
    first = await mod._get_nls_token_cached(
        access_key_id="ak-one", access_key_secret="secret-a", region="cn-shanghai"
    )
    second = await mod._get_nls_token_cached(
        access_key_id="ak-one", access_key_secret="secret-a", region="cn-shanghai"
    )
    rotated = await mod._get_nls_token_cached(
        access_key_id="ak-one", access_key_secret="secret-b", region="cn-shanghai"
    )
    assert first[0] == second[0] == "token-1"
    assert rotated[0] == "token-2"
    assert calls == 2
    assert all("secret-a" not in key and "secret-b" not in key for key in fake.values)


@pytest.mark.asyncio
async def test_concurrent_refresh_single_flight(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(mod, "redis_client", fake)
    calls = 0

    async def fetch(**kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return ("shared-token", 3600)

    monkeypatch.setattr(mod, "_fetch_nls_token", fetch)
    result = await asyncio.gather(
        *[
            mod._get_nls_token_cached(
                access_key_id="ak", access_key_secret="secret", region="cn-shanghai"
            )
            for _ in range(8)
        ]
    )
    assert calls == 1
    assert {item[0] for item in result} == {"shared-token"}


def test_cache_can_be_used_from_multiple_event_loops(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(mod, "redis_client", fake)
    calls = 0

    async def fetch(**kwargs):
        nonlocal calls
        calls += 1
        return (f"token-{calls}", 3600)

    monkeypatch.setattr(mod, "_fetch_nls_token", fetch)

    async def run():
        return await mod._get_nls_token_cached(
            access_key_id="ak", access_key_secret="secret", region="cn-shanghai"
        )

    assert asyncio.run(run())[0] == "token-1"
    assert asyncio.run(run())[0] == "token-1"


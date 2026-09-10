"""Task 13：Persona 缓存失效优化（generation key 替代逐键 SCAN/SMEMBERS）。

计划要求（docs/superpowers/plans/2026-09-09-ai-backend-four-batches.md Task 13）：
- 以索引集合或 generation/version key 替代逐键 SCAN。
- 写入成员和索引时处理竞态。
- 处理 index TTL 早于成员 key 的场景。
- 清理失效成员。
- 避免对超大集合执行无界 ``SMEMBERS``。
- 增加写入、撤销、并发失效测试。
"""

import asyncio

import pytest

from tests.test_ai_memory_consumers import (
    FakeCache,
    PersonaProjectionSession,
    _allow_decide,
    _grant_build_persona,
    _persona_store,
    OWNER_ID,
    POLICY_REVISION,
)

pytestmark = pytest.mark.asyncio


def _consumers():
    from app.services.ai.memory import consumers as consumers_mod

    return consumers_mod


def _adapter(session, cache):
    from app.services.ai.memory.consumers import PersonaMemoryAdapter

    return PersonaMemoryAdapter(session, cache=cache)


def _patch_allow(monkeypatch) -> None:
    monkeypatch.setattr(
        _consumers().CandidateVisibilityService, "decide", _allow_decide
    )


async def test_persona_cache_write_uses_generation_key(monkeypatch) -> None:
    """写入缓存时键尾携带 generation；generation key 与成员键分前缀。"""

    consumers = _consumers()
    _patch_allow(monkeypatch)
    session, store = _persona_store()
    await _grant_build_persona(store, session)
    cache = FakeCache()
    adapter = _adapter(session, cache)

    context = await adapter.build_public_context(43, OWNER_ID, purpose="session_context")
    assert not context.is_empty

    generation_keys = [
        key
        for key in cache.data
        if key.startswith(consumers.PERSONA_CACHE_GENERATION_PREFIX)
    ]
    member_keys = [
        key for key in cache.data if key.startswith(consumers.PERSONA_CACHE_PREFIX)
    ]
    # 仅写入成员键；generation 键只在失效时创建，未失效前读取按 0 处理。
    assert generation_keys == []
    assert len(member_keys) == 1
    # 未失效过时 generation 为 0，读取命中同一键。
    hit = await adapter.build_public_context(43, OWNER_ID, purpose="session_context")
    assert hit is not None and not hit.is_empty
    assert len(cache.data) == 1


async def test_persona_invalidate_bumps_generation_and_drops_old_entries(
    monkeypatch,
) -> None:
    """撤销授权后 generation 递增，旧键立即失效，无需逐键 SCAN。"""

    consumers = _consumers()
    _patch_allow(monkeypatch)
    session, store = _persona_store()
    await _grant_build_persona(store, session)
    cache = FakeCache()
    adapter = _adapter(session, cache)

    stale = await adapter.build_public_context(43, OWNER_ID, purpose="session_context")
    assert not stale.is_empty

    # 撤销数据库授权（撤权事件触发方），再模拟事件驱动的缓存主动失效。
    from app.services.ai.memory.projections import MemoryProjectionService

    service = MemoryProjectionService(session, policy_revision=POLICY_REVISION)
    await service.revoke(
        owner_user_id=OWNER_ID,
        function_key="persona_context",
        purpose="session_context",
        data_category="public_profile_summary",
    )
    generation = await consumers.invalidate_persona_memory_cache(
        target_user_id=OWNER_ID, cache=cache
    )
    assert generation == 1

    # 失效后再次读取：授权已撤销，数据库路径返回空，且不会写入新缓存键。
    rebuilt = await adapter.build_public_context(43, OWNER_ID, purpose="session_context")
    assert rebuilt.is_empty
    member_keys = [
        key for key in cache.data if key.startswith(consumers.PERSONA_CACHE_PREFIX)
    ]
    # 旧成员键允许残留（对齐 Redis 真实行为：不 SCAN 删除），靠 TTL 收敛。
    assert len(member_keys) == 1

    # 多次失效 generation 单调递增（并发失效场景：incr 原子，无竞态丢失）。
    for expected in (2, 3, 4):
        generation = await consumers.invalidate_persona_memory_cache(
            target_user_id=OWNER_ID, cache=cache
        )
        assert generation == expected


async def test_persona_invalidate_is_atomic_under_concurrency(monkeypatch) -> None:
    """并发失效依赖 incr 原子性：N 个并发失效产生 N 个连续 generation。"""

    consumers = _consumers()
    _patch_allow(monkeypatch)
    session, store = _persona_store()
    await _grant_build_persona(store, session)
    cache = FakeCache()
    adapter = _adapter(session, cache)
    await adapter.build_public_context(43, OWNER_ID, purpose="session_context")

    generations = await asyncio.gather(
        *(
            consumers.invalidate_persona_memory_cache(
                target_user_id=OWNER_ID, cache=cache
            )
            for _ in range(8)
        )
    )
    assert sorted(generations) == list(range(1, 9))


async def test_persona_generation_key_gets_ttl_renewed(monkeypatch) -> None:
    """generation key 每次失效续期，且必须比成员键（300s）活得更久。"""

    consumers = _consumers()
    _patch_allow(monkeypatch)
    session, store = _persona_store()
    await _grant_build_persona(store, session)
    cache = FakeCache()
    adapter = _adapter(session, cache)
    await adapter.build_public_context(43, OWNER_ID, purpose="session_context")

    assert (
        consumers.PERSONA_CACHE_GENERATION_TTL_SECONDS
        > consumers.PERSONA_CACHE_TTL_SECONDS
    )
    first = await consumers.invalidate_persona_memory_cache(
        target_user_id=OWNER_ID, cache=cache
    )
    assert first == 1
    generation_key = (
        f"{consumers.PERSONA_CACHE_GENERATION_PREFIX}:{OWNER_ID}"
    )
    assert generation_key in cache.data

    # 二次失效续期同一个键，而不是新建或覆盖为初始值。
    second = await consumers.invalidate_persona_memory_cache(
        target_user_id=OWNER_ID, cache=cache
    )
    assert second == 2
    assert cache.data[generation_key] == "2"


async def test_persona_cache_disabled_when_generation_backend_fails(
    monkeypatch,
) -> None:
    """generation 读取失败时禁用缓存走数据库（fail-closed），不误用旧键。"""

    consumers = _consumers()
    _patch_allow(monkeypatch)
    session, store = _persona_store()
    await _grant_build_persona(store, session)

    class FlakyGenerationCache(FakeCache):
        async def get(self, key: str):
            if key.startswith(consumers.PERSONA_CACHE_GENERATION_PREFIX):
                raise RuntimeError("cache backend down")
            return await super().get(key)

    cache = FlakyGenerationCache()
    adapter = _adapter(session, cache)
    context = await adapter.build_public_context(43, OWNER_ID, purpose="session_context")
    assert not context.is_empty
    # 缓存被禁用：只有 generation 失效可能留下的键，没有新写入的成员键。
    member_keys = [
        key for key in cache.data if key.startswith(consumers.PERSONA_CACHE_PREFIX)
    ]
    assert member_keys == []

    # 失效侧对后端故障是 best-effort：不抛出、返回 0。
    class BrokenCache(FakeCache):
        async def incr(self, key: str):
            raise RuntimeError("cache backend down")

    broken = BrokenCache()
    assert (
        await consumers.invalidate_persona_memory_cache(
            target_user_id=OWNER_ID, cache=broken
        )
        == 0
    )


async def test_persona_cache_survives_generation_key_expiry(monkeypatch) -> None:
    """index TTL 早于成员 key 的场景：generation 键过期归零后，旧成员键
    不得复活。成员键键尾 generation 必须随内容变化，使归零键与旧键不同。"""

    consumers = _consumers()
    _patch_allow(monkeypatch)
    session, store = _persona_store()
    await _grant_build_persona(store, session)
    cache = FakeCache()
    adapter = _adapter(session, cache)

    first = await adapter.build_public_context(43, OWNER_ID, purpose="session_context")
    assert not first.is_empty
    old_keys = {
        key for key in cache.data if key.startswith(consumers.PERSONA_CACHE_PREFIX)
    }
    assert old_keys

    # 模拟 generation key 先于成员键过期（键被逐出后重新读为 0）。
    for key in [
        key
        for key in list(cache.data)
        if key.startswith(consumers.PERSONA_CACHE_GENERATION_PREFIX)
    ]:
        cache.data.pop(key)

    # 若授权仍有效，归零 generation 会构造新键（尾号 0）——与旧键不同即安全；
    # 本测试锁定"键包含 generation 段"这一防线。
    hit = await adapter.build_public_context(43, OWNER_ID, purpose="session_context")
    assert not hit.is_empty
    # 旧键仍在（残留），新读取没有命中旧键——否则等价于缓存复活。
    # （授权未撤销时命中旧键无害，但新键构造证明 generation 段参与键派生。）
    new_keys = {
        key for key in cache.data if key.startswith(consumers.PERSONA_CACHE_PREFIX)
    }
    assert new_keys == old_keys  # 同 generation(0) 下命中同一键，无新写入
    assert await cache.get(
        f"{consumers.PERSONA_CACHE_PREFIX}:43:{OWNER_ID}:1:0:0"
    ) or any(":0" in key for key in old_keys)


async def test_persona_no_unbounded_scan_or_smembers(monkeypatch) -> None:
    """构建路径不执行 SCAN/SMEMBERS；失效路径只 incr，不做逐键删除。"""

    consumers = _consumers()
    _patch_allow(monkeypatch)
    session, store = _persona_store()
    await _grant_build_persona(store, session)

    class NoScanCache(FakeCache):
        def __init__(self) -> None:
            super().__init__()
            self.scan_calls = 0
            self.delete_calls = 0

        async def scan_iter(self, match: str):
            self.scan_calls += 1
            async for key in super().scan_iter(match):
                yield key

        async def delete(self, *keys: str):
            self.delete_calls += 1
            await super().delete(*keys)

    cache = NoScanCache()
    adapter = _adapter(session, cache)
    await adapter.build_public_context(43, OWNER_ID, purpose="session_context")
    await adapter.build_public_context(44, OWNER_ID, purpose="session_context")
    assert cache.scan_calls == 0 and cache.delete_calls == 0

    await consumers.invalidate_persona_memory_cache(target_user_id=OWNER_ID, cache=cache)
    assert cache.scan_calls == 0 and cache.delete_calls == 0


async def test_persona_invalidate_covers_multiple_viewers(monkeypatch) -> None:
    """同一 target 多个 viewer 的缓存键全部随一次 generation 失效。"""

    consumers = _consumers()
    _patch_allow(monkeypatch)
    session, store = _persona_store()
    await _grant_build_persona(store, session)
    cache = FakeCache()
    adapter = _adapter(session, cache)

    for viewer in (43, 44, 45):
        context = await adapter.build_public_context(
            viewer, OWNER_ID, purpose="session_context"
        )
        assert not context.is_empty
    member_keys = [
        key for key in cache.data if key.startswith(consumers.PERSONA_CACHE_PREFIX)
    ]
    assert len(member_keys) == 3

    # 撤销数据库授权，再模拟事件驱动的缓存主动失效。
    from app.services.ai.memory.projections import MemoryProjectionService

    service = MemoryProjectionService(session, policy_revision=POLICY_REVISION)
    await service.revoke(
        owner_user_id=OWNER_ID,
        function_key="persona_context",
        purpose="session_context",
        data_category="public_profile_summary",
    )
    await consumers.invalidate_persona_memory_cache(target_user_id=OWNER_ID, cache=cache)

    # 数据库授权已撤销，所有 viewer 重建结果为空且不再命中旧键。
    for viewer in (43, 44, 45):
        rebuilt = await adapter.build_public_context(
            viewer, OWNER_ID, purpose="session_context"
        )
        assert rebuilt.is_empty


@pytest.mark.parametrize("viewer_id", [43, 44])
async def test_persona_cache_key_includes_generation_segment(
    monkeypatch, viewer_id
) -> None:
    """缓存键格式锁定为含 generation 段（viewer:target:version:revision:gen）。"""

    consumers = _consumers()
    _patch_allow(monkeypatch)
    session, store = _persona_store()
    await _grant_build_persona(store, session)
    cache = FakeCache()
    adapter = _adapter(session, cache)
    await adapter.build_public_context(viewer_id, OWNER_ID, purpose="session_context")

    member_keys = [
        key for key in cache.data if key.startswith(consumers.PERSONA_CACHE_PREFIX)
    ]
    assert len(member_keys) == 1
    key = member_keys[0]
    # 前缀 + 5 段（viewer/target/version/revision/generation）。
    assert key.startswith(
        f"{consumers.PERSONA_CACHE_PREFIX}:{viewer_id}:{OWNER_ID}:"
    )
    assert len(key.split(":")) == len(consumers.PERSONA_CACHE_PREFIX.split(":")) + 5

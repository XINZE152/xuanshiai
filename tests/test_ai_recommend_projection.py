"""Task 14：Recommend memory 模式 correctness 回归（批次三）。

计划要求（docs/superpowers/plans/2026-09-09-ai-backend-four-batches.md Task 14）：
- fail closed、可见性、consent、revision 回归；
- 逐用户 read_active() 改为批量读取后语义不变。
"""

from __future__ import annotations

import pytest

from app.core.config import settings

pytestmark = pytest.mark.asyncio


async def _batched_memory_store():
    """复用 recommend 的 memory store fixture；三用户：
    701/702 有本人画像（compatibility），703 只有 ideal_partner；
    701 的 compatibility grant 保持 active，用于撤销/轮换场景的对照组。
    """
    from tests.test_ai_recommend import _recommend_memory_store

    return await _recommend_memory_store()


async def test_memory_candidate_pool_batched_query_count(monkeypatch) -> None:
    """候选池 SQL 形态固定：发现(1) + 双维度批量(2×3) = 7 条，不随人数增长。"""
    from app.services.ai.recommend import _load_memory_candidate_pool

    monkeypatch.setattr(settings, "ai_memory_projection_read_mode", "memory")
    session, _store = await _batched_memory_store()
    baseline = len(session.calls)
    pool = await _load_memory_candidate_pool(session, viewer_id=701, limit=10)
    executed = session.calls[baseline:]
    assert len(executed) == 7, (
        f"候选池应为 发现1 + 双维度批量6 = 7 条 SQL，实际 {len(executed)}"
    )
    batch_sql = [
        sql
        for sql, _ in executed
        if "owner_user_id IN (" in sql or "user_id IN (" in sql
    ]
    assert len(batch_sql) == 6, "双维度批量各 3 条（projection/grant/consent）"
    # 不存在逐用户单读（owner_user_id = :owner_user_id）。
    assert not any(
        "owner_user_id = :owner_user_id" in sql or "user_id = :user_id" in sql
        for sql, _ in executed
    ), "不得出现逐用户单读查询"
    assert [entry["user_id"] for entry in pool] == [702]


async def test_memory_candidate_pool_semantics_unchanged_after_batching(
    monkeypatch,
) -> None:
    """批量后池语义与旧逐用户路径一致：排除 viewer/无画像者，fields 相同。"""
    from app.services.ai.features import read_memory_fields_for_kind
    from app.schemas.ai_common import ProjectionKind
    from app.services.ai.recommend import _load_memory_candidate_pool

    monkeypatch.setattr(settings, "ai_memory_projection_read_mode", "memory")
    session, _store = await _batched_memory_store()
    pool = await _load_memory_candidate_pool(session, viewer_id=701, limit=10)
    assert 701 not in [entry["user_id"] for entry in pool], "viewer 不进自己的池"
    assert 703 not in [entry["user_id"] for entry in pool], "无本人画像者不进池"
    entry = next(entry for entry in pool if entry["user_id"] == 702)
    # 与逐用户读取结果对齐（fields 同形同值）。
    legacy_shape = await read_memory_fields_for_kind(
        session, user_id=702, projection_kind=ProjectionKind.PERSONAL_COMPATIBILITY
    )
    assert entry["profile_fields"] == legacy_shape["fields"]
    assert entry["source_hash"] == legacy_shape["projection_input_hash"]
    assert entry["source"] == "memory_projection"


async def test_memory_candidate_pool_fail_closed_on_grant_revoke(monkeypatch) -> None:
    """候选人 compatibility grant 撤销 → 不进入池（不扩大数据范围）。"""
    from app.services.ai.recommend import _load_memory_candidate_pool

    monkeypatch.setattr(settings, "ai_memory_projection_read_mode", "memory")
    session, store = await _batched_memory_store()
    store.grants[(702, "compatibility", "candidate_rank", "compatibility_features")][
        "status"
    ] = "revoked"
    pool = await _load_memory_candidate_pool(session, viewer_id=701, limit=10)
    assert [entry["user_id"] for entry in pool] == [], (
        "grant 撤销的候选人必须 fail closed 出池"
    )


async def test_memory_candidate_pool_fail_closed_on_consent_rotation(
    monkeypatch,
) -> None:
    """候选人 consent 快照轮换（与投影记录不一致）→ 不进入池。"""
    from app.services.ai.recommend import _load_memory_candidate_pool

    monkeypatch.setattr(settings, "ai_memory_projection_read_mode", "memory")
    session, store = await _batched_memory_store()
    store.consents[702] = dict(
        store.consents[702], version="profile_text_extract-v4"
    )
    pool = await _load_memory_candidate_pool(session, viewer_id=701, limit=10)
    assert [entry["user_id"] for entry in pool] == []


async def test_memory_candidate_pool_fail_closed_on_projection_invalidated(
    monkeypatch,
) -> None:
    """候选人 compatibility 投影被整体失效 → 不进入池。"""
    from app.services.ai.recommend import _load_memory_candidate_pool

    monkeypatch.setattr(settings, "ai_memory_projection_read_mode", "memory")
    session, store = await _batched_memory_store()
    for rows in store.projections.values():
        for row in rows:
            if row["owner_user_id"] == 702 and row["function_key"] == "compatibility":
                row["status"] = "invalidated"
    pool = await _load_memory_candidate_pool(session, viewer_id=701, limit=10)
    assert [entry["user_id"] for entry in pool] == []


async def test_memory_candidate_pool_batch_result_matches_per_user_read(
    monkeypatch,
) -> None:
    """批量路径与逐用户 read_active 的结果逐字段一致（同门同果）。"""
    from app.services.ai.memory.projections import MemoryProjectionService
    from app.schemas.ai_common import ProjectionKind
    from app.services.ai.features import memory_dimension_for_kind
    from app.services.ai.recommend import _load_memory_candidate_pool

    monkeypatch.setattr(settings, "ai_memory_projection_read_mode", "memory")
    session, _store = await _batched_memory_store()
    service = MemoryProjectionService(session)
    batch_profiles = await service.read_active_batch(
        owner_user_ids=[701, 702, 703],
        **memory_dimension_for_kind(ProjectionKind.PERSONAL_COMPATIBILITY),
    )
    for uid in (701, 702, 703):
        single = await service.read_active(
            owner_user_id=uid,
            **memory_dimension_for_kind(ProjectionKind.PERSONAL_COMPATIBILITY),
        )
        if uid in batch_profiles:
            assert single is not None
            assert batch_profiles[uid]["projection_id"] == single["projection_id"]
            assert batch_profiles[uid]["entries"] == single["entries"]
        else:
            assert single is None, f"{uid} 批量缺失但单读存在，两路径语义分叉"
    # 候选池消费同一批数据。
    pool = await _load_memory_candidate_pool(session, viewer_id=701, limit=10)
    assert [entry["user_id"] for entry in pool] == [
        uid for uid in (702,) if uid in batch_profiles
    ]

"""Memory Kernel Core v1 MemoryService unit tests (Task 4).

MemoryService is the only component higher layers (Shadow Write, API,
backfill) talk to.  It must only write through the Ledger (every action is an
event), enforce expected_revision optimistic locking on user confirmations,
keep AI-recommended values from ever setting ``importance_confirmed``, and
honour tombstones / owner isolation.
"""

from __future__ import annotations

import inspect
import json
from typing import Any

import pytest

from app.services.ai.memory.policy import MemoryPolicyDenied
from app.services.ai.memory.service import (
    MemoryClaimNotFound,
    MemoryClaimStateDenied,
    MemoryRevisionConflict,
    MemoryService,
)

from tests.test_ai_memory_ledger import FakeMemorySession, MemoryStore, make_ledger

pytestmark = pytest.mark.asyncio

PROPOSE_KWARGS = {
    "owner_user_id": 42,
    "subject": "personal",
    "canonical_key": "personal:lifestyle:coffee",
    "dimension": "lifestyle",
    "value": "每天喝咖啡",
    "confidence": 0.82,
    "source_kind": "user_explicit",
    "fact_kind": "about_user",
    "source_quote": "我每天早上都要喝一杯咖啡",
    "idempotency_key": "propose-001",
}


def make_service() -> tuple[MemoryService, FakeMemorySession, MemoryStore]:
    store = MemoryStore()
    session = FakeMemorySession(store)
    _, _, _, _ = make_ledger(store)
    from app.services.ai.memory.ledger import MemoryLedger

    ledger = MemoryLedger(session)
    return MemoryService(session, ledger=ledger), session, store


# ---------------------------------------------------------------------------
# propose
# ---------------------------------------------------------------------------


async def test_first_propose_creates_observation_and_claim() -> None:
    service, _, store = make_service()
    records = await service.propose(**PROPOSE_KWARGS)
    assert [r.event_type.value for r in records] == ["observation_proposed", "claim_proposed"]
    assert len(store.observations) == 1
    (claim_row,) = store.claims.values()
    assert claim_row["status"] == "proposed"
    assert claim_row["importance_confirmed"] == 0, "AI 推荐值不得设置 importance_confirmed"
    assert claim_row["importance"] == 0.5


async def test_second_propose_same_key_only_adds_observation() -> None:
    service, _, store = make_service()
    await service.propose(**PROPOSE_KWARGS)
    records = await service.propose(
        **{**PROPOSE_KWARGS, "idempotency_key": "propose-002", "confidence": 0.9}
    )
    assert [r.event_type.value for r in records] == ["observation_proposed"]
    assert len(store.observations) == 2
    assert len(store.claims) == 1


async def test_propose_rejects_fact_kind_mismatch() -> None:
    service, _, store = make_service()
    with pytest.raises(MemoryPolicyDenied):
        await service.propose(**{**PROPOSE_KWARGS, "fact_kind": "partner_preference"})
    assert store.events == []


async def test_propose_blocked_by_active_tombstone_and_recovered_by_lift() -> None:
    service, _, _ = make_service()
    await service.suppress(
        owner_user_id=42,
        subject="personal",
        canonical_key=PROPOSE_KWARGS["canonical_key"],
        idempotency_key="suppress-coffee-1",
        reason="用户删除",
    )
    with pytest.raises(MemoryPolicyDenied):
        await service.propose(**{**PROPOSE_KWARGS, "idempotency_key": "propose-blocked"})
    await service.lift_suppression(
        owner_user_id=42,
        subject="personal",
        canonical_key=PROPOSE_KWARGS["canonical_key"],
        idempotency_key="lift-coffee-1",
    )
    records = await service.propose(**{**PROPOSE_KWARGS, "idempotency_key": "propose-after-lift"})
    assert records


# ---------------------------------------------------------------------------
# confirm_claim
# ---------------------------------------------------------------------------


async def _proposed_claim(service: MemoryService) -> tuple[dict[str, Any], int]:
    await service.propose(**PROPOSE_KWARGS)
    row = await service.read_claim_by_canonical(
        owner_user_id=42,
        subject="personal",
        namespace="moxiang",
        canonical_key=PROPOSE_KWARGS["canonical_key"],
    )
    assert row is not None
    return row, int(row["last_event_seq"])


async def test_confirm_requires_expected_revision_and_sets_importance_confirmed() -> None:
    service, _, store = make_service()
    row, revision = await _proposed_claim(service)
    record = await service.confirm_claim(
        owner_user_id=42,
        claim_id=row["claim_id"],
        expected_revision=revision,
        importance=0.9,
        constraint_type="preference",
    )
    assert record.event_type.value == "claim_confirmed"
    (claim_row,) = store.claims.values()
    assert claim_row["status"] == "confirmed"
    assert claim_row["importance_confirmed"] == 1
    assert claim_row["importance"] == 0.9
    assert claim_row["constraint_type"] == "preference"


async def test_confirm_with_stale_revision_conflicts() -> None:
    service, _, _ = make_service()
    row, revision = await _proposed_claim(service)
    with pytest.raises(MemoryRevisionConflict):
        await service.confirm_claim(
            owner_user_id=42,
            claim_id=row["claim_id"],
            expected_revision=revision + 5,
            importance=0.9,
        )


async def test_confirm_unknown_claim_is_owner_scoped() -> None:
    service, _, _ = make_service()
    with pytest.raises(MemoryClaimNotFound):
        await service.confirm_claim(
            owner_user_id=43,
            claim_id="clm-does-not-exist",
            expected_revision=1,
            importance=0.5,
        )


async def test_confirm_rejects_non_proposed_claim() -> None:
    service, _, _ = make_service()
    row, revision = await _proposed_claim(service)
    await service.correct_claim(
        owner_user_id=42,
        claim_id=row["claim_id"],
        expected_revision=revision,
        value="基本不喝咖啡",
    )
    with pytest.raises(MemoryClaimStateDenied):
        await service.confirm_claim(
            owner_user_id=42,
            claim_id=row["claim_id"],
            expected_revision=revision + 1,
            importance=0.5,
        )


def test_service_confirm_signature_has_no_importance_confirmed() -> None:
    params = inspect.signature(MemoryService.confirm_claim).parameters
    assert "importance_confirmed" not in params, "AI/调用方不得从接口注入 importance_confirmed"


# ---------------------------------------------------------------------------
# correct_claim
# ---------------------------------------------------------------------------


async def test_correct_updates_value_and_keeps_causal_chain() -> None:
    service, _, store = make_service()
    row, revision = await _proposed_claim(service)
    original_claim_id = str(row["claim_id"])
    previous_event_id = str(row["last_event_id"])  # 快照：物化会原地更新行
    record = await service.correct_claim(
        owner_user_id=42,
        claim_id=original_claim_id,
        expected_revision=revision,
        value="基本不喝咖啡",
        source_quote="其实我最近戒了",
    )
    assert record.event_type.value == "claim_user_corrected"
    assert record.causal_event_ids == (previous_event_id,), "纠正必须保留因果链"
    (claim_row,) = store.claims.values()
    assert claim_row["claim_id"] == original_claim_id
    assert claim_row["status"] == "user_corrected"
    assert json.loads(claim_row["value_json"]) == "基本不喝咖啡"


async def test_correct_with_stale_revision_conflicts() -> None:
    service, _, _ = make_service()
    row, revision = await _proposed_claim(service)
    with pytest.raises(MemoryRevisionConflict):
        await service.correct_claim(
            owner_user_id=42,
            claim_id=row["claim_id"],
            expected_revision=revision - 1,
            value="x",
        )


# ---------------------------------------------------------------------------
# suppress / lift_suppression
# ---------------------------------------------------------------------------


async def test_suppress_is_idempotent_and_generates_one_event() -> None:
    service, _, store = make_service()
    first = await service.suppress(
        owner_user_id=42,
        subject="personal",
        canonical_key="personal:lifestyle:tea",
        idempotency_key="suppress-tea-1",
    )
    second = await service.suppress(
        owner_user_id=42,
        subject="personal",
        canonical_key="personal:lifestyle:tea",
        idempotency_key="suppress-tea-1",
    )
    assert first.event_id == second.event_id, "同一 Idempotency-Key 重试回放同一事件"
    assert len(store.events) == 1
    (row,) = store.suppressions.values()
    assert row["status"] == "active"


async def test_lift_only_by_owner_and_reproposable() -> None:
    service, _, _ = make_service()
    await service.suppress(
        owner_user_id=42,
        subject="personal",
        canonical_key="personal:lifestyle:tea",
        idempotency_key="suppress-tea-1",
    )
    with pytest.raises(MemoryClaimNotFound):
        await service.lift_suppression(
            owner_user_id=43,
            subject="personal",
            canonical_key="personal:lifestyle:tea",
            idempotency_key="lift-tea-rogue",
        )
    record = await service.lift_suppression(
        owner_user_id=42,
        subject="personal",
        canonical_key="personal:lifestyle:tea",
        idempotency_key="lift-tea-1",
    )
    assert record.event_type.value == "suppression_lifted"
    # lift 后再次 suppress：新用户意图（新 Idempotency-Key）→ 生成新事件。
    again = await service.suppress(
        owner_user_id=42,
        subject="personal",
        canonical_key="personal:lifestyle:tea",
        idempotency_key="suppress-tea-2",
    )
    assert again.event_type.value == "suppression_activated"
    assert again.event_id != record.event_id


# ---------------------------------------------------------------------------
# Service 只通过 Ledger 写入
# ---------------------------------------------------------------------------


def test_service_module_has_no_direct_view_writes() -> None:
    from app.services.ai.memory import service as service_module

    source = inspect.getsource(service_module)
    for banned in ("INSERT INTO ai_memory", "UPDATE ai_memory", "DELETE FROM ai_memory"):
        assert banned not in source, f"MemoryService 不得直接写视图表: {banned}"


# ---------------------------------------------------------------------------
# 对抗性审查补强（2026-09-05）：墓碑过滤与统一锁序
# ---------------------------------------------------------------------------


async def test_list_excludes_active_tombstones_and_restores_after_lift() -> None:
    """删除墓碑生效后列表不得再返回该记忆；解除后恢复展示。"""

    service, _, store = make_service()
    await service.propose(**PROPOSE_KWARGS)
    (claim_row,) = store.claims.values()
    statuses = frozenset({"proposed", "confirmed"})

    items, _ = await service.list_memory_items(42, subject="personal", statuses=statuses)
    assert [item["claim_id"] for item in items] == [str(claim_row["claim_id"])]

    await service.suppress_claim(
        owner_user_id=42,
        claim_id=str(claim_row["claim_id"]),
        idempotency_key="suppress-list-1",
    )
    items, _ = await service.list_memory_items(42, subject="personal", statuses=statuses)
    assert items == [], "活动墓碑命中的 Claim 不得出现在列表中"

    (tombstone_row,) = store.suppressions.values()
    await service.lift_suppression_by_id(
        owner_user_id=42,
        suppression_id=str(tombstone_row["suppression_id"]),
        idempotency_key="lift-list-1",
    )
    items, _ = await service.list_memory_items(42, subject="personal", statuses=statuses)
    assert [item["claim_id"] for item in items] == [str(claim_row["claim_id"])]


async def test_mutators_acquire_owner_lock_before_row_locks() -> None:
    """统一锁序：owner 序列锁必须先于 claim/suppression 行锁，防 AB-BA 死锁。"""

    service, session, store = make_service()
    await service.propose(**PROPOSE_KWARGS)
    (claim_row,) = store.claims.values()

    def _owner_index() -> int:
        return next(
            i
            for i, (sql, _) in enumerate(session.calls)
            if "ai_memory_owner_sequence" in sql
        )

    session.calls.clear()
    await service.confirm_claim(
        owner_user_id=42,
        claim_id=str(claim_row["claim_id"]),
        expected_revision=int(claim_row["last_event_seq"]),
        importance=0.8,
    )
    claim_index = next(
        i
        for i, (sql, _) in enumerate(session.calls)
        if "FROM ai_memory_claim" in sql and "FOR UPDATE" in sql
    )
    assert _owner_index() < claim_index, "confirm 必须 owner 锁先行"

    session.calls.clear()
    await service.suppress_claim(
        owner_user_id=42,
        claim_id=str(claim_row["claim_id"]),
        idempotency_key="suppress-order-1",
    )
    suppression_index = next(
        i
        for i, (sql, _) in enumerate(session.calls)
        if "FROM ai_memory_suppression" in sql and "FOR UPDATE" in sql
    )
    assert _owner_index() < suppression_index, "suppress_claim 必须 owner 锁先行"

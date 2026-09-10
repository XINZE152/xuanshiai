"""Memory Kernel Core v1 materializer unit tests (Task 3).

The materializer turns append-only events into the five current views.
Every test drives it through the same fake store as the ledger tests and
asserts view-level outcomes: first-wins canonical claims, cascades,
idempotent replay and owner-scoped rebuilds.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.schemas.ai_memory import MemoryEventInput
from app.services.ai.memory.materializer import MemoryMaterializationError, MemoryMaterializer

from tests.test_ai_memory_ledger import (
    FakeMemorySession,
    MemoryStore,
    OBSERVATION_PAYLOAD,
    make_ledger,
    observation_event,
)

pytestmark = pytest.mark.asyncio

CLAIM_PAYLOAD = {
    "canonical_key": OBSERVATION_PAYLOAD["canonical_key"],
    "dimension": "lifestyle",
    "value": "每天喝咖啡",
    "confidence": 0.82,
    "stability": 0.9,
    "importance": 0.4,
    "fact_kind": "about_user",
}


def claim_event(**overrides: Any) -> MemoryEventInput:
    fields: dict[str, Any] = {
        "owner_user_id": 42,
        "subject": "personal",
        "node_type": "claim",
        "event_type": "claim_proposed",
        "payload": dict(CLAIM_PAYLOAD),
        "source_kind": "user_explicit",
        "idempotency_key": "seed-claim-001",
    }
    fields.update(overrides)
    return MemoryEventInput(**fields)


def confirmed_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        **CLAIM_PAYLOAD,
        "importance": 0.9,
        "importance_confirmed": True,
        "constraint_type": "preference",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Observation 物化
# ---------------------------------------------------------------------------


async def test_observation_explicit_source_materializes_active() -> None:
    ledger, _, store, _ = make_ledger()
    await ledger.append(observation_event())
    (row,) = store.observations.values()
    assert row["status"] == "active", "user_explicit evidence is immediately active"
    assert row["canonical_key"] == OBSERVATION_PAYLOAD["canonical_key"]
    assert row["observation_id"].startswith("obs_")


async def test_observation_inferred_source_stays_proposed() -> None:
    ledger, _, store, _ = make_ledger()
    await ledger.append(observation_event(source_kind="inferred", idempotency_key="seed-i1"))
    (row,) = store.observations.values()
    assert row["status"] == "proposed"


async def test_two_observations_same_key_merge_into_one_claim() -> None:
    ledger, _, store, _ = make_ledger()
    await ledger.append(observation_event())
    await ledger.append(observation_event(idempotency_key="seed-2"))
    await ledger.append(claim_event())
    assert len(store.observations) == 2
    assert len(store.claims) == 1
    (claim_row,) = store.claims.values()
    assert claim_row["canonical_key"] == OBSERVATION_PAYLOAD["canonical_key"]


async def test_second_claim_proposed_does_not_overwrite_first() -> None:
    ledger, _, store, _ = make_ledger()
    await ledger.append(claim_event())
    first = next(iter(store.claims.values()))
    await ledger.append(
        claim_event(
            idempotency_key="seed-claim-002",
            payload={**CLAIM_PAYLOAD, "value": "不喝咖啡", "confidence": 0.95},
        )
    )
    assert len(store.claims) == 1
    assert json.loads(store.claims[first["claim_id"]]["value_json"]) == "每天喝咖啡", (
        "same-priority conflict must keep first (no silent LWW)"
    )


# ---------------------------------------------------------------------------
# Claim 状态机
# ---------------------------------------------------------------------------


async def test_claim_confirmed_sets_importance_confirmed() -> None:
    ledger, _, store, _ = make_ledger()
    await ledger.append(claim_event())
    await ledger.append(
        claim_event(
            event_type="claim_confirmed",
            idempotency_key="seed-confirm-1",
            payload=confirmed_payload(),
        )
    )
    (claim_row,) = store.claims.values()
    assert claim_row["status"] == "confirmed"
    assert claim_row["importance_confirmed"] == 1
    assert claim_row["constraint_type"] == "preference"


async def test_claim_confirmed_is_idempotent() -> None:
    ledger, _, store, _ = make_ledger()
    await ledger.append(claim_event())
    await ledger.append(
        claim_event(event_type="claim_confirmed", idempotency_key="seed-c1", payload=confirmed_payload())
    )
    # 同状态重放：无操作不报错（幂等）。
    await ledger.append(
        claim_event(event_type="claim_confirmed", idempotency_key="seed-c2", payload=confirmed_payload())
    )
    (claim_row,) = store.claims.values()
    assert claim_row["status"] == "confirmed"


async def test_terminal_states_reject_further_transitions() -> None:
    ledger, _, store, _ = make_ledger()
    await ledger.append(claim_event())
    (claim_row,) = store.claims.values()
    insight_payload = {
        "summary": "用户对咖啡有明确日常习惯",
        "claim_ids": (claim_row["claim_id"],),
        "confidence": 0.7,
    }
    await ledger.append(
        MemoryEventInput(
            owner_user_id=42,
            subject="personal",
            node_type="insight",
            event_type="insight_proposed",
            payload=insight_payload,
            source_kind="inferred",
            idempotency_key="seed-ins-1",
        )
    )
    (insight_row,) = store.insights.values()
    await ledger.append(
        MemoryEventInput(
            owner_user_id=42,
            subject="personal",
            node_type="insight",
            event_type="insight_invalidated",
            payload={**insight_payload, "insight_id": insight_row["insight_id"]},
            source_kind="inferred",
            idempotency_key="seed-ins-2",
        )
    )
    # invalidated 是终态：再确认必须拒绝（schema 状态机 + 物化器双重校验）。
    with pytest.raises(MemoryMaterializationError):
        await ledger.append(
            MemoryEventInput(
                owner_user_id=42,
                subject="personal",
                node_type="insight",
                event_type="insight_confirmed",
                payload={**insight_payload, "insight_id": insight_row["insight_id"]},
                source_kind="inferred",
                idempotency_key="seed-ins-3",
            ),
        )


async def test_claim_correction_updates_value_and_keeps_causal_chain() -> None:
    ledger, _, store, _ = make_ledger()
    await ledger.append(observation_event())  # 纠正级联需要至少一条同 key 证据行
    await ledger.append(claim_event(idempotency_key="seed-claim-050"))
    (claim_row,) = store.claims.values()
    original_claim_id = claim_row["claim_id"]
    corrected = await ledger.append(
        claim_event(
            event_type="claim_user_corrected",
            idempotency_key="seed-correct-1",
            payload={**CLAIM_PAYLOAD, "value": "基本不喝咖啡", "stability": 0.95},
        )
    )
    (claim_row,) = store.claims.values()
    assert len(store.claims) == 1, "correction must not delete or duplicate the claim"
    assert claim_row["claim_id"] == original_claim_id
    assert claim_row["status"] == "user_corrected"
    assert json.loads(claim_row["value_json"]) == "基本不喝咖啡"
    observations = list(store.observations.values())
    assert observations and all(row["status"] == "user_corrected" for row in observations)


async def test_claim_events_for_unknown_claim_raise() -> None:
    ledger, _, _, _ = make_ledger()
    with pytest.raises(MemoryMaterializationError):
        await ledger.append(
            claim_event(event_type="claim_confirmed", idempotency_key="seed-orphan", payload=confirmed_payload())
        )


async def test_claim_transition_rejects_cross_owner_target() -> None:
    ledger, _, store, _ = make_ledger()
    await ledger.append(claim_event(owner_user_id=43))
    before = {k: dict(v) for k, v in store.claims.items()}
    with pytest.raises(MemoryMaterializationError):
        await ledger.append(
            claim_event(
                owner_user_id=42,
                event_type="claim_confirmed",
                idempotency_key="seed-cross-1",
                payload=confirmed_payload(),
            ),
        )
    assert {k: dict(v) for k, v in store.claims.items()} == before


# ---------------------------------------------------------------------------
# Suppression / State / Insight 物化
# ---------------------------------------------------------------------------


async def test_suppression_blocks_and_lifts() -> None:
    ledger, _, store, _ = make_ledger()
    await ledger.append(
        MemoryEventInput(
            owner_user_id=42,
            subject="personal",
            node_type="suppression",
            event_type="suppression_activated",
            payload={"canonical_key": OBSERVATION_PAYLOAD["canonical_key"], "reason": "用户删除"},
            source_kind="user_explicit",
            idempotency_key="seed-suppress-1",
        )
    )
    (row,) = store.suppressions.values()
    assert row["status"] == "active"
    await ledger.append(
        MemoryEventInput(
            owner_user_id=42,
            subject="personal",
            node_type="suppression",
            event_type="suppression_lifted",
            payload={"canonical_key": OBSERVATION_PAYLOAD["canonical_key"]},
            source_kind="user_explicit",
            idempotency_key="seed-lift-1",
        )
    )
    (row,) = store.suppressions.values()
    assert row["status"] == "lifted"


async def test_state_ttl_rows_materialize_and_expire() -> None:
    ledger, _, store, _ = make_ledger()
    await ledger.append(
        MemoryEventInput(
            owner_user_id=42,
            subject="personal",
            node_type="state",
            event_type="state_activated",
            payload={
                "canonical_key": "session:context:mood",
                "value": "今晚想聊轻松话题",
                "valid_until": datetime.now(UTC) + timedelta(minutes=30),
            },
            source_kind="user_explicit",
            idempotency_key="seed-state-1",
        )
    )
    (row,) = store.states.values()
    assert row["status"] == "active"
    state_id = row["state_id"]
    await ledger.append(
        MemoryEventInput(
            owner_user_id=42,
            subject="personal",
            node_type="state",
            event_type="state_expired",
            payload={
                "canonical_key": "session:context:mood",
                "value": "今晚想聊轻松话题",
                "valid_until": datetime.now(UTC) + timedelta(minutes=30),
                "state_id": state_id,
            },
            source_kind="inferred",
            idempotency_key="seed-state-2",
        )
    )
    (row,) = store.states.values()
    assert row["status"] == "expired"


async def test_insight_stores_claim_ids_and_invalidates() -> None:
    ledger, _, store, _ = make_ledger()
    await ledger.append(claim_event())
    (claim_row,) = store.claims.values()
    await ledger.append(
        MemoryEventInput(
            owner_user_id=42,
            subject="personal",
            node_type="insight",
            event_type="insight_proposed",
            payload={
                "summary": "用户对咖啡有明确日常习惯",
                "claim_ids": (claim_row["claim_id"],),
                "confidence": 0.7,
            },
            source_kind="inferred",
            idempotency_key="seed-insight-1",
        )
    )
    (insight_row,) = store.insights.values()
    assert insight_row["status"] == "proposed"
    assert claim_row["claim_id"] in insight_row["claim_ids_json"]
    await ledger.append(
        MemoryEventInput(
            owner_user_id=42,
            subject="personal",
            node_type="insight",
            event_type="insight_invalidated",
            payload={
                "summary": "用户对咖啡有明确日常习惯",
                "claim_ids": (claim_row["claim_id"],),
                "insight_id": insight_row["insight_id"],
            },
            source_kind="inferred",
            idempotency_key="seed-insight-2",
        )
    )
    (insight_row,) = store.insights.values()
    assert insight_row["status"] == "invalidated"


# ---------------------------------------------------------------------------
# 幂等重放与 rebuild_owner
# ---------------------------------------------------------------------------


async def test_replaying_same_event_keeps_single_view_row() -> None:
    ledger, session, store, _ = make_ledger()
    record = await ledger.append(observation_event())
    materializer = MemoryMaterializer(session)
    await materializer.apply(record)
    await materializer.apply(record)
    assert len(store.observations) == 1


async def test_rebuild_owner_restores_views_from_events() -> None:
    ledger, session, store, _ = make_ledger()
    await ledger.append(observation_event())
    await ledger.append(claim_event(idempotency_key="seed-claim-100"))
    snapshot = session._capture()
    store.clear_views()
    materializer = MemoryMaterializer(session)
    applied = await materializer.rebuild_owner(42)
    assert applied == 2
    assert store.claims == snapshot["claims"]
    assert store.observations == snapshot["observations"]


async def test_rebuild_owner_is_scoped_and_does_not_touch_events() -> None:
    ledger, session, store, _ = make_ledger()
    await ledger.append(observation_event(owner_user_id=43))
    events_before = [dict(row) for row in store.events]
    materializer = MemoryMaterializer(session)
    applied = await materializer.rebuild_owner(42)
    assert applied == 0
    assert [dict(row) for row in store.events] == events_before
    assert store.observations, "owner 43 rows must survive"

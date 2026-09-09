"""Projection rebuild outbox integration tests (Task 4).

``handle_memory_derivation`` must trigger a projection invalidation/rebuild
for the owner's granted dimensions on claim confirm / correct / suppress
events, keep the outbox idempotent per
``projection:{owner}:{function}:{purpose}:{category}:{input_hash}``, and
exclude facts hit by an active tombstone from rebuilt projections.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.services.derivation_outbox import DerivationEvent
from app.services.revisions import RevisionVector

from tests.test_ai_memory_projections import (
    GRANT_KWARGS,
    OWNER_ID,
    POLICY_REVISION,
    FakeProjectionSession,
    ProjectionStore,
    seed_claim,
)

pytestmark = pytest.mark.asyncio

NOW = datetime.now(UTC).replace(tzinfo=None)


def make_store_with_grant() -> tuple[FakeProjectionSession, ProjectionStore]:
    from app.services.ai.memory.projections import derive_consent_snapshot_id

    store = ProjectionStore()
    consent = {
        "scope": "profile_text_extract",
        "version": "profile_text_extract-v3",
        "policy_revision": POLICY_REVISION,
        "granted_at": "2026-09-05T08:00:00",
    }
    store.consents[OWNER_ID] = consent
    snapshot_id = derive_consent_snapshot_id(consent)
    store.grants[(OWNER_ID, "search", "candidate_filter", "personal_profile")] = {
        "grant_id": "g1",
        "owner_user_id": OWNER_ID,
        "function_key": "search",
        "purpose": "candidate_filter",
        "data_category": "personal_profile",
        "status": "active",
        "consent_snapshot_id": snapshot_id,
        "policy_revision": POLICY_REVISION,
        "granted_at": "2026-09-05T08:00:00",
        "revoked_at": None,
    }
    return FakeProjectionSession(store), store


def seed_event(
    store: ProjectionStore,
    event_id: str,
    event_type: str,
    node_type: str = "claim",
) -> DerivationEvent:
    if node_type == "claim":
        payload = {
            "canonical_key": "personal:lifestyle:digest",
            "dimension": "lifestyle",
            "value": "我每天早上都要喝一杯咖啡",
            "confidence": 0.9,
            "stability": 0.85,
            "importance": 0.5,
            "constraint_type": None,
            "importance_confirmed": False,
            "fact_kind": "about_user",
        }
    elif node_type == "insight":
        payload = {
            "summary": "用户偏好安静的生活方式",
            "claim_ids": ["c_ok"],
            "confidence": 0.6,
        }
    else:
        payload = {"canonical_key": "personal:lifestyle:digest", "reason": None}
    store.events[event_id] = {
        "event_id": event_id,
        "owner_user_id": OWNER_ID,
        "server_seq": 9,
        "subject": "personal",
        "namespace": "moxiang",
        "node_type": node_type,
        "event_type": event_type,
        "payload_json": json.dumps(payload, ensure_ascii=False),
        "source_kind": "user_confirmed",
        "source_turn_id": None,
        "source_ref": None,
        "source_quote": None,
        "causal_event_ids_json": "[]",
        "consent_scope": "profile_text_extract",
        "idempotency_key": f"idem-{event_id}",
        "occurred_at": NOW,
    }
    return DerivationEvent(
        event_id=event_id,
        aggregate_type="ai_memory",
        aggregate_id=OWNER_ID,
        event_type=f"memory_{node_type}",
        changed_fields=(),
        source_revision=RevisionVector(),
        occurred_at=NOW,
        priority=50,
    )


async def run_handler(session: FakeProjectionSession, event: DerivationEvent) -> str:
    from app.services.ai.memory.derivations import handle_memory_derivation

    return await handle_memory_derivation(session, event)


async def test_claim_confirmed_event_rebuilds_granted_dimension() -> None:
    session, store = make_store_with_grant()
    seed_claim(store, "c_ok")
    result = await run_handler(session, seed_event(store, "evt-1", "claim_confirmed"))
    assert result == "processed"
    key = (OWNER_ID, "search", "candidate_filter", "personal_profile")
    (projection,) = store.projections[key]
    assert projection["projection_version"] == 1
    assert json.loads(projection["entries_json"])[0]["claim_id"] == "c_ok"
    assert len(store.outbox) == 1


async def test_rebuild_without_active_grant_is_noop() -> None:
    session, store = make_store_with_grant()
    store.grants.clear()
    seed_claim(store, "c_ok")
    result = await run_handler(session, seed_event(store, "evt-1", "claim_confirmed"))
    assert result == "noop"
    assert not store.projections
    assert not store.outbox


async def test_same_content_two_triggers_single_outbox_row() -> None:
    session, store = make_store_with_grant()
    seed_claim(store, "c_ok")
    await run_handler(session, seed_event(store, "evt-1", "claim_confirmed"))
    await run_handler(session, seed_event(store, "evt-2", "claim_confirmed"))
    key = (OWNER_ID, "search", "candidate_filter", "personal_profile")
    assert len(store.projections[key]) == 1, "同 input hash 不得重复建版本"
    assert len(store.outbox) == 1, "同 input hash 的通知必须幂等去重"


async def test_suppression_event_removes_fact_from_projection() -> None:
    session, store = make_store_with_grant()
    claim = seed_claim(store, "c_ok")
    await run_handler(session, seed_event(store, "evt-1", "claim_confirmed"))
    key = (OWNER_ID, "search", "candidate_filter", "personal_profile")
    assert json.loads(store.projections[key][0]["entries_json"])[0]["claim_id"] == "c_ok"

    store.suppressions.append(
        {
            "owner_user_id": OWNER_ID,
            "subject": claim["subject"],
            "canonical_key": claim["canonical_key"],
            "status": "active",
        }
    )
    result = await run_handler(
        session, seed_event(store, "evt-2", "suppression_activated", node_type="suppression")
    )
    assert result == "processed"
    versions = {row["projection_version"]: row for row in store.projections[key]}
    assert versions[1]["status"] == "invalidated"
    assert versions[2]["status"] == "active"
    assert json.loads(versions[2]["entries_json"]) == [], "删除的事实不得再进入投影"


async def test_non_triggering_event_is_noop() -> None:
    session, store = make_store_with_grant()
    seed_claim(store, "c_ok")
    result = await run_handler(
        session, seed_event(store, "evt-1", "insight_proposed", node_type="insight")
    )
    assert result == "noop"
    assert not store.projections


async def test_memory_projection_outbox_rows_have_noop_consumer() -> None:
    from app.services.derivation_outbox import CLEANUP_HANDLERS

    assert "memory_projection" in CLEANUP_HANDLERS
    session, store = make_store_with_grant()
    # 通知行被消费时应为 noop，不产生副作用。
    result = await CLEANUP_HANDLERS["memory_projection"](
        session,
        DerivationEvent(
            event_id="prj-x",
            aggregate_type="ai_memory",
            aggregate_id=OWNER_ID,
            event_type="memory_projection",
            changed_fields=(),
            source_revision=RevisionVector(),
            occurred_at=NOW,
            priority=50,
        ),
    )
    assert result == "noop"

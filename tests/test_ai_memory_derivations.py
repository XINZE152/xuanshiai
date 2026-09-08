"""Memory Kernel Core v1 derivations unit tests (Task 6).

Covers the outbox fan-out (one event enqueued exactly once, minimal payload),
the insight invalidation cascade (a terminating claim event invalidates every
insight derived from that claim, without ever touching claims), and the
mandatory State TTL expiry.

The fake store already routes the outbox/receipt SQL (see
``tests.test_ai_memory_ledger.FakeMemorySession``); claim/consume reuse the
real ``derivation_outbox`` functions so receipt idempotency is exercised.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.services.revisions import RevisionVector
from app.services.derivation_outbox import (
    CLEANUP_HANDLERS,
    DerivationEvent,
    consume_outbox_event,
)
from app.services.ai.memory.derivations import (
    MEMORY_EVENT_TYPE_BY_NODE,
    expire_memory_states,
    handle_memory_derivation,
)


def _single_arg_handler(session: FakeMemorySession):
    """consume_outbox_event 的 handler 是单参 (event)；适配双参 cleanup handler。"""

    async def _run(event):
        return await handle_memory_derivation(session, event)

    return _run
from app.services.ai.memory.service import MemoryService

from tests.test_ai_memory_ledger import FakeMemorySession, MemoryStore

pytestmark = pytest.mark.asyncio

CANONICAL_KEY = "personal:lifestyle:coffee"


def _make_service() -> tuple[MemoryStore, FakeMemorySession, MemoryService]:
    store = MemoryStore()
    session = FakeMemorySession(store)
    from app.services.ai.memory.ledger import MemoryLedger

    return store, session, MemoryService(session, ledger=MemoryLedger(session))


async def _proposal_with_claim() -> tuple[MemoryStore, FakeMemorySession, MemoryService, str]:
    store, session, service = _make_service()
    await service.propose(
        owner_user_id=42,
        subject="personal",
        canonical_key=CANONICAL_KEY,
        dimension="lifestyle",
        value="每天喝咖啡",
        confidence=0.9,
        source_kind="user_explicit",
        fact_kind="about_user",
        idempotency_key="deriv-propose-1",
    )
    (claim_row,) = store.claims.values()
    return store, session, service, str(claim_row["claim_id"])


def _derivation_event(
    event_id: str, aggregate_id: int = 42, event_type: str = "memory_claim"
) -> DerivationEvent:
    return DerivationEvent(
        event_id=event_id,
        aggregate_type="ai_memory",
        aggregate_id=aggregate_id,
        event_type=event_type,
        changed_fields=(),
        source_revision=RevisionVector(),
        occurred_at=datetime.now(UTC),
        priority=50,
    )


# ---------------------------------------------------------------------------
# outbox 入队：同一 event 只入队一次；payload 最小化
# ---------------------------------------------------------------------------


async def test_same_event_enqueued_exactly_once() -> None:
    from tests.test_ai_memory_ledger import make_ledger

    ledger, _, store, _ = make_ledger()
    await ledger.append(observation_event_helper())
    await ledger.append(observation_event_helper())  # 重放：不再入队
    await ledger.append(observation_event_helper(idempotency_key="seed-2"))
    assert len(store.outbox) == 2
    rows = list(store.outbox.values())
    assert all(row["aggregate_type"] == "ai_memory" for row in rows)
    assert all(row["event_type"] == "memory_observation" for row in rows)
    assert all(row["published_at"] is not None for row in rows)


def observation_event_helper(**overrides):
    from tests.test_ai_memory_ledger import observation_event

    return observation_event(**overrides)


async def test_outbox_payload_minimal_has_no_user_content() -> None:
    from tests.test_ai_memory_ledger import make_ledger

    ledger, _, store, _ = make_ledger()
    await ledger.append(observation_event_helper())  # 事件 payload 带 value/source_quote
    (row,) = store.outbox.values()
    payload = json.loads(row["payload_minimal"])
    assert set(payload) <= {"node_type", "event_type", "subject"}, (
        "outbox payload_minimal must not carry candidate text or quotes"
    )
    assert "每天" not in row["payload_minimal"]


def test_cleanup_registry_knows_all_memory_event_types() -> None:
    for node_type, event_type in MEMORY_EVENT_TYPE_BY_NODE.items():
        assert event_type == f"memory_{node_type}"
        assert event_type in CLEANUP_HANDLERS, f"missing cleanup handler for {event_type}"
        assert CLEANUP_HANDLERS[event_type] is handle_memory_derivation


# ---------------------------------------------------------------------------
# Insight：保存 Claim ids；依赖纠正触发 invalidated；不影响 Claim
# ---------------------------------------------------------------------------


async def test_insight_stores_claim_ids_and_minimal_payload() -> None:
    store, _, service, claim_id = await _proposal_with_claim()
    records = await service.propose_insight(
        owner_user_id=42,
        subject="personal",
        summary="用户对咖啡有明确日常习惯",
        claim_ids=(claim_id,),
        confidence=0.7,
        idempotency_key="deriv-insight-1",
    )
    (insight_row,) = store.insights.values()
    assert insight_row["status"] == "proposed"
    stored_ids = json.loads(insight_row["claim_ids_json"])
    assert claim_id in stored_ids
    payload = records[0].payload
    assert set(payload) <= {"summary", "claim_ids", "confidence", "insight_id"}


async def test_claim_correction_invalidates_dependent_insight() -> None:
    store, session, service, claim_id = await _proposal_with_claim()
    await service.propose_insight(
        owner_user_id=42,
        subject="personal",
        summary="用户对咖啡有明确日常习惯",
        claim_ids=(claim_id,),
        confidence=0.7,
        idempotency_key="deriv-insight-cascade",
    )
    claim_row = next(iter(store.claims.values()))
    await service.correct_claim(
        owner_user_id=42,
        claim_id=str(claim_row["claim_id"]),
        expected_revision=int(claim_row["last_event_seq"]),
        value="基本不喝咖啡",
    )
    corrected_event_id = next(
        e["event_id"]
        for e in reversed(store.events)
        if e["event_type"] == "claim_user_corrected"
    )
    event = _derivation_event(corrected_event_id)
    result = await consume_outbox_event(
        session, event, "ai-memory-test", _single_arg_handler(session)
    )
    assert result.status == "succeeded"
    (insight_row,) = store.insights.values()
    assert insight_row["status"] == "invalidated"
    invalidation = next(
        e for e in store.events if e["event_type"] == "insight_invalidated"
    )
    causal = json.loads(invalidation["causal_event_ids_json"])
    assert corrected_event_id in causal, "失效事件必须携带因果链"
    # 重复消费：收据幂等，不再追加事件。
    before = len(store.events)
    result2 = await consume_outbox_event(
        session, event, "ai-memory-test", _single_arg_handler(session)
    )
    assert result2.status == "duplicate"
    assert len(store.events) == before


async def test_unconfirmed_insight_never_touches_claims() -> None:
    store, session, service, claim_id = await _proposal_with_claim()
    claim_row = next(iter(store.claims.values()))
    snapshot = dict(claim_row)
    await service.propose_insight(
        owner_user_id=42,
        subject="personal",
        summary="用户对咖啡有明确日常习惯",
        claim_ids=(claim_id,),
        confidence=0.7,
        idempotency_key="deriv-insight-readonly",
    )
    event_id = next(
        e["event_id"] for e in store.events if e["event_type"] == "insight_proposed"
    )
    result = await consume_outbox_event(
        session,
        _derivation_event(event_id, event_type="memory_insight"),
        "ai-memory-test",
        _single_arg_handler(session),
    )
    assert result.status == "succeeded"
    assert dict(claim_row) == snapshot, "Insight 派生不得改变长期 Claim"


# ---------------------------------------------------------------------------
# State TTL
# ---------------------------------------------------------------------------


async def test_state_ttl_expiry_is_idempotent() -> None:
    from app.schemas.ai_memory import MemoryEventInput

    from tests.test_ai_memory_ledger import make_ledger

    ledger, session, store, _ = make_ledger()
    past = datetime.now(UTC) - timedelta(minutes=5)
    await ledger.append(
        MemoryEventInput(
            owner_user_id=42,
            subject="personal",
            node_type="state",
            event_type="state_activated",
            payload={
                "canonical_key": "session:context:mood",
                "value": "今晚想聊轻松话题",
                "valid_until": past,
            },
            source_kind="user_explicit",
            idempotency_key="deriv-state-1",
        )
    )
    (state_row,) = store.states.values()
    assert state_row["status"] == "active"
    expired_first = await expire_memory_states(session, now=datetime.now(UTC))
    assert expired_first == 1
    assert state_row["status"] == "expired"
    expiry_events = [e for e in store.events if e["event_type"] == "state_expired"]
    assert len(expiry_events) == 1
    expired_second = await expire_memory_states(session, now=datetime.now(UTC))
    assert expired_second == 0
    assert len([e for e in store.events if e["event_type"] == "state_expired"]) == 1

"""Backfill (legacy -> memory kernel) unit tests (Task 8).

The backfill maps legacy rows onto the kernel with fixed legacy source rules
(never guessing from confidence):

- ``ai_profile_candidate``   -> proposed Observation (inferred);
- ``ai_profile_revision_field`` (confirmed history) -> confirmed Claim.

Unknown subjects/dimensions are skipped and counted, ``legacy:<table>:<pk>``
idempotency keys make reruns resumable, and no transcript is ever copied.
Real-database consistency runs in ``tests/integration/ai/``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.services.ai.memory.policy import MemoryPolicy
from scripts.backfill_ai_memory import (
    LEGACY_IDEMPOTENCY_PREFIX,
    candidate_identity,
    parse_args,
    run_backfill,
)

from tests.test_ai_memory_ledger import FakeMemorySession, MemoryStore

pytestmark = pytest.mark.asyncio


def _candidate_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "id": 101,
        "candidate_id": "cand-legacy-1",
        "user_id": 42,
        "subject": "personal",
        "profile_dimension": "lifestyle",
        "field_kind": "entry",
        "field_key": None,
        "category": "interests",
        "content": "周末喜欢看展",
        "value_json": None,
        "confidence": 0.85,
        "source_span": "我周末喜欢看展",
        "status": "active",
        "content_hash": "a" * 64,
    }
    row.update(overrides)
    return row


def _revision_field_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "id": 201,
        "revision_id": 1,
        "user_id": 42,
        "subject": "personal",
        "profile_dimension": "lifestyle",
        "field_kind": "structured",
        "field_key": "age",
        "category": None,
        "content": None,
        "value_json": "33",
        "confidence": 1.0,
        "content_hash": "b" * 64,
    }
    row.update(overrides)
    return row


def _make_store(
    candidates: list[dict[str, Any]] | None = None,
    revision_fields: list[dict[str, Any]] | None = None,
) -> MemoryStore:
    store = MemoryStore()
    store.legacy_candidates = candidates or []
    store.legacy_revision_fields = revision_fields or []
    return store


# ---------------------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------------------


def test_parse_args_defaults_and_dry_run() -> None:
    args = parse_args(
        ["--database-url", "mysql+aiomysql://root:@127.0.0.1:3307/x", "--batch-size", "200", "--dry-run"]
    )
    assert args.database_url.endswith("/x")
    assert args.batch_size == 200
    assert args.dry_run is True
    assert args.resume_from == 0


def test_candidate_identity_matches_shadow_write_rule() -> None:
    identity = candidate_identity(
        {"field_kind": "entry", "field_key": None, "category": "interests", "content_hash": "a" * 64}
    )
    expected = MemoryPolicy.candidate_identity("entry", None, "interests", "a" * 64)
    assert identity == expected
    structured = candidate_identity(
        {"field_kind": "structured", "field_key": "age", "category": None, "content_hash": "b" * 64}
    )
    assert structured == "age"


def test_legacy_idempotency_prefix_shape() -> None:
    assert LEGACY_IDEMPOTENCY_PREFIX.format(table="ai_profile_candidate", pk=9) == (
        "legacy:ai_profile_candidate:9"
    )


# ---------------------------------------------------------------------------
# run_backfill 行为
# ---------------------------------------------------------------------------


async def test_backfill_creates_proposed_observation_from_candidate() -> None:
    store = _make_store(candidates=[_candidate_row()])
    session = FakeMemorySession(store)
    stats = await run_backfill(session, batch_size=200, dry_run=False, resume_from=0)
    assert stats["read"] == 1
    assert stats["created"] == 1
    assert stats["rejected"] == {}
    (obs_row,) = store.observations.values()
    assert obs_row["status"] == "proposed", "legacy candidates are inferred evidence"
    assert obs_row["owner_user_id"] == 42
    events = [e for e in store.events if e["node_type"] == "observation"]
    assert events and events[0]["idempotency_key"] == "legacy:ai_profile_candidate:101"
    assert events[0]["source_kind"] == "inferred"
    payload = events[0]["payload_json"]
    assert "transcript" not in payload
    assert len(store.outbox) == 2, "propose emits observation + claim events"


async def test_backfill_creates_confirmed_claim_from_revision_field() -> None:
    store = _make_store(revision_fields=[_revision_field_row()])
    session = FakeMemorySession(store)
    stats = await run_backfill(session, batch_size=200, dry_run=False, resume_from=0)
    assert stats["created"] == 1
    (claim_row,) = store.claims.values()
    assert claim_row["status"] == "confirmed"
    assert claim_row["importance_confirmed"] == 1
    confirm_events = [e for e in store.events if e["event_type"] == "claim_confirmed"]
    assert confirm_events, "confirmed revision history must land as confirmed claim"
    assert confirm_events[0]["idempotency_key"].startswith("legacy:ai_profile_revision_field:201")


async def test_backfill_skips_unknown_subject_and_dimension() -> None:
    store = _make_store(
        candidates=[
            _candidate_row(id=1, subject="third_party_profile"),
            _candidate_row(id=2, profile_dimension="not_a_dimension"),
        ]
    )
    session = FakeMemorySession(store)
    stats = await run_backfill(session, batch_size=200, dry_run=False, resume_from=0)
    assert stats["created"] == 0
    assert stats["rejected"] == {"unknown_subject": 1, "unknown_dimension": 1}
    assert store.events == []


async def test_backfill_is_resumable_via_legacy_idempotency_key() -> None:
    store = _make_store(candidates=[_candidate_row()])
    session = FakeMemorySession(store)
    first = await run_backfill(session, batch_size=200, dry_run=False, resume_from=0)
    assert first["created"] == 1
    events_before = len(store.events)
    second = await run_backfill(session, batch_size=200, dry_run=False, resume_from=0)
    assert second["created"] == 0
    assert second["skipped"] == 1
    assert len(store.events) == events_before


async def test_backfill_dry_run_writes_nothing() -> None:
    store = _make_store(candidates=[_candidate_row()])
    session = FakeMemorySession(store)
    stats = await run_backfill(session, batch_size=200, dry_run=True, resume_from=0)
    assert stats["created"] == 1
    assert store.events == []
    assert store.observations == {}


async def test_backfill_rejects_suppressed_canonical_key() -> None:
    from app.services.ai.memory.ledger import MemoryLedger
    from app.services.ai.memory.service import MemoryService

    store = _make_store(candidates=[_candidate_row()])
    session = FakeMemorySession(store)
    service = MemoryService(session, ledger=MemoryLedger(session))
    identity = candidate_identity(_candidate_row())
    key = MemoryPolicy.canonical_key("personal", "lifestyle", identity)
    await service.suppress(
        owner_user_id=42,
        subject="personal",
        canonical_key=key,
        idempotency_key="backfill-suppress-1",
        reason="用户已删除",
    )
    stats = await run_backfill(session, batch_size=200, dry_run=False, resume_from=0)
    assert stats["rejected"] == {"suppressed": 1}
    assert stats["created"] == 0

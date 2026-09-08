"""Real-MySQL coverage for the memory kernel (Task 5 shadow write first).

The journey worker must shadow-write memory events after each candidate upsert
without disturbing the legacy candidate chain: subjects stay isolated, the
assertion mode (never confidence) decides the source kind, and memory
failures never fail the extraction task.

This module grows through Task 8/9 with concurrency, tombstone, TTL and
backfill-resume coverage on the dedicated test database.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import settings
from app.schemas.ai_common import AiConsentGrantRequest
from app.schemas.ai_profile import ProfileSubject
from app.services.ai.base import ExtractedPatch, StructuredExtractResult
from app.services.ai.consents import grant_consent
from app.services.ai.gateway import AIGateway
from app.services.ai.journey import submit_journey_turn
from app.services.ai.profile import create_master_session
from app.workers import ai_worker

from tests.integration.ai.test_moxiang_journey_worker_real_db import (
    CONSENT_VERSION,
    POLICY_REVISION,
)

pytestmark = pytest.mark.asyncio

USER_ID = 9_876_543_501
OTHER_USER_ID = USER_ID + 1


def _stub_gateway(monkeypatch: pytest.MonkeyPatch, result: StructuredExtractResult) -> None:
    """Deterministic provider: hand-crafted typed extract result."""

    async def _fake_extract(self: Any, context: Any, request: Any) -> SimpleNamespace:
        return SimpleNamespace(result=result, error_code=None, retryable=False)

    monkeypatch.setattr(AIGateway, "structured_extract", _fake_extract)


def _patch(
    *,
    subject: str,
    content: str,
    category: str = "interests",
    assertion_mode: str = "explicit",
    confidence: float = 0.9,
    quote: str | None = None,
) -> ExtractedPatch:
    return ExtractedPatch(
        action="add",
        category=category,
        content=content,
        subject=subject,  # type: ignore[arg-type]
        source_quote=quote or content,
        confidence=confidence,
        assertion_mode=assertion_mode,  # type: ignore[arg-type]
    )


async def _start_master_turn(
    real_db_session: AsyncSession,
    *,
    user_id: int,
    subject: str,
    client_turn_id: str,
    answer_text: str,
) -> str:
    """Returns the created session_id."""
    await grant_consent(
        real_db_session,
        user_id,
        "profile_text_extract",
        AiConsentGrantRequest(
            consent_version=CONSENT_VERSION,
            policy_revision=POLICY_REVISION,
        ),
        f"mem-grant-{user_id}-{client_turn_id}",
        0,
    )
    session = await create_master_session(
        real_db_session, user_id, ProfileSubject(subject), CONSENT_VERSION
    )
    await submit_journey_turn(
        real_db_session,
        session_id=session.session_id,
        owner_user_id=user_id,
        client_turn_id=client_turn_id,
        answer_text=answer_text,
    )
    await real_db_session.commit()
    return session.session_id


async def _memory_rows(
    verify_db: AsyncSession, owner_user_id: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    event_rows = (
        await verify_db.execute(
            text(
                "SELECT event_id, owner_user_id, server_seq, subject, namespace, node_type, "
                "event_type, payload_json, source_kind, source_turn_id, source_ref, "
                "source_quote, consent_scope, idempotency_key FROM ai_memory_event "
                "WHERE owner_user_id = :owner ORDER BY server_seq"
            ),
            {"owner": owner_user_id},
        )
    ).mappings().all()
    claim_rows = (
        await verify_db.execute(
            text(
                "SELECT claim_id, owner_user_id, subject, canonical_key, fact_kind, "
                "status, importance_confirmed, last_event_seq FROM ai_memory_claim "
                "WHERE owner_user_id = :owner"
            ),
            {"owner": owner_user_id},
        )
    ).mappings().all()
    return [dict(row) for row in event_rows], [dict(row) for row in claim_rows]


async def _candidate_count(verify_db: AsyncSession, session_id: str) -> int:
    row = (
        await verify_db.execute(
            text("SELECT COUNT(*) AS n FROM ai_profile_candidate WHERE session_id = :sid"),
            {"sid": session_id},
        )
    ).scalar_one()
    return int(row)


# ---------------------------------------------------------------------------
# Shadow Write 主链路
# ---------------------------------------------------------------------------


async def test_real_shadow_write_persists_memory_after_candidate_upsert(
    real_db_session: AsyncSession,
    real_db_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ai_provider", "mock")
    _stub_gateway(
        monkeypatch,
        StructuredExtractResult(
            patches=(_patch(subject="personal", content="周末喜欢看展"),),
        ),
    )
    await _start_master_turn(
        real_db_session,
        user_id=USER_ID,
        subject="personal",
        client_turn_id="mem-turn-1",
        answer_text="我周末喜欢看展。",
    )

    factory = async_sessionmaker(real_db_engine, expire_on_commit=False)
    monkeypatch.setattr(ai_worker, "session_factory", factory)
    assert await ai_worker._run_round("it-mem-worker-1", 1) == (1, 1, 0)

    async with factory() as verify_db:
        events, claims = await _memory_rows(verify_db, USER_ID)
        assert events, "shadow write must persist memory events"
        assert {e["node_type"] for e in events} == {"observation", "claim"}
        assert all(e["subject"] == "personal" for e in events)
        assert all(e["namespace"] == "moxiang" for e in events)
        assert all(e["consent_scope"] == "profile_text_extract" for e in events)
        assert [e["event_type"] for e in events] == [
            "observation_proposed",
            "claim_proposed",
        ]
        assert all(e["source_kind"] == "user_explicit" for e in events)
        payload = json.loads(events[0]["payload_json"])
        assert payload["fact_kind"] == "about_user"
        assert payload["dimension"] in (
            "personality_social",
            "intimacy_pattern",
            "lifestyle",
            "emotional_expression",
            "relationship_boundaries",
            "future_expectations",
        )
        assert set(payload) <= {
            "canonical_key",
            "dimension",
            "value",
            "confidence",
            "fact_kind",
            "note",
            "insight_id",
            "state_id",
        }, "memory payload must stay minimal (no transcript)"
        assert claims and claims[0]["status"] == "proposed"
        assert claims[0]["importance_confirmed"] == 0


async def test_real_shadow_write_isolates_subjects(
    real_db_session: AsyncSession,
    real_db_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ai_provider", "mock")
    _stub_gateway(
        monkeypatch,
        StructuredExtractResult(
            patches=(
                _patch(
                    subject="ideal_partner",
                    content="希望未来伴侣情绪稳定",
                    category="personality",
                ),
            ),
        ),
    )
    await _start_master_turn(
        real_db_session,
        user_id=USER_ID,
        subject="ideal_partner",
        client_turn_id="mem-turn-ip-1",
        answer_text="我希望对方情绪稳定。",
    )

    factory = async_sessionmaker(real_db_engine, expire_on_commit=False)
    monkeypatch.setattr(ai_worker, "session_factory", factory)
    assert await ai_worker._run_round("it-mem-worker-ip", 1) == (1, 1, 0)

    async with factory() as verify_db:
        events, claims = await _memory_rows(verify_db, USER_ID)
        assert events
        assert all(e["subject"] == "ideal_partner" for e in events)
        assert all(json.loads(e["payload_json"])["fact_kind"] == "partner_preference" for e in events)
        assert claims and claims[0]["fact_kind"] == "partner_preference"


async def test_real_shadow_write_maps_assertion_mode_not_confidence(
    real_db_session: AsyncSession,
    real_db_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """inferred 即使高置信也只能沉淀 proposed 观察；explicit 立即 active。"""
    monkeypatch.setattr(settings, "ai_provider", "mock")
    _stub_gateway(
        monkeypatch,
        StructuredExtractResult(
            patches=(
                _patch(
                    subject="personal",
                    content="习惯熬夜赶方案",
                    category="routine",
                    assertion_mode="inferred",
                    confidence=0.95,
                ),
                _patch(
                    subject="personal",
                    content="周末喜欢看展",
                    assertion_mode="explicit",
                    confidence=0.55,
                ),
            ),
        ),
    )
    await _start_master_turn(
        real_db_session,
        user_id=USER_ID,
        subject="personal",
        client_turn_id="mem-turn-mode",
        answer_text="我最近常熬夜赶方案，周末也看展。",
    )

    factory = async_sessionmaker(real_db_engine, expire_on_commit=False)
    monkeypatch.setattr(ai_worker, "session_factory", factory)
    assert await ai_worker._run_round("it-mem-worker-mode", 1) == (1, 1, 0)

    async with factory() as verify_db:
        rows = (
            await verify_db.execute(
                text(
                    "SELECT source_kind, status, confidence FROM ai_memory_observation "
                    "WHERE owner_user_id = :owner ORDER BY id"
                ),
                {"owner": USER_ID},
            )
        ).mappings().all()
        by_confidence = {round(float(row["confidence"]), 2): row for row in rows}
        assert by_confidence[0.95]["source_kind"] == "inferred"
        assert by_confidence[0.95]["status"] == "proposed", "高置信推断也只能是 proposed"
        assert by_confidence[0.55]["source_kind"] == "user_explicit"
        assert by_confidence[0.55]["status"] == "active"


async def test_real_subject_mismatch_rejects_turn_without_memory(
    real_db_session: AsyncSession,
    real_db_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """供应商把第三方/他人主体混入结果 → 整轮拒绝，零候选零记忆。"""
    monkeypatch.setattr(settings, "ai_provider", "mock")
    _stub_gateway(
        monkeypatch,
        StructuredExtractResult(
            patches=(
                _patch(subject="ideal_partner", content="他很温柔", category="personality"),
            ),
        ),
    )
    await _start_master_turn(
        real_db_session,
        user_id=USER_ID,
        subject="personal",
        client_turn_id="mem-turn-third",
        answer_text="他说他很温柔。",
    )

    factory = async_sessionmaker(real_db_engine, expire_on_commit=False)
    monkeypatch.setattr(ai_worker, "session_factory", factory)
    assert await ai_worker._run_round("it-mem-worker-third", 1) == (1, 0, 1), (
        "subject mismatch must fail the extraction task"
    )

    async with factory() as verify_db:
        events, _ = await _memory_rows(verify_db, USER_ID)
        assert events == [], "subject mismatch must never reach the memory kernel"


async def test_real_memory_failure_does_not_break_candidate_chain(
    real_db_session: AsyncSession,
    real_db_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """记忆写入失败只留安全日志，旧候选链路照常成功。"""
    monkeypatch.setattr(settings, "ai_provider", "mock")
    _stub_gateway(
        monkeypatch,
        StructuredExtractResult(
            patches=(_patch(subject="personal", content="周末喜欢看展"),),
        ),
    )

    import app.services.ai.journey as journey

    from app.services.ai.memory.service import MemoryService

    async def _boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("simulated memory outage")

    monkeypatch.setattr(MemoryService, "propose", _boom)
    assert hasattr(journey, "_shadow_write_candidate_memory")
    await _start_master_turn(
        real_db_session,
        user_id=USER_ID,
        subject="personal",
        client_turn_id="mem-turn-fail",
        answer_text="我周末喜欢看展。",
    )

    factory = async_sessionmaker(real_db_engine, expire_on_commit=False)
    monkeypatch.setattr(ai_worker, "session_factory", factory)
    assert await ai_worker._run_round("it-mem-worker-fail", 1) == (1, 1, 0)

    async with factory() as verify_db:
        events, _ = await _memory_rows(verify_db, USER_ID)
        assert events == []


async def test_real_owner_sequence_is_monotonic_and_dense(
    real_db_session: AsyncSession,
    real_db_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ai_provider", "mock")
    _stub_gateway(
        monkeypatch,
        StructuredExtractResult(
            patches=(
                _patch(subject="personal", content="周末喜欢看展"),
                _patch(subject="personal", content="习惯早起跑步", category="routine"),
            ),
        ),
    )
    await _start_master_turn(
        real_db_session,
        user_id=USER_ID,
        subject="personal",
        client_turn_id="mem-turn-seq",
        answer_text="我周末看展，早上跑步。",
    )

    factory = async_sessionmaker(real_db_engine, expire_on_commit=False)
    monkeypatch.setattr(ai_worker, "session_factory", factory)
    assert await ai_worker._run_round("it-mem-worker-seq", 1) == (1, 1, 0)

    async with factory() as verify_db:
        events, _ = await _memory_rows(verify_db, USER_ID)
        seqs = [e["server_seq"] for e in events]
        assert seqs == list(range(1, len(seqs) + 1)), "owner 内 server_seq 必须连续不重复"


# ---------------------------------------------------------------------------
# 旧动作转发：草稿确认/拒绝 → 记忆 confirm/suppress（协议不变，双写）
# ---------------------------------------------------------------------------


async def test_real_draft_confirm_forwards_to_memory_claim(
    real_db_session: AsyncSession,
    real_db_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """旅程 → 候选 → 邀请 → 草稿确认后，记忆里的同名事实应为 confirmed。

    反向也成立：reject 草稿字段会把对应记忆事实打上墓碑。
    """
    from app.services.ai.journey import maybe_create_build_invite, resolve_journey_invite
    from app.services.ai.profile import confirm_profile_draft
    from app.schemas.ai_profile import ProfileDraftFieldPatchRequest, ProfileFieldPatchAction

    monkeypatch.setattr(settings, "ai_provider", "mock")
    _stub_gateway(
        monkeypatch,
        StructuredExtractResult(
            patches=(
                _patch(subject="personal", content="周末喜欢看展", category="interests", confidence=0.95),
                _patch(subject="personal", content="性格慢热", category="personality", confidence=0.95),
                _patch(subject="personal", content="恋爱中需要个人空间", category="values", confidence=0.95),
            ),
        ),
    )
    session_id = await _start_master_turn(
        real_db_session, user_id=USER_ID, subject="personal",
        client_turn_id="mem-turn-fwd-1", answer_text="我周末喜欢看展。",
    )
    for index, answer in enumerate(("我比较慢热。", "需要个人空间。", "就这些啦。"), start=2):
        await submit_journey_turn(
            real_db_session,
            session_id=session_id,
            owner_user_id=USER_ID,
            client_turn_id=f"mem-turn-fwd-{index}",
            answer_text=answer,
        )
        await real_db_session.commit()
    factory = async_sessionmaker(real_db_engine, expire_on_commit=False)
    monkeypatch.setattr(ai_worker, "session_factory", factory)
    assert await ai_worker._run_round("it-mem-worker-fwd", 4) == (4, 4, 0)

    invite = await maybe_create_build_invite(
        real_db_session, session_id=session_id, user_id=USER_ID, subject="personal"
    )
    assert invite is not None, "0.95 置信候选必须触发建档邀请"
    _, draft_id = await resolve_journey_invite(
        real_db_session, invite_id=invite.invite_id, user_id=USER_ID, resolution="accepted"
    )
    assert draft_id is not None
    field_rows = (
        await real_db_session.execute(
            text(
                "SELECT field_key FROM ai_profile_draft_field WHERE draft_id = :draft_id "
                "AND confirmation_status = 'suggested' ORDER BY field_key"
            ),
            {"draft_id": draft_id},
        )
    ).scalars().all()
    assert field_rows
    await confirm_profile_draft(
        real_db_session,
        draft_id,
        USER_ID,
        [
            ProfileDraftFieldPatchRequest(
                field_key=str(field_key),
                action=ProfileFieldPatchAction.CONFIRM,
                expected_revision=0,
            )
            for field_key in field_rows
        ],
        expected_revision=0,
        idempotency_key="mem-fwd-confirm-1",
    )
    await real_db_session.commit()

    async with factory() as verify_db:
        _, claims = await _memory_rows(verify_db, USER_ID)
        assert claims, "shadow write must have created a claim before confirmation"
        confirmed = [c for c in claims if c["status"] == "confirmed"]
        assert confirmed, "draft confirm must forward into the memory kernel"
        assert all(c["importance_confirmed"] == 1 for c in confirmed)


# ---------------------------------------------------------------------------
# 旧数据回填（Task 8）：legacy 行 → proposed Observation / confirmed Claim
# ---------------------------------------------------------------------------


async def test_real_backfill_maps_legacy_rows_and_resumes(
    real_db_session: AsyncSession,
    real_db_engine: AsyncEngine,
) -> None:
    from scripts.backfill_ai_memory import run_backfill

    await real_db_session.execute(
        text(
            "INSERT INTO ai_profile_candidate (candidate_id, session_id, user_id, subject, "
            "profile_dimension, field_kind, field_key, category, content, value_json, "
            "confidence, source_turn_ids, source_span, consent_version, policy_revision, "
            "status, content_hash) VALUES ('cand-bf-1', 'legacy-backfill', :uid, 'personal', "
            "'lifestyle', 'entry', NULL, 'interests', '周末喜欢看展', NULL, 0.85, "
            "JSON_ARRAY('legacy-turn'), '我周末喜欢看展', 'profile-text-v1', "
            "'ai-policy-2026-08-07-v1', 'active', :hash)"
        ),
        {"uid": USER_ID, "hash": "c" * 64},
    )
    await real_db_session.execute(
        text(
            "INSERT INTO ai_profile_candidate (candidate_id, session_id, user_id, subject, "
            "profile_dimension, field_kind, field_key, category, content, value_json, "
            "confidence, source_turn_ids, source_span, consent_version, policy_revision, "
            "status, content_hash) VALUES ('cand-bf-bad', 'legacy-backfill', :uid, "
            "'third_party_profile', 'lifestyle', 'entry', NULL, 'interests', 'x', NULL, "
            "0.5, JSON_ARRAY('legacy-turn'), NULL, 'profile-text-v1', "
            "'ai-policy-2026-08-07-v1', 'active', :hash)"
        ),
        {"uid": USER_ID, "hash": "d" * 64},
    )
    await real_db_session.execute(
        text(
            "INSERT INTO ai_profile_revision (user_id, subject, revision_no, "
            "source_revision_json, policy_revision) VALUES (:uid, 'personal', 1, '{}', "
            "'ai-policy-2026-08-07-v1')"
        ),
        {"uid": USER_ID},
    )
    revision_id = (
        await real_db_session.execute(text("SELECT LAST_INSERT_ID() AS id"))
    ).scalar_one()
    await real_db_session.execute(
        text(
            "INSERT INTO ai_profile_revision_field (revision_id, field_key, subject, field_kind, "
            "profile_dimension, category, content, value_json, confidence, content_hash) "
            "VALUES (:rid, 'age', 'personal', 'structured', 'lifestyle', NULL, NULL, '33', 1.0, :hash)"
        ),
        {"rid": int(revision_id), "hash": "e" * 64},
    )
    await real_db_session.commit()

    first = await run_backfill(real_db_session, batch_size=200, dry_run=False, resume_from=0)
    assert first["created"] == 2, "one candidate + one revision_field must be created"
    assert first["rejected"].get("unknown_subject") == 1
    dry = await run_backfill(real_db_session, batch_size=200, dry_run=True, resume_from=0)
    assert dry["skipped"] == 2, "already-backfilled rows must be skipped on rerun"

    async with async_sessionmaker(real_db_engine, expire_on_commit=False)() as verify_db:
        events, claims = await _memory_rows(verify_db, USER_ID)
        observations = [
            json.loads(e["payload_json"]) for e in events if e["node_type"] == "observation"
        ]
        assert observations and observations[0]["dimension"] == "lifestyle"
        legacy_obs = [
            e
            for e in events
            if e["node_type"] == "observation" and e["idempotency_key"] == "legacy:ai_profile_candidate:1"
        ] or [
            e
            for e in events
            if e["node_type"] == "observation" and e["idempotency_key"].startswith("legacy:")
        ]
        assert legacy_obs, "legacy idempotency key must mark the backfilled event"
        assert all(e["source_kind"] == "inferred" for e in legacy_obs)
        confirmed_claims = [c for c in claims if c["status"] == "confirmed"]
        assert confirmed_claims, "legacy confirmed revision must land as confirmed claim"
        raw = json.dumps(events, ensure_ascii=False, default=str)
        assert "transcript" not in raw


# ---------------------------------------------------------------------------
# Task 9 真实 DB 深水区：并发 append / 重复确认 / State TTL
# ---------------------------------------------------------------------------


async def test_real_concurrent_appends_keep_server_seq_dense_and_unique(
    real_db_engine: AsyncEngine,
) -> None:
    """两个并发事务同时给同一 owner 写事件：server_seq 必须连续且不重复。"""
    from app.schemas.ai_memory import MemoryEventInput
    from app.services.ai.memory.ledger import MemoryLedger

    factory = async_sessionmaker(real_db_engine, expire_on_commit=False)
    owner = USER_ID

    async def _append_one(index: int) -> None:
        async with factory() as db:
            ledger = MemoryLedger(db)
            await ledger.append(
                MemoryEventInput(
                    owner_user_id=owner,
                    subject="personal",
                    node_type="observation",
                    event_type="observation_proposed",
                    payload={
                        "canonical_key": f"personal:lifestyle:conc-{index}",
                        "dimension": "lifestyle",
                        "value": f"conc-{index}",
                        "confidence": 0.9,
                        "fact_kind": "about_user",
                    },
                    source_kind="user_explicit",
                    idempotency_key=f"conc-{index}",
                )
            )
            await db.commit()

    await asyncio.gather(*(_append_one(i) for i in range(6)))

    async with factory() as verify_db:
        events, _ = await _memory_rows(verify_db, owner)
        conc_seqs = sorted(
            e["server_seq"]
            for e in events
            if e["idempotency_key"].startswith("conc-")
        )
        assert len(conc_seqs) == 6
        assert len(set(conc_seqs)) == 6, "server_seq must be unique under concurrency"
        # 连续性：这 6 条并发事件占据一段无空洞的序号区间。
        assert conc_seqs == list(range(conc_seqs[0], conc_seqs[0] + 6))


async def test_real_duplicate_confirm_replays_then_conflicts(
    real_db_session: AsyncSession,
    real_db_engine: AsyncEngine,
) -> None:
    from app.services.ai.memory.service import (
        MemoryClaimStateDenied,
        MemoryRevisionConflict,
        MemoryService,
    )

    await grant_consent(
        real_db_session,
        USER_ID,
        "profile_text_extract",
        AiConsentGrantRequest(
            consent_version=CONSENT_VERSION,
            policy_revision=POLICY_REVISION,
        ),
        "mem-confirm-grant",
        0,
    )
    session = await create_master_session(
        real_db_session, USER_ID, ProfileSubject.PERSONAL, CONSENT_VERSION
    )
    await real_db_session.commit()

    factory = async_sessionmaker(real_db_engine, expire_on_commit=False)
    async with factory() as work_db:
        service = MemoryService(work_db)
        records = await service.propose(
            owner_user_id=USER_ID,
            subject="personal",
            canonical_key="personal:lifestyle:confirm-flow",
            dimension="lifestyle",
            value="每天喝咖啡",
            confidence=0.9,
            source_kind="user_explicit",
            fact_kind="about_user",
            idempotency_key="confirm-flow-propose",
        )
        await work_db.commit()
        claim_row = await service.read_claim_by_canonical(
            owner_user_id=USER_ID,
            subject="personal",
            namespace="moxiang",
            canonical_key="personal:lifestyle:confirm-flow",
        )
        assert claim_row is not None
        claim_id = str(claim_row["claim_id"])
        revision = int(claim_row["last_event_seq"])

        first = await service.confirm_claim(
            owner_user_id=USER_ID,
            claim_id=claim_id,
            expected_revision=revision,
            importance=0.9,
            idempotency_key="confirm-flow-key",
        )
        await work_db.commit()
        events_before = len((await _memory_rows(work_db, USER_ID))[0])

        # 同 key 重试：回放同一事件，不追加。
        replay = await service.confirm_claim(
            owner_user_id=USER_ID,
            claim_id=claim_id,
            expected_revision=revision,
            importance=0.9,
            idempotency_key="confirm-flow-key",
        )
        assert replay.event_id == first.event_id
        events_after = len((await _memory_rows(work_db, USER_ID))[0])
        assert events_after == events_before

        # 不同 key 的过期 revision：稳定 409。
        try:
            await service.confirm_claim(
                owner_user_id=USER_ID,
                claim_id=claim_id,
                expected_revision=revision,
                importance=0.9,
                idempotency_key="confirm-flow-stale",
            )
            raise AssertionError("stale revision must conflict")
        except (MemoryRevisionConflict, MemoryClaimStateDenied):
            pass
        await work_db.rollback()


async def test_real_state_ttl_expiry_on_real_db(
    real_db_session: AsyncSession,
    real_db_engine: AsyncEngine,
) -> None:
    from datetime import UTC, datetime, timedelta

    from app.schemas.ai_memory import MemoryEventInput
    from app.services.ai.memory.derivations import expire_memory_states
    from app.services.ai.memory.ledger import MemoryLedger

    factory = async_sessionmaker(real_db_engine, expire_on_commit=False)
    past = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
    async with factory() as db:
        ledger = MemoryLedger(db)
        await ledger.append(
            MemoryEventInput(
                owner_user_id=USER_ID,
                subject="personal",
                node_type="state",
                event_type="state_activated",
                payload={
                    "canonical_key": "session:context:ttl",
                    "value": "轻松话题",
                    "valid_until": past,
                },
                source_kind="user_explicit",
                idempotency_key="ttl-state-1",
            )
        )
        await db.commit()

    async with factory() as db:
        expired = await expire_memory_states(db, now=datetime.now(UTC).replace(tzinfo=None))
        await db.commit()
        assert expired == 1
        again = await expire_memory_states(db, now=datetime.now(UTC).replace(tzinfo=None))
        assert again == 0
        row = (
            await db.execute(
                text(
                    "SELECT status FROM ai_memory_state WHERE owner_user_id = :o "
                    "AND canonical_key = 'session:context:ttl'"
                ),
                {"o": USER_ID},
            )
        ).scalar_one()
        assert row == "expired"


async def test_real_concurrent_first_writes_on_fresh_owner(
    real_db_engine: AsyncEngine,
) -> None:
    """对抗性审查回归：全新 owner 的 6 路并发首写。

    旧实现是先 SELECT FOR UPDATE 再 insert-if-missing——REPEATABLE READ 下
    真并发首写会互持间隙锁死锁（裸连接实验实测 1213）；ensure-first
    （幂等 upsert 先行）按行锁串行化。注意 pytest 的事件循环 + 建连节奏
    可能把竞态错开，所以本测试是并发冒烟 + 不变量验证；锁序本身由
    tests/test_ai_memory_ledger.py 的语句顺序单测确定性钉死。
    """

    from sqlalchemy import text as sql_text

    from app.schemas.ai_memory import MemoryEventInput
    from app.services.ai.memory.ledger import MemoryLedger

    factory = async_sessionmaker(real_db_engine, expire_on_commit=False)
    owner = USER_ID + 1000  # 全新 owner：清理范围内、无 owner_sequence 行

    async with factory() as db:
        await db.execute(
            sql_text(
                "DELETE FROM ai_memory_owner_sequence WHERE owner_user_id = :owner"
            ),
            {"owner": owner},
        )
        await db.execute(
            sql_text("DELETE FROM ai_memory_event WHERE owner_user_id = :owner"),
            {"owner": owner},
        )
        await db.commit()

    async def _append_one(index: int) -> None:
        async with factory() as db:
            ledger = MemoryLedger(db)
            await ledger.append(
                MemoryEventInput(
                    owner_user_id=owner,
                    subject="personal",
                    node_type="observation",
                    event_type="observation_proposed",
                    payload={
                        "canonical_key": f"personal:lifestyle:fresh-{index}",
                        "dimension": "lifestyle",
                        "value": f"fresh-{index}",
                        "confidence": 0.9,
                        "fact_kind": "about_user",
                    },
                    source_kind="user_explicit",
                    idempotency_key=f"conc-fresh-{index}",
                )
            )
            await db.commit()

    await asyncio.gather(*(_append_one(i) for i in range(6)))

    async with factory() as verify_db:
        events, _ = await _memory_rows(verify_db, owner)
        fresh_seqs = sorted(
            e["server_seq"]
            for e in events
            if e["idempotency_key"].startswith("conc-fresh-")
        )
        assert len(fresh_seqs) == 6, "并发首写不得丢事件（死锁/uk 冲突即失败）"
        assert len(set(fresh_seqs)) == 6, "server_seq 必须唯一"
        assert fresh_seqs == [1, 2, 3, 4, 5, 6], "首写段必须从 1 起连续无空洞"

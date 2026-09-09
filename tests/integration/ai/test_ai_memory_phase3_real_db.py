"""Memory Kernel Phase 3 real-DB tests (Task 5).

Dedicated MySQL (3307) + in-process service calls, real ledger/claims:
consent → projection grants (all 8 consumer dimensions) → build → counselor /
persona adapter reads → revoke (immediate invalidation) → producer retry
event → rollback drill (legacy/shadow read modes never mutate Core rows).
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.schemas.ai_common import AiConsentGrantRequest
from app.services.ai.consents import grant_consent, revoke_consent
from app.services.ai.memory import consent_producers
from app.services.ai.memory.consumers import (
    CounselorMemoryAdapter,
    PersonaMemoryAdapter,
    PERSONA_PUBLIC_FIELD_ALLOWLIST,
)
from app.services.ai.memory.ledger import MemoryLedger
from app.services.ai.memory.projections import MemoryProjectionService
from app.services.ai.memory.service import MemoryService
from app.services.ai.profile import PROFILE_POLICY_REVISION as CORE_POLICY_REVISION

from tests.integration.ai.test_moxiang_journey_worker_real_db import CONSENT_VERSION

pytestmark = pytest.mark.asyncio

USER_ID = 9_876_543_611
VIEWER_ID = 9_876_543_612


def _service(db: AsyncSession) -> MemoryProjectionService:
    return MemoryProjectionService(db, policy_revision=CORE_POLICY_REVISION)


async def _seed_users(db: AsyncSession, *user_ids: int) -> None:
    """可见性门所需的最小用户行：users + user_privacy + profile_completion。"""

    for user_id in user_ids:
        await db.execute(
            text(
                "INSERT IGNORE INTO users (id, nickname, gender, birthday, status) "
                "VALUES (:id, :nickname, 1, '1992-05-01', 1)"
            ),
            {"id": user_id, "nickname": f"p3-user-{user_id}"},
        )
        await db.execute(
            text(
                "INSERT IGNORE INTO user_privacy (user_id, who_can_see_me, show_profile, match_status) "
                "VALUES (:id, 1, 1, 1)"
            ),
            {"id": user_id},
        )
        await db.execute(
            text(
                "INSERT IGNORE INTO user_profile_completion (user_id, score) VALUES (:id, 120)"
            ),
            {"id": user_id},
        )
    await db.commit()


async def _grant_consent(db: AsyncSession, user_id: int, idem: str) -> None:
    await grant_consent(
        db,
        user_id,
        "profile_text_extract",
        AiConsentGrantRequest(
            consent_version=CONSENT_VERSION,
            policy_revision=CORE_POLICY_REVISION,
        ),
        idem,
        0,
    )


async def _seed_confirmed_claim(
    db: AsyncSession, user_id: int, marker: str, value: str
) -> str:
    service = MemoryService(db)
    canonical_key = f"personal:lifestyle:rt-{marker}"
    await service.propose(
        owner_user_id=user_id,
        subject="personal",
        canonical_key=canonical_key,
        dimension="lifestyle",
        value=value,
        confidence=0.9,
        source_kind="user_explicit",
        fact_kind="about_user",
        source_quote=f"对话原文：{value}，之后我还聊了别的事",
        source_ref=f"rt:{marker}",
        idempotency_key=f"rt-propose-{marker}",
    )
    row = (
        await db.execute(
            text(
                "SELECT claim_id, last_event_seq FROM ai_memory_claim "
                "WHERE owner_user_id = :owner AND canonical_key = :key"
            ),
            {"owner": user_id, "key": canonical_key},
        )
    ).mappings().first()
    await service.confirm_claim(
        owner_user_id=user_id,
        claim_id=str(row["claim_id"]),
        expected_revision=int(row["last_event_seq"]),
        importance=0.6,
    )
    return str(row["claim_id"])


async def test_consent_grant_produces_all_consumer_dimensions(
    real_db_session: AsyncSession,
) -> None:
    """consent 授予 → 8 个消费维度 grant 全部落库（真实 DB）。"""

    await _grant_consent(real_db_session, USER_ID, f"rt-p3-consent-{USER_ID}")
    rows = (
        await real_db_session.execute(
            text(
                "SELECT function_key, purpose, data_category, status "
                "FROM ai_memory_projection_grant WHERE owner_user_id = :owner"
            ),
            {"owner": USER_ID},
        )
    ).mappings().all()
    dimensions = {
        (str(row["function_key"]), str(row["purpose"]), str(row["data_category"]))
        for row in rows
    }
    assert dimensions == {
        (d["function_key"], d["purpose"], d["data_category"])
        for d in consent_producers.CONSENT_PRODUCER_DIMENSIONS
    }
    assert all(str(row["status"]) == "active" for row in rows)
    await real_db_session.commit()


async def test_consumer_input_closed_loop_and_revoke_fail_closed(
    real_db_session: AsyncSession,
) -> None:
    """consent → grant → build → 军师/分身读取 → revoke → fail closed 闭环。"""

    await _seed_users(real_db_session, USER_ID, VIEWER_ID)
    await _grant_consent(real_db_session, USER_ID, f"rt-p3-loop-{USER_ID}")
    claim_id = await _seed_confirmed_claim(
        real_db_session, USER_ID, "p3loop", "每天喝咖啡"
    )
    # persona 公开 allowlist 需要 structured 身份（field_key 反解为 age）。
    from app.services.ai.memory.policy import MemoryPolicy

    age_key = MemoryPolicy.canonical_key(
        "personal", "lifestyle",
        MemoryPolicy.candidate_identity("structured", "age", None, None),
    )
    persona_service = MemoryService(real_db_session)
    await persona_service.propose(
        owner_user_id=USER_ID,
        subject="personal",
        canonical_key=age_key,
        dimension="lifestyle",
        value=25,
        confidence=0.9,
        source_kind="user_explicit",
        fact_kind="about_user",
        source_quote="对话原文：我今年25岁，之后我还聊了别的事",
        source_ref="rt:p3loop-age",
        idempotency_key="rt-propose-p3loop-age",
    )
    row = (
        await real_db_session.execute(
            text(
                "SELECT claim_id, last_event_seq FROM ai_memory_claim "
                "WHERE owner_user_id = :owner AND canonical_key = :key"
            ),
            {"owner": USER_ID, "key": age_key},
        )
    ).mappings().first()
    await persona_service.confirm_claim(
        owner_user_id=USER_ID,
        claim_id=str(row["claim_id"]),
        expected_revision=int(row["last_event_seq"]),
        importance=0.6,
    )
    service = _service(real_db_session)
    snapshot = await service._load_active_consent(USER_ID)
    snapshot_id = MemoryProjectionService._snapshot_id(snapshot)
    for category in ("personal_profile", "ideal_partner_preference"):
        await service.grant(
            owner_user_id=USER_ID,
            function_key="counselor_context",
            purpose="session_context",
            data_category=category,
            consent_snapshot_id=snapshot_id,
            policy_revision=CORE_POLICY_REVISION,
        )
    await service.grant(
        owner_user_id=USER_ID,
        function_key="persona_context",
        purpose="session_context",
        data_category="public_profile_summary",
        consent_snapshot_id=snapshot_id,
        policy_revision=CORE_POLICY_REVISION,
    )
    for category in ("personal_profile", "ideal_partner_preference"):
        await service.build(
            owner_user_id=USER_ID,
            function_key="counselor_context",
            purpose="session_context",
            data_category=category,
        )
    await service.build(
        owner_user_id=USER_ID,
        function_key="persona_context",
        purpose="session_context",
        data_category="public_profile_summary",
    )
    await real_db_session.commit()

    counselor = CounselorMemoryAdapter(real_db_session)
    context = await counselor.build_context(USER_ID, purpose="session_context")
    assert not context.is_empty, "confirmed claim 必须进入军师上下文"
    payload = json.dumps(context.to_prompt_payload(), ensure_ascii=False)
    assert "对话原文" not in payload and "之后我还聊了别的事" not in payload, (
        "Provider 输入不含 raw quote"
    )
    assert "source_quote" not in payload and "transcript" not in payload
    assert "evidence_ref" not in payload and "source_kind" not in payload

    class _NullCache:
        async def get(self, key):
            return None

        async def set(self, key, value, ex=None):
            return None

        async def delete(self, *keys):
            return None

        async def scan_iter(self, match):
            return
            yield

    persona = PersonaMemoryAdapter(real_db_session, cache=_NullCache())
    persona_context = await persona.build_public_context(
        VIEWER_ID, USER_ID, purpose="session_context"
    )
    assert not persona_context.is_empty
    for entry in persona_context.iter_entries():
        assert entry["field_key"] in PERSONA_PUBLIC_FIELD_ALLOWLIST
    persona_payload = json.dumps(
        persona_context.to_prompt_payload(), ensure_ascii=False
    )
    assert "对话原文" not in persona_payload and "之后我还聊了别的事" not in persona_payload

    # 撤回 consent → 生产者同步撤销全部授权 + 失效 active 投影。
    revision_row = (
        await real_db_session.execute(
            text("SELECT privacy_revision FROM user_revision_state WHERE user_id = :o"),
            {"o": USER_ID},
        )
    ).mappings().first()
    await revoke_consent(
        real_db_session,
        USER_ID,
        "profile_text_extract",
        f"rt-p3-revoke-{USER_ID}",
        int(revision_row["privacy_revision"] or 0) if revision_row else 0,
    )
    await real_db_session.commit()
    grants = (
        await real_db_session.execute(
            text(
                "SELECT status FROM ai_memory_projection_grant "
                "WHERE owner_user_id = :owner"
            ),
            {"owner": USER_ID},
        )
    ).mappings().all()
    assert grants and all(str(row["status"]) == "revoked" for row in grants)
    projections = (
        await real_db_session.execute(
            text(
                "SELECT status FROM ai_memory_projection WHERE owner_user_id = :owner"
            ),
            {"owner": USER_ID},
        )
    ).mappings().all()
    assert projections and all(str(row["status"]) == "invalidated" for row in projections)

    # 撤权后军师/分身读取 fail closed。
    assert (
        await counselor.build_context(USER_ID, purpose="session_context")
    ).is_empty
    assert (
        await persona.build_public_context(VIEWER_ID, USER_ID, purpose="session_context")
    ).is_empty


async def test_producer_retry_event_processed_by_handler(
    real_db_session: AsyncSession, monkeypatch
) -> None:
    """生产者失败 → outbox 重试事件 → handler 重放授予成功（真实 DB）。"""

    from app.services.ai.memory import consent_producers as producers_mod

    async def failing_grant(db, **kwargs):
        raise RuntimeError("injected producer failure")

    monkeypatch.setattr(
        producers_mod, "grant_projection_dimensions_for_consent", failing_grant
    )
    await _grant_consent(real_db_session, USER_ID, f"rt-p3-retry-{USER_ID}")
    rows = (
        await real_db_session.execute(
            text(
                "SELECT event_id, payload_minimal FROM derivation_outbox "
                "WHERE event_type = 'memory_projection_producer' "
                "AND aggregate_id = :owner"
            ),
            {"owner": USER_ID},
        )
    ).mappings().all()
    assert rows, "授权失败必须留下 memory_projection_producer 重试事件"
    # 恢复真实生产者后重放 handler（handler 内部读取真实 consent 行）。
    monkeypatch.undo()
    from app.services.ai.memory.consent_producers import (
        handle_projection_producer_retry,
    )

    for row in rows:
        event = _event_from_payload(row)
        outcome = await handle_projection_producer_retry(real_db_session, event)
        assert outcome == "processed"
    await real_db_session.commit()
    grants = (
        await real_db_session.execute(
            text(
                "SELECT COUNT(*) AS n FROM ai_memory_projection_grant "
                "WHERE owner_user_id = :owner AND status = 'active'"
            ),
            {"owner": USER_ID},
        )
    ).scalar()
    assert int(grants) == len(consent_producers.CONSENT_PRODUCER_DIMENSIONS)


def _event_from_payload(row) -> object:
    from app.services.derivation_outbox import DerivationEvent
    from app.services.revisions import RevisionVector

    payload = json.loads(str(row["payload_minimal"]))
    return DerivationEvent(
        event_id=str(row["event_id"]),
        aggregate_type="ai_memory",
        aggregate_id=9_876_543_611,
        event_type="memory_projection_producer",
        changed_fields=(),
        source_revision=RevisionVector(privacy=0),
        occurred_at=None,
        priority=30,
        payload=payload,
    )


@pytest.mark.parametrize("mode", ["legacy", "shadow", "memory"])
async def test_read_modes_rollback_never_mutates_core(
    real_db_session: AsyncSession, mode: str
) -> None:
    """三种读取模式切换：Core 事件/Claim 行数不变；memory 模式下授权驱动可读。"""

    original_mode = settings.ai_memory_projection_read_mode
    try:
        await _grant_consent(real_db_session, USER_ID, f"rt-p3-mode-{mode}-{USER_ID}")
        await _seed_confirmed_claim(
            real_db_session, USER_ID, f"p3mode{mode}", "每周打两次球"
        )
        service = _service(real_db_session)
        snapshot = await service._load_active_consent(USER_ID)
        snapshot_id = MemoryProjectionService._snapshot_id(snapshot)
        # 军师契约要求 personal/ideal_partner 两个冻结类别都通过当前授权；
        # 生产链路由 claim 事件触发 rebuild_dimensions_for_owner 物化全部
        # 已授权维度（含空条目的 ideal_partner），此处手工 setup 必须对齐。
        for category in ("personal_profile", "ideal_partner_preference"):
            await service.grant(
                owner_user_id=USER_ID,
                function_key="counselor_context",
                purpose="session_context",
                data_category=category,
                consent_snapshot_id=snapshot_id,
                policy_revision=CORE_POLICY_REVISION,
            )
        await real_db_session.commit()

        counts_before = await _core_counts(real_db_session, USER_ID)
        settings.ai_memory_projection_read_mode = mode
        if mode == "memory":
            for category in ("personal_profile", "ideal_partner_preference"):
                await service.build(
                    owner_user_id=USER_ID,
                    function_key="counselor_context",
                    purpose="session_context",
                    data_category=category,
                )
            await real_db_session.commit()
            counselor = CounselorMemoryAdapter(real_db_session)
            context = await counselor.build_context(
                USER_ID, purpose="session_context"
            )
            assert not context.is_empty
        counts_after = await _core_counts(real_db_session, USER_ID)
        assert counts_after == counts_before or mode == "memory", (
            "legacy/shadow 模式不修改 Core 行"
        )
        # memory 模式只允许新增（append-only），不修改既有行。
        if mode == "memory":
            assert counts_after["events"] >= counts_before["events"]
            assert counts_after["claims"] == counts_before["claims"]
    finally:
        settings.ai_memory_projection_read_mode = original_mode
        await real_db_session.rollback()


async def _core_counts(db: AsyncSession, owner: int) -> dict[str, int]:
    events = (
        await db.execute(
            text("SELECT COUNT(*) FROM ai_memory_event WHERE owner_user_id = :o"),
            {"o": owner},
        )
    ).scalar()
    claims = (
        await db.execute(
            text("SELECT COUNT(*) FROM ai_memory_claim WHERE owner_user_id = :o"),
            {"o": owner},
        )
    ).scalar()
    return {"events": int(events), "claims": int(claims)}

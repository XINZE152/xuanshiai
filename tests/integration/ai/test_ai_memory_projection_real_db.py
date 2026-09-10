"""Memory Projection Phase 2 real-DB tests (Task 9).

Dedicated MySQL (3307) + real ledger/claims: grant/revoke/read cycle,
version uniqueness under concurrency, outbox idempotency, suppress-driven
invalidation, consent/policy fail-closed gates, and the rollback drill
(switching back to legacy never mutates Core event/claim rows).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import settings
from app.schemas.ai_common import AiConsentGrantRequest
from app.services.ai.consents import grant_consent
from app.services.ai.memory.ledger import MemoryLedger
from app.services.ai.memory.projections import (
    ProjectionGrantDenied,
    MemoryProjectionService,
)
from app.services.ai.memory.service import MemoryService
from app.services.ai.profile import PROFILE_POLICY_REVISION as CORE_POLICY_REVISION

from tests.integration.ai.test_moxiang_journey_worker_real_db import CONSENT_VERSION

pytestmark = pytest.mark.asyncio

USER_ID = 9_876_543_601
OTHER_ID = 9_876_543_602


def _service(db: AsyncSession) -> MemoryProjectionService:
    return MemoryProjectionService(db, policy_revision=CORE_POLICY_REVISION)


async def _clean_owner(db: AsyncSession, owner: int) -> None:
    for table in (
        "ai_memory_projection",
        "ai_memory_projection_grant",
        "ai_memory_claim",
        "ai_memory_observation",
        "ai_memory_event",
        "ai_memory_owner_sequence",
        "ai_memory_suppression",
    ):
        await db.execute(
            text(f"DELETE FROM {table} WHERE owner_user_id = :owner"),
            {"owner": owner},
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
    """ledger propose(user_explicit) → confirm → confirmed claim；返回 claim_id。"""

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
        source_quote=value,
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


async def _grant_search_dimension(db: AsyncSession, user_id: int) -> None:
    snapshot = await _service(db)._load_active_consent(user_id)
    await _service(db).grant(
        owner_user_id=user_id,
        function_key="search",
        purpose="candidate_filter",
        data_category="personal_profile",
        consent_snapshot_id=MemoryProjectionService._snapshot_id(snapshot),
        policy_revision=CORE_POLICY_REVISION,
    )


async def test_real_grant_build_revoke_read_cycle(
    real_db_session: AsyncSession,
) -> None:
    await _clean_owner(real_db_session, USER_ID)
    await _grant_consent(real_db_session, USER_ID, f"rt-grant-{USER_ID}-a")
    await _seed_confirmed_claim(real_db_session, USER_ID, "cycle", "每天喝咖啡")
    await _grant_search_dimension(real_db_session, USER_ID)
    service = _service(real_db_session)

    doc = await service.build(
        owner_user_id=USER_ID,
        function_key="search",
        purpose="candidate_filter",
        data_category="personal_profile",
    )
    assert doc["projection_version"] == 1
    assert doc["entries"], "confirmed claim 必须进入投影"

    read = await service.read_active(
        owner_user_id=USER_ID,
        function_key="search",
        purpose="candidate_filter",
        data_category="personal_profile",
    )
    assert read is not None
    assert "source_quote" not in json.dumps(read, default=str), (
        "投影响应不得携带原文摘录"
    )

    # revoke → 立即失效 → 读取为 None（fail closed）
    invalidated = await service.revoke(
        owner_user_id=USER_ID,
        function_key="search",
        purpose="candidate_filter",
        data_category="personal_profile",
    )
    assert invalidated == 1
    assert (
        await service.read_active(
            owner_user_id=USER_ID,
            function_key="search",
            purpose="candidate_filter",
            data_category="personal_profile",
        )
        is None
    )

    # re-grant（幂等复活）→ 读取恢复（投影行从未删除）
    snapshot = await service._load_active_consent(USER_ID)
    await service.grant(
        owner_user_id=USER_ID,
        function_key="search",
        purpose="candidate_filter",
        data_category="personal_profile",
        consent_snapshot_id=service._snapshot_id(snapshot),
        policy_revision=CORE_POLICY_REVISION,
    )
    # revoke 已把投影行失效；re-grant 只复活授权。同内容重建接管历史版本
    # （重新激活 v1，不插新版本——input hash 幂等语义）。
    read_again = await service.build(
        owner_user_id=USER_ID,
        function_key="search",
        purpose="candidate_filter",
        data_category="personal_profile",
    )
    assert read_again["projection_version"] == 1
    assert read_again["status"] == "active"
    live = await service.read_active(
        owner_user_id=USER_ID,
        function_key="search",
        purpose="candidate_filter",
        data_category="personal_profile",
    )
    assert live is not None and live["projection_version"] == 1
    await real_db_session.commit()


async def test_real_concurrent_builds_keep_versions_unique(
    real_db_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(real_db_engine, expire_on_commit=False)
    async with factory() as db:
        await _clean_owner(db, USER_ID)
        await _grant_consent(db, USER_ID, f"rt-grant-{USER_ID}-b")
        await _seed_confirmed_claim(db, USER_ID, "conc-a", "喜欢徒步")
        await _grant_search_dimension(db, USER_ID)
        await db.commit()

    async def _build_same() -> None:
        async with factory() as db:
            await _service(db).build(
                owner_user_id=USER_ID,
                function_key="search",
                purpose="candidate_filter",
                data_category="personal_profile",
            )
            await db.commit()

    # 同 input hash 并发：唯一键兜底，只落一行。
    await asyncio.gather(*(_build_same() for _ in range(4)))

    async with factory() as verify:
        rows = (
            await verify.execute(
                text(
                    "SELECT projection_version, status FROM ai_memory_projection "
                    "WHERE owner_user_id = :owner AND function_key = 'search' "
                    "AND purpose = 'candidate_filter' AND data_category = 'personal_profile'"
                ),
                {"owner": USER_ID},
            )
        ).mappings().all()
    versions = sorted(int(row["projection_version"]) for row in rows)
    assert versions == [1], f"同 input hash 并发只允许一个版本: {versions}"
    assert all(row["status"] == "active" for row in rows)


async def test_real_rebuild_outbox_is_idempotent(
    real_db_session: AsyncSession,
) -> None:
    await _clean_owner(real_db_session, USER_ID)
    await _grant_consent(real_db_session, USER_ID, f"rt-grant-{USER_ID}-c")
    await _seed_confirmed_claim(real_db_session, USER_ID, "outbox", "坚持晨跑")
    await _grant_search_dimension(real_db_session, USER_ID)
    service = _service(real_db_session)

    touched1 = await service.rebuild_dimensions_for_owner(
        USER_ID, trigger_ref="rt-1"
    )
    touched2 = await service.rebuild_dimensions_for_owner(
        USER_ID, trigger_ref="rt-2"
    )
    # Phase 3 起 consent 授予自动创建全部 8 个消费维度授权，rebuild 触达
    # 全部授权维度（有 confirmed claim 的维度产出投影，其余只补幂等通知）。
    expected_dimensions = len(
        __import__("app.services.ai.memory.consent_producers", fromlist=["x"])
        .CONSENT_PRODUCER_DIMENSIONS
    )
    assert touched1 == expected_dimensions and touched2 == expected_dimensions
    rows = (
        await real_db_session.execute(
            text(
                "SELECT event_id FROM derivation_outbox "
                "WHERE aggregate_id = :owner AND event_type = 'memory_projection'"
            ),
            {"owner": USER_ID},
        )
    ).mappings().all()
    assert len(rows) == expected_dimensions, (
        f"同 input hash 每维度只允许一条通知: {[r['event_id'] for r in rows]}"
    )
    await real_db_session.commit()


async def test_real_suppress_removes_fact_from_projection(
    real_db_session: AsyncSession,
) -> None:
    await _clean_owner(real_db_session, USER_ID)
    await _grant_consent(real_db_session, USER_ID, f"rt-grant-{USER_ID}-d")
    await _seed_confirmed_claim(real_db_session, USER_ID, "sup-a", "每天喝咖啡")
    await _seed_confirmed_claim(real_db_session, USER_ID, "sup-b", "坚持夜跑")
    await _grant_search_dimension(real_db_session, USER_ID)
    service = _service(real_db_session)
    doc = await service.build(
        owner_user_id=USER_ID,
        function_key="search",
        purpose="candidate_filter",
        data_category="personal_profile",
    )
    assert len(doc["entries"]) == 2

    # 删除其中一条事实（墓碑）→ 重建 → 新版本不含该事实
    claim_row = (
        await real_db_session.execute(
            text(
                "SELECT claim_id, canonical_key FROM ai_memory_claim "
                "WHERE owner_user_id = :owner AND canonical_key = :key"
            ),
            {"owner": USER_ID, "key": "personal:lifestyle:rt-sup-a"},
        )
    ).mappings().first()
    from app.services.ai.memory.service import MemoryService as CoreMemoryService

    await CoreMemoryService(real_db_session).suppress_claim(
        owner_user_id=USER_ID,
        claim_id=str(claim_row["claim_id"]),
        idempotency_key=f"rt-suppress-{USER_ID}",
    )
    touched = await service.rebuild_dimensions_for_owner(
        USER_ID, trigger_ref="rt-suppress"
    )
    assert touched == len(
        __import__("app.services.ai.memory.consent_producers", fromlist=["x"])
        .CONSENT_PRODUCER_DIMENSIONS
    )
    read = await service.read_active(
        owner_user_id=USER_ID,
        function_key="search",
        purpose="candidate_filter",
        data_category="personal_profile",
    )
    assert read is not None and read["projection_version"] == 2
    claim_ids = [entry["claim_id"] for entry in read["entries"]]
    suppressed_claim_id = str(claim_row["claim_id"])
    assert suppressed_claim_id not in claim_ids, "删除的事实不得再进入投影"
    await real_db_session.commit()


async def test_real_consent_revoked_read_is_zero(
    real_db_session: AsyncSession,
) -> None:
    await _clean_owner(real_db_session, USER_ID)
    await _grant_consent(real_db_session, USER_ID, f"rt-grant-{USER_ID}-e")
    await _seed_confirmed_claim(real_db_session, USER_ID, "consent", "不吃辣")
    await _grant_search_dimension(real_db_session, USER_ID)
    service = _service(real_db_session)
    await service.build(
        owner_user_id=USER_ID,
        function_key="search",
        purpose="candidate_filter",
        data_category="personal_profile",
    )
    assert (
        await service.read_active(
            owner_user_id=USER_ID,
            function_key="search",
            purpose="candidate_filter",
            data_category="personal_profile",
        )
        is not None
    )
    await real_db_session.execute(
        text(
            "UPDATE ai_consent_grant SET revoked_at = UTC_TIMESTAMP() "
            "WHERE user_id = :owner AND scope = 'profile_text_extract' "
            "AND revoked_at IS NULL"
        ),
        {"owner": USER_ID},
    )
    assert (
        await service.read_active(
            owner_user_id=USER_ID,
            function_key="search",
            purpose="candidate_filter",
            data_category="personal_profile",
        )
        is None
    ), "授权撤回后读取必须为 0"
    await real_db_session.commit()


async def test_real_policy_revision_mismatch_fail_closed(
    real_db_session: AsyncSession,
) -> None:
    await _clean_owner(real_db_session, USER_ID)
    await _grant_consent(real_db_session, USER_ID, f"rt-grant-{USER_ID}-f")
    await _seed_confirmed_claim(real_db_session, USER_ID, "policy", "每周健身三次")
    await _grant_search_dimension(real_db_session, USER_ID)
    service = _service(real_db_session)
    await service.build(
        owner_user_id=USER_ID,
        function_key="search",
        purpose="candidate_filter",
        data_category="personal_profile",
    )
    # 回滚演练：制造策略版本不一致（模拟策略回滚后的旧投影）。
    await real_db_session.execute(
        text(
            "UPDATE ai_memory_projection SET policy_revision = 'ai-policy-2020-01-01-v0' "
            "WHERE owner_user_id = :owner AND status = 'active'"
        ),
        {"owner": USER_ID},
    )
    assert (
        await service.read_active(
            owner_user_id=USER_ID,
            function_key="search",
            purpose="candidate_filter",
            data_category="personal_profile",
        )
        is None
    ), "策略版本不一致必须 fail closed"
    # 恢复一致 → 读取恢复（fail closed 不删数据、不需回滚数据库）。
    await real_db_session.execute(
        text(
            "UPDATE ai_memory_projection SET policy_revision = :revision "
            "WHERE owner_user_id = :owner AND status = 'active'"
        ),
        {"owner": USER_ID, "revision": CORE_POLICY_REVISION},
    )
    assert (
        await service.read_active(
            owner_user_id=USER_ID,
            function_key="search",
            purpose="candidate_filter",
            data_category="personal_profile",
        )
        is not None
    )
    await real_db_session.commit()


async def test_real_mode_switch_never_touches_core_ledger(
    real_db_session: AsyncSession, monkeypatch
) -> None:
    await _clean_owner(real_db_session, USER_ID)
    await _grant_consent(real_db_session, USER_ID, f"rt-grant-{USER_ID}-g")
    await _seed_confirmed_claim(real_db_session, USER_ID, "switch", "喜欢古典音乐")
    await _grant_search_dimension(real_db_session, USER_ID)
    await _service(real_db_session).build(
        owner_user_id=USER_ID,
        function_key="search",
        purpose="candidate_filter",
        data_category="personal_profile",
    )
    await real_db_session.commit()

    async def _counts() -> tuple[int, int]:
        row = (
            await real_db_session.execute(
                text(
                    "SELECT (SELECT COUNT(*) FROM ai_memory_event WHERE owner_user_id = :o) AS events, "
                    "(SELECT COUNT(*) FROM ai_memory_claim WHERE owner_user_id = :o) AS claims"
                ),
                {"o": USER_ID},
            )
        ).mappings().first()
        return int(row["events"]), int(row["claims"])

    before = await _counts()

    # legacy 模式：读取分发自定义 loader，不触碰任何记忆表。
    monkeypatch.setattr(settings, "ai_memory_projection_read_mode", "legacy")
    from app.services.ai.features import read_projection_with_mode
    from app.schemas.ai_common import ProjectionKind

    async def _legacy_loader(db):
        return {"fields": {"height_cm": 175}, "source_hash": "legacy"}

    legacy_result = await read_projection_with_mode(
        real_db_session,
        user_id=USER_ID,
        projection_kind=ProjectionKind.PERSONAL_SEARCHABLE,
        legacy_loader=_legacy_loader,
    )
    assert legacy_result == {"fields": {"height_cm": 175}, "source_hash": "legacy"}

    # shadow 模式：双读 + diff 日志，结果仍以 legacy 为准。
    monkeypatch.setattr(settings, "ai_memory_projection_read_mode", "shadow")
    shadow_result = await read_projection_with_mode(
        real_db_session,
        user_id=USER_ID,
        projection_kind=ProjectionKind.PERSONAL_SEARCHABLE,
        legacy_loader=_legacy_loader,
    )
    assert shadow_result == legacy_result

    after = await _counts()
    assert before == after, "模式切换（legacy/shadow）不得修改 Core event/Claim"

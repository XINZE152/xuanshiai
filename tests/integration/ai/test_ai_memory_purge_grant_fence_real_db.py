"""真实库回归：fenced 记忆清理对投影授权的撤销判据。

背景（对抗性审查发现的缺陷 B 变体）：
``revoke → 重新授权`` 时，清理只应撤销**旧代**投影授权，保留新代授权，
否则 ``read_active()`` 的 ``grant.status='active'`` 门会把新代投影一起掐死。
（语义来源见 ``app/services/derivation_outbox.py`` 的围栏注释。）

判据必须用「不属于当前授权快照」比较。本测试锁定两条真实库行为：
1. 同秒内「撤回 + 重新授权」时，新代 grant 必须保持 active（时间比较会漏）。
2. 旧代 grant 必须被撤销（snapshot 不等），不留可读旧代投影。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.ai_common import AiConsentGrantRequest
from app.services.ai.consents import grant_consent, revoke_consent
from app.services.ai.memory.projections import MemoryProjectionService
from app.services.ai.memory.purge import current_owner_sequence, purge_memory_for_owner
from app.services.ai.profile import PROFILE_POLICY_REVISION as CORE_POLICY_REVISION

pytestmark = pytest.mark.asyncio

USER_ID = 9_876_543_901
DIMENSION = {
    "function_key": "search",
    "purpose": "candidate_filter",
    "data_category": "personal_profile",
}


@pytest_asyncio.fixture
async def db(real_db_session: AsyncSession) -> AsyncSession:
    """隔离会话：本用例自建自清该 owner 的授权/投影行。"""

    for table in (
        "ai_memory_projection",
        "ai_memory_projection_grant",
        "ai_memory_owner_sequence",
    ):
        await real_db_session.execute(
            text(f"DELETE FROM {table} WHERE owner_user_id = :u"), {"u": USER_ID}
        )
    for table in ("ai_consent_operation", "ai_consent_grant"):
        await real_db_session.execute(
            text(f"DELETE FROM {table} WHERE user_id = :u"), {"u": USER_ID}
        )
    await real_db_session.commit()
    return real_db_session


async def _privacy_revision(db: AsyncSession) -> int:
    """读取当前 privacy_revision（grant/revoke 都要求它匹配）。"""

    from sqlalchemy import text as _text

    row = (
        await db.execute(
            _text("SELECT privacy_revision FROM user_revision_state WHERE user_id = :u"),
            {"u": USER_ID},
        )
    ).mappings().first()
    return int(row["privacy_revision"]) if row else 0


async def _consent(db: AsyncSession, idem: str) -> None:
    await grant_consent(
        db,
        USER_ID,
        "profile_text_extract",
        AiConsentGrantRequest(
            consent_version="profile-text-v1", policy_revision=CORE_POLICY_REVISION
        ),
        idem,
        await _privacy_revision(db),
    )


async def _active_grant_rows(db: AsyncSession) -> list[dict]:
    rows = (
        await db.execute(
            text(
                "SELECT grant_id, consent_snapshot_id, status FROM ai_memory_projection_grant "
                "WHERE owner_user_id = :owner AND function_key = :fk AND purpose = :p "
                "AND data_category = :dc ORDER BY grant_id"
            ),
            {"owner": USER_ID, "fk": DIMENSION["function_key"], "p": DIMENSION["purpose"], "dc": DIMENSION["data_category"]},
        )
    ).mappings().all()
    return [dict(row) for row in rows]


async def _current_snapshot(db: AsyncSession) -> str:
    """按读路径同口径派生当前 consent 快照（清理侧不再持有该逻辑）。"""

    service = MemoryProjectionService(db, policy_revision=CORE_POLICY_REVISION)
    consent = await service._load_active_consent(USER_ID)
    assert consent is not None
    return service._snapshot_id(consent)


async def test_fenced_purge_keeps_regranted_grant_same_second(db: AsyncSession) -> None:
    """同秒「撤回 → 重新授权」：清理必须保留新代 grant，撤销旧代 grant。"""
    # 授代 1：建立 grant（旧代）。
    await _consent(db, "idem-grant-1")
    await db.commit()
    service = MemoryProjectionService(db, policy_revision=CORE_POLICY_REVISION)
    old_snapshot = await _current_snapshot(db)
    await service.grant(owner_user_id=USER_ID, consent_snapshot_id=old_snapshot, policy_revision=CORE_POLICY_REVISION, **DIMENSION)
    await db.commit()
    assert len(await _active_grant_rows(db)) == 1

    # 记下清理围栏（撤回事务内读取的水位）。
    fence = await current_owner_sequence(db, USER_ID)

    # 撤回授权，紧接着重新授权。
    await revoke_consent(
        db, USER_ID, "profile_text_extract", "idem-revoke-1", await _privacy_revision(db)
    )
    await db.commit()
    await _consent(db, "idem-grant-2")
    await db.commit()
    new_snapshot = await _current_snapshot(db)
    # 同秒撤回+重授权时快照**相同**（granted_at 仅秒级精度）——这正是清理侧
    # 无法用 grant 行判断代际的原因，本断言把它固定为事实。
    assert new_snapshot == old_snapshot, (
        "若无条件相等被打破，说明授权表新增了可区分代际的列，"
        "本测试的结论与 purge 的判据需要重新评估"
    )

    # 新代 grant（upsert 复活同一行）。
    await service.grant(
        owner_user_id=USER_ID,
        consent_snapshot_id=new_snapshot,
        policy_revision=CORE_POLICY_REVISION,
        **DIMENSION,
    )
    await db.commit()

    # 迟到的 fenced 清理：授权表必须**一字不动**（撤销由撤回路径负责）。
    await purge_memory_for_owner(db, USER_ID, scope="memory", fence_seq=fence)
    await db.commit()

    rows_after = await _active_grant_rows(db)
    assert len(rows_after) == 1
    assert rows_after[0]["status"] == "active", (
        "新代投影授权被误撤销：read_active() 的 grant.status='active' 门会一并掐死"
    )
    assert rows_after[0]["consent_snapshot_id"] == new_snapshot


async def test_fenced_purge_leaves_grants_to_revoke_path(db: AsyncSession) -> None:
    """撤回且未重新授权：撤销由撤回路径完成，fenced 清理不动授权表。"""
    await _consent(db, "idem-s1")
    await db.commit()
    service = MemoryProjectionService(db, policy_revision=CORE_POLICY_REVISION)
    snapshot = await _current_snapshot(db)
    await service.grant(
        owner_user_id=USER_ID,
        consent_snapshot_id=snapshot,
        policy_revision=CORE_POLICY_REVISION,
        **DIMENSION,
    )
    await db.commit()

    fence = await current_owner_sequence(db, USER_ID)
    await revoke_consent(
        db, USER_ID, "profile_text_extract", "idem-s2", await _privacy_revision(db)
    )
    await db.commit()

    # 撤回路径已同步撤销该行（同一事务），清理无需也不应重复撤销。
    rows_before_purge = await _active_grant_rows(db)
    assert rows_before_purge and all(
        row["status"] == "revoked" for row in rows_before_purge
    ), "撤回路径必须在撤回事务内同步撤销 active grant（清理不负责此事）"

    stats = await purge_memory_for_owner(db, USER_ID, scope="memory", fence_seq=fence)
    await db.commit()

    rows = await _active_grant_rows(db)
    assert rows and all(row["status"] == "revoked" for row in rows)
    assert stats.grants_revoked == 0, "fenced 清理不得改动授权表（撤销已由撤回路径完成）"


async def test_full_wipe_still_revokes_all_grants(db: AsyncSession) -> None:
    """全量清理（账号注销）：仍撤销全部 active grant，不留可读授权。"""

    await _consent(db, "idem-w1")
    await db.commit()
    service = MemoryProjectionService(db, policy_revision=CORE_POLICY_REVISION)
    snapshot = await _current_snapshot(db)
    await service.grant(
        owner_user_id=USER_ID,
        consent_snapshot_id=snapshot,
        policy_revision=CORE_POLICY_REVISION,
        **DIMENSION,
    )
    await db.commit()
    assert (await _active_grant_rows(db))[0]["status"] == "active"

    before = (
        await db.execute(
            text(
                "SELECT COUNT(*) FROM ai_memory_projection_grant "
                "WHERE owner_user_id = :o AND status = 'active'"
            ),
            {"o": USER_ID},
        )
    ).scalar()

    stats = await purge_memory_for_owner(db, USER_ID, scope="user", fence_seq=None)
    await db.commit()

    rows = await _active_grant_rows(db)
    assert rows and all(row["status"] == "revoked" for row in rows)
    assert stats.grants_revoked == int(before)

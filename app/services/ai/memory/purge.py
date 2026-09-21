"""OWNER 级记忆物理清理（修复清单 §3.17.2，决策 2(a)「删除并忘记」）。

本模块是「删除并忘记」路径的唯一物理清理实现。它**不放在**
``service.py`` / ``ledger.py`` 内，因为这两处被既有测试钉死为
「不得出现 ``INSERT/UPDATE/DELETE ... ai_memory``」（见
``tests/test_ai_memory_service.py`` 与 ``tests/test_ai_memory_ledger.py``）：
账本 append-only 与视图只经 Ledger 写入的纪律不因清理而破坏，清理是
独立的、可审计的治理动作。

围栏口径（**必须理解，否则会误删撤回后新建的记忆**）
------------------------------------------------------
记忆内核没有「同步阶段打 marker」这个半部——它的设计是 event 账本
append-only + 视图行 ``status``，逐表打 marker 需改 6 张表的写入路径。
因此本模块用**序号围栏**代替 marker：

- 撤回/删除发生的那个事务里读取 ``ai_memory_owner_sequence.next_seq``，
  作为 ``fence_seq`` 写进 cleanup 任务 payload（**不是**在清理执行时读取，
  那时已经晚了——期间用户可能已重新授权并写入新记忆）；
- 清理只删除 ``server_seq <= fence_seq``（视图按 ``last_event_seq <=
  fence_seq``），因此撤回后新建的记忆即使清理任务迟到也**不会**被删；
- 有围栏时**保留** ``ai_memory_owner_sequence`` 行：序号继续单调递增，
  幸存的新代行与后续写入的相对顺序不被破坏（若此处归零重编，新事件会拿到
  比幸存行更小的 seq，``ORDER BY server_seq`` 与 ``after_seq`` 分页都会错乱）。

``fence_seq=None`` 表示**全量清理**（账号注销：owner 已不存在，不会再有新行），
此时连同 ``ai_memory_owner_sequence`` 一起删除，序号从头开始——因为没有幸存行，
重新编号不会破坏顺序。

逐表行为（详见 ``docs/api/ai-memory.md`` §7.3）：
    - ``ai_memory_event`` / ``observation`` / ``insight`` / ``state``：
      物理删除（含原始引文与投影内容）。
    - ``ai_memory_claim``：先把 ``value_json`` 置 NULL、``status`` 置
      ``expired``（内容清除真实落库、可审计），随后删除行。
    - ``ai_memory_projection``：有围栏时只删已失效（``status <> 'active'``）
      的投影——撤回的同步半部已把旧代投影置 invalidated，重建的新代投影
      仍是 active，不能删。
    - ``ai_memory_suppression``：保留行（墓碑是「不要再记起来」的依据），
      仅把 ``last_event_id`` / ``last_event_seq`` 清为占位。
    - ``ai_memory_projection_grant``：保留行（审计），``status='revoked'``。
    - ``ai_memory_owner_sequence``：仅全量清理时删除。
    - ``ai_consent_grant``：完全保留（撤回记录本身是合规证据）。

本模块**绝不** commit：调用方（cleanup 任务事务 / ``purge_ai_resources``）
拥有事务与原子性。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ai.audit import emit_ai_metric

logger = logging.getLogger(__name__)

#: 清理计数指标名（只上报行数，绝不含任何用户内容）。
METRIC_MEMORY_PURGE_DELETED = "memory_purge_deleted"
METRIC_MEMORY_PURGE_DRY_RUN = "memory_purge_dry_run"

#: ``ai_memory_claim`` 内容清除后的状态值（schemas/ai_memory.py 的既有状态之一）。
_CLAIM_CLEARED_STATUS = "expired"
#: ``ai_memory_suppression`` 在被引用 event 删除后的安全占位。
_SUPPRESSION_PLACEHOLDER_EVENT_ID = ""
_SUPPRESSION_PLACEHOLDER_EVENT_SEQ = 0


@dataclass(frozen=True)
class MemoryPurgeStats:
    """按表聚合的实际删除/更新计数（绝不含任何用户内容）。"""

    events_deleted: int = 0
    observations_deleted: int = 0
    claims_content_cleared: int = 0
    claims_deleted: int = 0
    insights_deleted: int = 0
    states_deleted: int = 0
    suppressions_scrubbed: int = 0
    projections_deleted: int = 0
    grants_revoked: int = 0
    owner_sequence_deleted: int = 0
    watermark: int = 0
    dry_run: bool = False
    fence_seq: int | None = None

    def as_dict(self) -> dict[str, int]:
        return {
            "events_deleted": self.events_deleted,
            "observations_deleted": self.observations_deleted,
            "claims_content_cleared": self.claims_content_cleared,
            "claims_deleted": self.claims_deleted,
            "insights_deleted": self.insights_deleted,
            "states_deleted": self.states_deleted,
            "suppressions_scrubbed": self.suppressions_scrubbed,
            "projections_deleted": self.projections_deleted,
            "grants_revoked": self.grants_revoked,
            "owner_sequence_deleted": self.owner_sequence_deleted,
            "watermark": self.watermark,
        }

    def total_deleted(self) -> int:
        """内容被真正清除的行数（不含仅改状态的 grant / suppression）。"""
        return (
            self.events_deleted
            + self.observations_deleted
            + self.claims_deleted
            + self.insights_deleted
            + self.states_deleted
            + self.projections_deleted
            + self.owner_sequence_deleted
        )


async def current_owner_sequence(db: AsyncSession, owner_user_id: int) -> int:
    """读取该 owner **已分配的最大序号**，作为清理围栏 ``fence_seq``。

    必须在**撤回事务内**调用：这是撤回时刻的水位。若等到清理任务执行时再读，
    期间用户可能已重新授权并写入新记忆，那些新行会被划进删除范围。

    **返回 ``next_seq - 1``（最后已分配序号），不是 ``next_seq``**：
    ``ai_memory_owner_sequence.next_seq`` 是「下一个待分配」的值（见
    ``MemoryLedger.append``：先用 next_seq 作为事件的 ``server_seq``，随后把它
    递增）。因此直接用 ``next_seq`` 当围栏，会让 ``server_seq <= fence_seq``
    把撤回后写入的**第一条**新事件（它恰好拿到 ``next_seq``）一并删除。

    无行时返回 0，语义是「该 owner 还没有任何记忆事件」，清理随即退化为
    「什么都不删」——这是正确的（没有内容可清），**不是**误判为全量。

    隔离级别说明：用普通 SELECT（不加 ``FOR UPDATE``），不阻塞写入。MySQL 默认
    REPEATABLE READ 下同一事务的快照一致，故「撤回事务提交前并发写入并提交的
    新事件」不在本快照内——其 seq ``> fence_seq``，不会被删除。方向安全：
    少删可重试，误删不可恢复。
    """
    value = await db.scalar(
        text(
            "SELECT next_seq FROM ai_memory_owner_sequence "
            "WHERE owner_user_id = :owner_user_id"
        ),
        {"owner_user_id": int(owner_user_id)},
    )
    if value is None:
        return 0
    return max(int(value) - 1, 0)


def _event_scope(fence_seq: int | None) -> str:
    """账本过滤条件：``server_seq`` 与围栏比较（无围栏为全量）。"""
    return "" if fence_seq is None else " AND server_seq <= :fence_seq"


def _view_scope(fence_seq: int | None) -> str:
    """视图过滤条件：``last_event_seq`` 与围栏比较（无围栏为全量）。"""
    return "" if fence_seq is None else " AND last_event_seq <= :fence_seq"


def _build_params(owner_user_id: int, fence_seq: int | None) -> dict[str, Any]:
    params: dict[str, Any] = {"owner_user_id": int(owner_user_id)}
    if fence_seq is not None:
        params["fence_seq"] = int(fence_seq)
    return params


async def _count(db: AsyncSession, statement: str, params: dict[str, Any]) -> int:
    """``SELECT COUNT(*)``（dry_run 与实删共用同一 WHERE 条件，保证同口径）。"""
    value = await db.scalar(text(statement), params)
    return int(value or 0)


async def _write(db: AsyncSession, statement: str, params: dict[str, Any]) -> int:
    result = await db.execute(text(statement), params)
    return int(getattr(result, "rowcount", 0) or 0)


async def purge_memory_for_owner(
    db: AsyncSession,
    owner_user_id: int,
    *,
    dry_run: bool = False,
    scope: str = "memory",
    fence_seq: int | None = None,
) -> MemoryPurgeStats:
    """OWNER 级记忆物理清理（决策 2(a)「删除并忘记」）。

    ``fence_seq``：撤回事务内读取的序号水位（:func:`current_owner_sequence`）。
    提供时只清理 ``seq <= fence_seq`` 的行（撤回后新建的记忆不受影响，即使清理
    迟到）；为 ``None`` 时全量清理（账号注销），并删除 owner_sequence。

    ``dry_run=True`` 时只统计不写：用与实删**同一 WHERE 条件**的
    ``SELECT COUNT(*)`` 得出同口径计数，``watermark`` 照常读取但不做任何写操作。

    删除顺序：observation → insight → state → claim → projection → event →
    owner_sequence（子表/派生视图在前，账本与序列在最后）；suppression 与
    projection_grant 只做 UPDATE，永不 DELETE。

    绝不 commit（调用方拥有事务）。
    """
    params = _build_params(owner_user_id, fence_seq)
    event_scope = _event_scope(fence_seq)
    view_scope = _view_scope(fence_seq)
    full_wipe = fence_seq is None

    # 水位（= 围栏值本身；全量清理时仅用于统计展示）。
    watermark = int(fence_seq) if fence_seq is not None else await current_owner_sequence(
        db, owner_user_id
    )

    # ---- 统计（dry_run 与实删共用同一组 WHERE 条件）----------------------
    observations = await _count(
        db,
        "SELECT COUNT(*) FROM ai_memory_observation "
        "WHERE owner_user_id = :owner_user_id" + view_scope,
        params,
    )
    insights = await _count(
        db,
        "SELECT COUNT(*) FROM ai_memory_insight "
        "WHERE owner_user_id = :owner_user_id" + view_scope,
        params,
    )
    states = await _count(
        db,
        "SELECT COUNT(*) FROM ai_memory_state "
        "WHERE owner_user_id = :owner_user_id" + view_scope,
        params,
    )
    # 口径：仍有内容或尚未处于清除态的 claim 行数。
    claims_to_clear = await _count(
        db,
        "SELECT COUNT(*) FROM ai_memory_claim "
        "WHERE owner_user_id = :owner_user_id"
        f" AND (value_json IS NOT NULL OR status <> '{_CLAIM_CLEARED_STATUS}')"
        + view_scope,
        params,
    )
    claims = await _count(
        db,
        "SELECT COUNT(*) FROM ai_memory_claim "
        "WHERE owner_user_id = :owner_user_id" + view_scope,
        params,
    )
    # 投影无 event 序号：有围栏时只删已失效的旧代投影，保留 active 的新代投影。
    projection_scope = view_scope if full_wipe else " AND status <> 'active'"
    projections = await _count(
        db,
        "SELECT COUNT(*) FROM ai_memory_projection "
        "WHERE owner_user_id = :owner_user_id" + projection_scope,
        params,
    )
    events = await _count(
        db,
        "SELECT COUNT(*) FROM ai_memory_event "
        "WHERE owner_user_id = :owner_user_id" + event_scope,
        params,
    )
    suppressions = await _count(
        db,
        "SELECT COUNT(*) FROM ai_memory_suppression "
        "WHERE owner_user_id = :owner_user_id "
        f"AND (last_event_id <> '{_SUPPRESSION_PLACEHOLDER_EVENT_ID}' "
        f"OR last_event_seq <> {_SUPPRESSION_PLACEHOLDER_EVENT_SEQ})"
        + view_scope,
        params,
    )
    grants = await _count(
        db,
        "SELECT COUNT(*) FROM ai_memory_projection_grant "
        "WHERE owner_user_id = :owner_user_id AND status = 'active'",
        params,
    )
    owner_sequence = (
        await _count(
            db,
            "SELECT COUNT(*) FROM ai_memory_owner_sequence "
            "WHERE owner_user_id = :owner_user_id",
            params,
        )
        if full_wipe
        else 0
    )

    if dry_run:
        stats = MemoryPurgeStats(
            events_deleted=events,
            observations_deleted=observations,
            claims_content_cleared=claims_to_clear,
            claims_deleted=claims,
            insights_deleted=insights,
            states_deleted=states,
            suppressions_scrubbed=suppressions,
            projections_deleted=projections,
            grants_revoked=grants,
            owner_sequence_deleted=owner_sequence,
            watermark=watermark,
            dry_run=True,
            fence_seq=fence_seq,
        )
        _report_purge(stats, scope=scope)
        return stats

    # ---- 实删：顺序固定，子表/派生视图在前 -------------------------------
    await _write(
        db,
        "DELETE FROM ai_memory_observation "
        "WHERE owner_user_id = :owner_user_id" + view_scope,
        params,
    )
    await _write(
        db,
        "DELETE FROM ai_memory_insight "
        "WHERE owner_user_id = :owner_user_id" + view_scope,
        params,
    )
    await _write(
        db,
        "DELETE FROM ai_memory_state "
        "WHERE owner_user_id = :owner_user_id" + view_scope,
        params,
    )
    # 内容清除真实落库（先清内容，再删行）：可审计「内容确已被清除」。
    await _write(
        db,
        "UPDATE ai_memory_claim SET value_json = NULL, "
        f"status = '{_CLAIM_CLEARED_STATUS}' "
        "WHERE owner_user_id = :owner_user_id" + view_scope,
        params,
    )
    await _write(
        db,
        "DELETE FROM ai_memory_claim "
        "WHERE owner_user_id = :owner_user_id" + view_scope,
        params,
    )
    await _write(
        db,
        "DELETE FROM ai_memory_projection "
        "WHERE owner_user_id = :owner_user_id" + projection_scope,
        params,
    )
    # 墓碑保留，但把指向 event 的引用清为占位（被引用行即将删除）。
    await _write(
        db,
        "UPDATE ai_memory_suppression SET "
        f"last_event_id = '{_SUPPRESSION_PLACEHOLDER_EVENT_ID}', "
        f"last_event_seq = {_SUPPRESSION_PLACEHOLDER_EVENT_SEQ} "
        "WHERE owner_user_id = :owner_user_id "
        f"AND (last_event_id <> '{_SUPPRESSION_PLACEHOLDER_EVENT_ID}' "
        f"OR last_event_seq <> {_SUPPRESSION_PLACEHOLDER_EVENT_SEQ})"
        + view_scope,
        params,
    )
    # 授权保留审计：置 revoked（幂等，重复执行只影响仍 active 的行）。
    await _write(
        db,
        "UPDATE ai_memory_projection_grant SET status = 'revoked', "
        "revoked_at = COALESCE(revoked_at, UTC_TIMESTAMP()) "
        "WHERE owner_user_id = :owner_user_id AND status = 'active'",
        params,
    )
    # 账本最后删除（append-only 纪律只在正常写入路径生效，清理是治理动作）。
    await _write(
        db,
        "DELETE FROM ai_memory_event "
        "WHERE owner_user_id = :owner_user_id" + event_scope,
        params,
    )
    # 仅全量清理删除水位：有围栏时必须保留，否则新事件会拿到比幸存行更小的
    # seq，破坏 ORDER BY server_seq 与 after_seq 分页。
    if full_wipe:
        await _write(
            db,
            "DELETE FROM ai_memory_owner_sequence WHERE owner_user_id = :owner_user_id",
            params,
        )

    stats = MemoryPurgeStats(
        events_deleted=events,
        observations_deleted=observations,
        claims_content_cleared=claims_to_clear,
        claims_deleted=claims,
        insights_deleted=insights,
        states_deleted=states,
        suppressions_scrubbed=suppressions,
        projections_deleted=projections,
        grants_revoked=grants,
        owner_sequence_deleted=owner_sequence,
        watermark=watermark,
        dry_run=False,
        fence_seq=fence_seq,
    )
    _report_purge(stats, scope=scope)
    return stats


def _report_purge(stats: MemoryPurgeStats, *, scope: str) -> None:
    """上报逐表计数（指标 + 结构化日志），任何情况下不含用户内容。

    dry-run 与实删使用**不同**指标名：运维按 ``memory_purge_dry_run`` 可
    区分「只统计」与「真删除」，避免把预检数字误读成已删除量。日志只含
    行数、表名与 scope，绝不含 value / quote / 任何原文。
    """
    metric = (
        METRIC_MEMORY_PURGE_DRY_RUN if stats.dry_run else METRIC_MEMORY_PURGE_DELETED
    )
    emit_ai_metric(
        metric,
        float(stats.total_deleted()),
        {"scope": str(scope), "fenced": "false" if stats.fence_seq is None else "true"},
    )
    logger.info(
        "memory_purge_%s scope=%s fenced=%s watermark=%s counts=%s",
        "dry_run" if stats.dry_run else "deleted",
        scope,
        stats.fence_seq is not None,
        stats.watermark,
        stats.as_dict(),
    )

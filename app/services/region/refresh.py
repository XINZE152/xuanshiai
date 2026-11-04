"""属地批量刷新：候选筛选 → 清洗去重 → 解析 → 分批回写 → 统计。

**本模块是唯一允许出现属地写库 SQL 的地方**。路由层 ``_refresh_region`` 只做门控与
结果组装，从而保持既有断言「干跑阶段源码中不得出现 UPDATE users SET ip_region」。

三条必须保留的实现约束：

1. ``updated_at = updated_at``：``users.updated_at`` 带 ``ON UPDATE CURRENT_TIMESTAMP``，
   不自赋值会把全表时间刷成同一时刻，污染列表排序与运营判断；
2. ``updated`` 取**实际变更行数**：MySQL 对"新值等于旧值"不计 affected rows，
   因此重复刷新出现 ``scanned>0, updated=0`` 是正常现象，不是失败；
3. 默认只填空：目标列非空的行不参与回写；``overwrite=True`` 才覆盖，且必须留存旧值快照。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.region import registry
from app.services.region.types import RegionDataNotReady, RegionKind


class OverwriteTooLarge(RuntimeError):
    """overwrite 命中的行数超过单次上限，要求改用脚本分批执行。"""


@dataclass
class RefreshStats:
    scanned: int = 0
    updated: int = 0
    skipped: int = 0
    unknown: int = 0
    elapsed_ms: int = 0
    overwritten: list[dict[str, Any]] = field(default_factory=list)


async def _user_columns(db: AsyncSession) -> set[str]:
    rows = await db.execute(
        text(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS"
            " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users'"
        )
    )
    return {str(row[0]) for row in rows.all()}


def _candidate_sql(
    *, target: str, source: str, scope_sql: str, include_empty_only: bool, has_deleted_at: bool
) -> str:
    sql = (
        f"SELECT users.id AS id, users.{source} AS source_value, users.{target} AS old_region"
        f" FROM users WHERE ({scope_sql})"
    )
    if has_deleted_at:
        sql += " AND users.deleted_at IS NULL"
    sql += f" AND users.{source} IS NOT NULL AND users.{source} <> ''"
    if include_empty_only:
        sql += f" AND (users.{target} IS NULL OR users.{target} = '')"
    return sql


def _update_sql(*, target: str, include_empty_only: bool) -> str:
    sql = f"UPDATE users SET {target} = :region, updated_at = updated_at WHERE id = :user_id"
    if include_empty_only:
        sql += f" AND ({target} IS NULL OR {target} = '')"
    return sql


async def count_candidates(
    db: AsyncSession, *, kind: RegionKind, scope_sql: str, params: dict[str, Any], include_empty_only: bool
) -> int:
    """干跑（mock）与就绪检查共用：只统计候选中行数，不解析、不写库。"""
    columns = await _user_columns(db)
    if kind.column not in columns or kind.source_column not in columns:
        raise RegionDataNotReady(f"users.{kind.column} / users.{kind.source_column} 列未就绪")
    sql = _candidate_sql(
        target=kind.column,
        source=kind.source_column,
        scope_sql=scope_sql,
        include_empty_only=include_empty_only,
        has_deleted_at="deleted_at" in columns,
    )
    return int((await db.execute(text(f"SELECT COUNT(*) FROM ({sql}) AS candidates"), params)).scalar() or 0)


async def refresh(
    db: AsyncSession,
    *,
    kind: RegionKind,
    scope_sql: str,
    params: dict[str, Any],
    batch_size: int,
    overwrite: bool,
    max_overwrite_rows: int,
) -> RefreshStats:
    started = time.monotonic()
    columns = await _user_columns(db)
    target, source = kind.column, kind.source_column
    if target not in columns or source not in columns:
        raise RegionDataNotReady(f"users.{target} / users.{source} 列未就绪：请先执行属地列迁移")

    rows = await db.execute(
        text(
            _candidate_sql(
                target=target,
                source=source,
                scope_sql=scope_sql,
                include_empty_only=not overwrite,
                has_deleted_at="deleted_at" in columns,
            )
        ),
        params,
    )
    records = rows.mappings().all()
    if overwrite and len(records) > max_overwrite_rows:
        raise OverwriteTooLarge(
            f"overwrite 命中 {len(records)} 行，超过单次上限 {max_overwrite_rows}；请改用脚本分批执行"
        )

    outcome = registry.resolve(kind, [str(record["source_value"]) for record in records])

    updates: list[dict[str, Any]] = []
    overwritten: list[dict[str, Any]] = []
    for record in records:
        source_value = str(record["source_value"])
        region = outcome.resolved.get(source_value)
        if region is None:
            continue
        user_id = int(record["id"])
        updates.append({"region": region, "user_id": user_id})
        old_region = record["old_region"]
        if old_region != region:
            overwritten.append({"user_id": user_id, "old": old_region, "new": region})

    affected_total = 0
    if updates:
        statement = text(_update_sql(target=target, include_empty_only=not overwrite))
        size = max(1, int(batch_size))
        for offset in range(0, len(updates), size):
            result = await db.execute(statement, updates[offset : offset + size])
            affected = getattr(result, "rowcount", -1)
            if isinstance(affected, int) and affected > 0:
                affected_total += affected
        await db.commit()

    # ``updated`` 的业务语义是「属地值真的变了的行数」。
    # 不能用 rowcount 单独作准：``updated_at = updated_at`` 这种显式自赋值会让 MySQL 仍把该行
    # 计为 affected（overwrite 模式下会把「值完全相同」的重写也统计进来）；
    # 也不能只用 overwritten 长度：并发下目标列可能已被他人填好，实际未写成功。
    # 取两者较小值，两种偏差同时收敛。
    updated = min(affected_total, len(overwritten))

    return RefreshStats(
        scanned=len(records),
        updated=updated,
        skipped=outcome.skipped,
        unknown=outcome.unknown,
        elapsed_ms=int((time.monotonic() - started) * 1000),
        overwritten=overwritten,
    )


async def write_audit(
    db: AsyncSession,
    *,
    actor_id: int,
    kind: RegionKind,
    stats: RefreshStats,
    overwrite: bool,
    scope_label: str,
) -> None:
    """把本次刷新写入 ``business_audit_log``。

    - 有变化明细（覆盖或只填空）：按 500 行切片写 ``before_json`` / ``after_json``，
      记录受影响 user_id 与新旧值，支持按 id 回滚；
    - 无变化明细（没有行被改写）：写一条汇总。

    旧值/新值快照是批量刷新的可回溯保障，不能只落应用日志。
    """
    action = f"member.region.refresh_{kind.value}"
    label = "IP" if kind is RegionKind.IP else "手机"
    statement = text(
        "INSERT INTO business_audit_log"
        " (actor_user_id, action, resource_type, resource_id, before_json, after_json, reason)"
        " VALUES (:actor, :action, 'users', NULL, :before, :after, :reason)"
    )
    if stats.overwritten:
        size = 500
        mode_label = "覆盖" if overwrite else "只填空"
        for offset in range(0, len(stats.overwritten), size):
            chunk = stats.overwritten[offset : offset + size]
            before = [{"user_id": row["user_id"], "old": row["old"]} for row in chunk]
            after = [{"user_id": row["user_id"], "new": row["new"]} for row in chunk]
            await db.execute(
                statement,
                {
                    "actor": actor_id,
                    "action": action,
                    "before": json.dumps(before, ensure_ascii=False),
                    "after": json.dumps(after, ensure_ascii=False),
                    "reason": f"一键刷新{label}属地（{mode_label}，第 {offset // size + 1} 批）"[:255],
                },
            )
    else:
        await db.execute(
            statement,
            {
                "actor": actor_id,
                "action": action,
                "before": json.dumps(
                    {"mode": "overwrite" if overwrite else "fill-empty"}, ensure_ascii=False
                ),
                "after": json.dumps(
                    {
                        "scanned": stats.scanned,
                        "updated": stats.updated,
                        "skipped": stats.skipped,
                        "unknown": stats.unknown,
                        "scope": scope_label,
                        "overwrite": overwrite,
                    },
                    ensure_ascii=False,
                ),
                "reason": (
                    f"一键刷新{label}属地（{'覆盖' if overwrite else '只填空'}，"
                    f"扫描 {stats.scanned} / 更新 {stats.updated}）"
                )[:255],
            },
        )
    await db.commit()

"""牵线超时自动失败扫描（tools_match_timeout 配置域的消费方）。

需求（对标站齿轮抽屉）：每天 0 点把「待牵线 / 牵线中」且自发起时间起超过 ``hours``
小时仍未完成的牵线记录，自动置为「牵线失败」（``match_apply.status = 2``），
操作人记为「后台-管理员-admin」（自动任务，``business_audit_log.actor_user_id`` 为空），
失败原因写「超出时间未完成牵线，次数已退回」。

实现口径（与真实数据模型对齐，2026-10-28 核验）：
- ``match_apply`` 只有 status（0 待处理 / 1 同意 / 2 拒绝=牵线失败 / 3 已过期），
  「待牵线 / 牵线中」都是 status=0，仅按是否已归属服务红娘区分展示，故超时扫描只取 status=0。
- 后端不存在「牵线服务次数」的持久余额（发起牵线走每日 Redis 额度，拒绝 / 后台标记失败
  均不做余额入账），因此这里**不伪造次数退回**——仅置失败 + 写审计。审计文案沿用产品口径
  「超出时间未完成牵线，次数已退回」，实际不含余额入账，详见 docs/api 文档。
- 时区：created_at 由 MySQL CURRENT_TIMESTAMP / UTC_TIMESTAMP() 写入，属 UTC 时间；
  截止时间用 SQL 侧 DATE_SUB(UTC_TIMESTAMP(), INTERVAL :hours HOUR) 与列保持同一时钟。

调度：全项目无内置调度框架，本服务由外部 cron 每日 0 点调用
``scripts/run_match_timeout_sweep.py``（见该脚本 docstring）。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.admin_config import get_runtime_value

logger = logging.getLogger(__name__)

NAMESPACE = "tools_match_timeout"
# 产品口径的固定失败原因（写入审计 reason 列，供后台操作日志与前端展示）。
TIMEOUT_REASON = "超出时间未完成牵线，次数已退回"
# hours 兜底：配置缺失/非法时回落到 48（与 DEFAULT_CONFIGS 注册值一致）。
DEFAULT_HOURS = 48


async def sweep_match_timeouts(db: AsyncSession) -> dict[str, Any]:
    """扫描并置超时牵线为失败；返回 {enabled, hours, affected}。

    关闭态（enabled 非真）直接返回 affected=0，不触碰任何行。
    """
    enabled = bool(await get_runtime_value(db, NAMESPACE, "enabled", False))
    if not enabled:
        return {"enabled": False, "hours": None, "affected": 0}

    raw_hours = await get_runtime_value(db, NAMESPACE, "hours", DEFAULT_HOURS)
    try:
        hours = int(raw_hours)
    except (TypeError, ValueError):
        hours = DEFAULT_HOURS
    if hours <= 0:
        # 非正时长视为无效配置，跳过本轮，避免把所有待处理记录瞬间置失败。
        logger.warning("tools_match_timeout.hours 非法（%r），本轮跳过", raw_hours)
        return {"enabled": True, "hours": hours, "affected": 0}

    # 先取待置失败的 id，便于逐条写审计（actor_user_id 为空 = 系统自动任务）。
    rows = (
        await db.execute(
            text(
                "SELECT a.id FROM match_apply a"
                " WHERE a.status = 0"
                " AND a.created_at < DATE_SUB(UTC_TIMESTAMP(), INTERVAL :hours HOUR)"
            ),
            {"hours": hours},
        )
    ).mappings().all()
    ids = [int(r["id"]) for r in rows]
    if not ids:
        return {"enabled": True, "hours": hours, "affected": 0}

    result = await db.execute(
        text(
            "UPDATE match_apply"
            " SET status = 2, responded_at = COALESCE(responded_at, UTC_TIMESTAMP()), updated_at = UTC_TIMESTAMP()"
            " WHERE id IN :ids AND status = 0"
        ).bindparams(bindparam("ids", expanding=True)),
        {"ids": ids},
    )
    affected = int(result.rowcount or 0)

    for rid in ids:
        await db.execute(
            text(
                "INSERT INTO business_audit_log"
                " (actor_user_id, action, resource_type, resource_id, reason)"
                " VALUES (NULL, 'match_record.timeout_fail', 'match_apply', :id, :reason)"
            ),
            {"id": rid, "reason": TIMEOUT_REASON},
        )
    await db.commit()
    logger.info("牵线超时扫描完成：enabled=%s hours=%s 置失败 %s 条", enabled, hours, affected)
    return {"enabled": True, "hours": hours, "affected": affected}

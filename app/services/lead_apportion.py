"""客源线索自动分派引擎（消费分派配置 strategy 选红娘）。

背景：`matchmaker_apportion_config`（scope=config_type=assign/abandon）此前只做
配置 CRUD（见 `apportion_config_admin.py`），全项目没有任何代码消费 strategy 去选
红娘。本模块补上"读配置 → 选一个有效服务红娘"的服务端实现，供建单自动分派与
单条重算端点复用。

本期落地策略（与产品确认，2026-10-28）：
- designated        → 用配置的 target_matchmaker_id（须为有效服务红娘）；
- round_robin_random→ 在"前台展示且非锁定"的服务红娘池里纯随机选一人（无状态）；
- 其它（by_region / by_promoter / by_partner / none / 未配置 / 停用 / 池为空）
  → 返回 None，即"选不到人保持未分派"，由上层写 matchmaker_id=NULL 且 assigned=false。

说明：customer_lead 表无省/市/现居地字段、无 partner 字段，by_region/by_partner 缺
数据源；by_promoter 的推广人未必是有效服务红娘，写进 matchmaker_id 会违反归属校验。
故这三档本期一律走"未分派"，与 `apportion_config_admin.upsert_assign` 对 customer_lead
拒绝这些 strategy 的既有口径一致。真轮询（公平轮转）需持久游标，本期用纯随机替代。
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# 有效服务红娘池：申请通过 + 角色有效，且红娘档案前台展示(visible=1)、非锁定
# (locked=0)、未软删。缺档案行按"未展示"处理，宁缺毋滥。
_POOL_SQL = """SELECT a.user_id
    FROM user_matchmaker_apply a
    JOIN user_role r ON r.user_id = a.user_id AND r.role_code = 'service_matchmaker' AND r.status = 1
    JOIN matchmaker_profile p ON p.user_id = a.user_id
        AND p.visible = 1 AND p.locked = 0 AND p.deleted_at IS NULL
    WHERE a.application_type = 'service_matchmaker' AND a.status = 1"""

_VALID_SERVICE_MATCHMAKER_SQL = """SELECT 1 FROM user_matchmaker_apply a
    JOIN user_role r ON r.user_id = a.user_id AND r.role_code = 'service_matchmaker' AND r.status = 1
    WHERE a.user_id = :id AND a.application_type = 'service_matchmaker' AND a.status = 1
    LIMIT 1"""


async def _load_assign_config(db: AsyncSession) -> dict[str, Any] | None:
    """读取 customer_lead/assign 配置行；不存在返回 None。"""
    row = (
        await db.execute(
            text(
                """SELECT strategy, target_matchmaker_id, is_enabled
                     FROM matchmaker_apportion_config
                    WHERE scope = 'customer_lead' AND config_type = 'assign'"""
            )
        )
    ).mappings().first()
    return dict(row) if row else None


async def _is_valid_service_matchmaker(db: AsyncSession, user_id: int) -> bool:
    return bool((await db.execute(text(_VALID_SERVICE_MATCHMAKER_SQL), {"id": user_id})).scalar())


async def _pick_random_pool_matchmaker(db: AsyncSession) -> int | None:
    """从前台展示且非锁定的服务红娘池纯随机选一人；池为空返回 None。"""
    return (
        await db.execute(text(f"{_POOL_SQL} ORDER BY RAND() LIMIT 1"))
    ).scalar()


async def resolve_assigned_matchmaker(db: AsyncSession, *, auto_assign: bool) -> int | None:
    """按分派配置选出一个归属红娘 user_id；未开启/未配置/选不到人返回 None。

    上层据此在建单事务内写 matchmaker_id（None 则不写，保持未分派）。
    """
    if not auto_assign:
        return None
    config = await _load_assign_config(db)
    if not config or int(config.get("is_enabled") or 0) != 1:
        return None
    strategy = config.get("strategy")
    if strategy == "designated":
        target = config.get("target_matchmaker_id")
        if target is not None and await _is_valid_service_matchmaker(db, int(target)):
            return int(target)
        return None
    if strategy == "round_robin_random":
        return await _pick_random_pool_matchmaker(db)
    return None

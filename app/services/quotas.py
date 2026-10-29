import json
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.redis import daily_quota_key, get_daily_used
from app.schemas.quotas import QuotaItem, QuotaSummary
from app.services.admin_config import get_runtime_value
from app.services.membership import get_active_membership_row
from app.services.runtime_config import match_line_permissions


async def _membership_rights(db: AsyncSession, user_id: int) -> tuple[bool, dict]:
    row = await get_active_membership_row(db, user_id)
    if not row:
        return False, {}
    value = row["rights"]
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = {}
    return True, value if isinstance(value, dict) else {}


async def _extra_remaining(db: AsyncSession, user_id: int, quota_code: str) -> int:
    result = await db.execute(text("SELECT COALESCE(SUM(remaining),0) FROM user_quota_grant WHERE user_id=:user_id AND quota_code=:quota_code AND (expires_at IS NULL OR expires_at>UTC_TIMESTAMP())"), {"user_id": user_id, "quota_code": quota_code})
    return int(result.scalar() or 0)


async def consume_extra(db: AsyncSession, user_id: int, quota_code: str, reason: str, target_user_id: int | None = None) -> bool:
    result = await db.execute(text("SELECT id FROM user_quota_grant WHERE user_id=:user_id AND quota_code=:quota_code AND remaining>0 AND (expires_at IS NULL OR expires_at>UTC_TIMESTAMP()) ORDER BY expires_at IS NULL, expires_at, id LIMIT 1 FOR UPDATE"), {"user_id": user_id, "quota_code": quota_code})
    row = result.first()
    if not row:
        return False
    await db.execute(text("UPDATE user_quota_grant SET remaining=remaining-1 WHERE id=:id"), {"id": row[0]})
    await db.execute(text("INSERT INTO user_quota_usage (user_id,quota_code,quota_date,source,reason,target_user_id) VALUES (:user_id,:quota_code,:quota_date,'points',:reason,:target_user_id)"), {"user_id": user_id, "quota_code": quota_code, "quota_date": date.today(), "reason": reason, "target_user_id": target_user_id})
    return True


async def grant(db: AsyncSession, user_id: int, quota_code: str, amount: int, source: str, order_no: str) -> None:
    await db.execute(text("INSERT INTO user_quota_grant (user_id,quota_code,remaining,source,order_no) VALUES (:user_id,:quota_code,:remaining,:source,:order_no)"), {"user_id": user_id, "quota_code": quota_code, "remaining": amount, "source": source, "order_no": order_no})


async def resolve_browse_limit(
    db: AsyncSession, user_id: int, is_vip: bool | None = None
) -> int:
    """每日可查看的**不同用户**上限。

    会员读套餐权益 ``rights.browse_daily_limit``（缺键回退 20），非会员读平台
    权限快照 ``free_browse_daily_limit``。``discovery._quota_limit`` 与
    ``/users/me/quotas`` 共用此函数，避免"接口报 20、实际只给 8"的分叉。
    """
    vip, rights = await _membership_rights(db, user_id)
    if is_vip is not None:
        vip = is_vip
    if vip:
        value = rights.get("browse_daily_limit")
        return max(0, int(value)) if value is not None else 20
    return int(
        await get_runtime_value(
            db,
            "platform_permissions",
            "free_browse_daily_limit",
            settings.browse_daily_limit,
        )
    )


async def resolve_apply_limit(db: AsyncSession, user_id: int, is_vip: bool | None = None) -> int:
    """每日「申请认识」上限（唯一实现）。

    来源优先级（与原 ``discovery._consume_apply_quota`` 逐字一致）：

    1. 后台卡片4「牵线次数」统一值 ``apply_daily > 0`` —— 覆盖 VIP/免费两套旧限额；
    2. 平台权限快照 ``free_apply_daily_limit``；
    3. VIP 且存在有效会员：``free_apply_daily_limit + rights.apply_bonus``。

    说明：``vip_apply_daily_limit``（默认 10）在当前口径下**不参与计算** —— VIP 走的是
    "免费额度 + 套餐加次"，这是历史行为，本次只做"单一来源"收敛，不改变数值。
    """
    vip = is_vip
    rights: dict = {}
    # 只在"未知是否会员"或"已确认是会员"时才查会员行（非会员不需要 apply_bonus）；
    # 显式传入 is_vip=False 时省掉一次查询，扣次链路少一次往返。
    if vip is None or vip:
        member_vip, member_rights = await _membership_rights(db, user_id)
        if vip is None:
            vip = member_vip
        rights = member_rights if member_vip else {}
    line_perm = await match_line_permissions(db)
    unified = line_perm.get("apply_daily")
    if unified and int(unified) > 0:
        return int(unified)
    free_limit = int(
        await get_runtime_value(
            db,
            "platform_permissions",
            "free_apply_daily_limit",
            settings.apply_daily_free_limit,
        )
    )
    if not vip:
        return free_limit
    return free_limit + int(rights.get("apply_bonus") or 0)


async def resolve_superlike_limit(db: AsyncSession, user_id: int, is_vip: bool | None = None) -> int:
    """每日「爆灯」上限（唯一实现）：平台权限快照 > settings 默认。"""
    if is_vip is None:
        vip, _ = await _membership_rights(db, user_id)
    else:
        vip = is_vip
    return int(
        await get_runtime_value(
            db,
            "platform_permissions",
            "vip_superlike_daily_limit" if vip else "free_superlike_daily_limit",
            settings.superlike_daily_vip_limit if vip else settings.superlike_daily_free_limit,
        )
    )


async def resolve_paper_plane_limit(db: AsyncSession) -> int:
    """每日「纸飞机」上限（唯一实现）：平台权限快照 > settings 默认。

    此前 ``community.create_paper_plane`` 写死 ``consume_daily(key, 3)``，
    后台改 ``paper_plane_daily_limit`` 只影响展示、不影响扣次 —— 本函数把两者统一。
    """
    return int(
        await get_runtime_value(
            db,
            "platform_permissions",
            "paper_plane_daily_limit",
            settings.paper_plane_daily_limit,
        )
    )


async def summary(db: AsyncSession, user_id: int) -> QuotaSummary:
    vip, _ = await _membership_rights(db, user_id)
    definitions = (
        ("apply", await resolve_apply_limit(db, user_id, is_vip=vip), "apply"),
        ("browse", await resolve_browse_limit(db, user_id, is_vip=vip), "browse"),
        ("superlike", await resolve_superlike_limit(db, user_id, is_vip=vip), "superlike"),
        ("paper_plane", await resolve_paper_plane_limit(db), "paper_plane"),
    )
    items: list[QuotaItem] = []
    for quota_code, daily_limit, redis_code in definitions:
        key = daily_quota_key(f"discovery:{redis_code}", user_id)
        if quota_code == "paper_plane":
            key = daily_quota_key("paper-plane", user_id)
        used = await get_daily_used(key)
        daily_remaining = max(0, daily_limit - used)
        extra = await _extra_remaining(db, user_id, quota_code)
        items.append(QuotaItem(quota_code=quota_code, daily_limit=daily_limit, daily_used=used, daily_remaining=daily_remaining, extra_remaining=extra, total_remaining=daily_remaining + extra))
    return QuotaSummary(items=items)

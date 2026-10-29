"""type=1 会员履约。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.payment.fulfillment.base import FulfillmentError


class MembershipFulfillmentHandler:
    """会员开通/续费与退款回收。

    幂等依赖 ``user_membership.uk_user_membership_order_no``：
    同一订单重复执行只会命中唯一键，不会重复开通。
    """

    order_type = 1

    async def fulfill(self, db: AsyncSession, order: Mapping[str, Any]) -> dict[str, Any]:
        package = await _load_package(db, order)
        if not package:
            raise FulfillmentError("会员套餐不存在，无法开通")

        now = datetime.now(UTC).replace(tzinfo=None)
        active = (
            await db.execute(
                text(
                    """SELECT end_at FROM user_membership
                       WHERE user_id = :user_id AND status = 1
                         AND (end_at IS NULL OR end_at > UTC_TIMESTAMP())
                       ORDER BY end_at DESC, id DESC
                       LIMIT 1"""
                ),
                {"user_id": order["user_id"]},
            )
        ).mappings().first()

        # 续费叠加：当前会员未到期时从原到期时间继续；否则从现在开始。
        start_at = now
        if active and active["end_at"]:
            active_end = active["end_at"]
            if active_end.tzinfo is not None:
                active_end = active_end.astimezone(UTC).replace(tzinfo=None)
            if active_end > now:
                start_at = active_end
        end_at = start_at + timedelta(days=int(package["duration_days"]))

        await db.execute(
            text(
                """INSERT INTO user_membership
                   (user_id, package_type, amount, order_no, start_at, end_at, status)
                   VALUES (:user_id, :package_type, :amount, :order_no,
                           :start_at, :end_at, 1)
                   ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)"""
            ),
            {
                "user_id": order["user_id"],
                "package_type": package["code"],
                "amount": order["amount"],
                "order_no": order["order_no"],
                "start_at": start_at,
                "end_at": end_at,
            },
        )
        membership = (
            await db.execute(
                text(
                    """SELECT id, start_at, end_at FROM user_membership
                       WHERE order_no = :order_no LIMIT 1"""
                ),
                {"order_no": order["order_no"]},
            )
        ).mappings().first()
        if not membership:
            raise FulfillmentError("会员记录写入失败")

        return {
            "fulfilled": True,
            "membership_id": int(membership["id"]),
            "start_at": membership["start_at"],
            "end_at": membership["end_at"],
        }

    async def revoke(
        self,
        db: AsyncSession,
        order: Mapping[str, Any],
        refund: Mapping[str, Any],
    ) -> None:
        # 只回收该订单对应的会员记录；其他叠加订单不受影响。
        await db.execute(
            text(
                """UPDATE user_membership
                   SET status = 3, updated_at = UTC_TIMESTAMP()
                   WHERE order_no = :order_no AND status = 1"""
            ),
            {"order_no": order["order_no"]},
        )


async def _load_package(
    db: AsyncSession,
    order: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    """按套餐编码查套餐；兼容历史订单把数字 ID 写进 product_type 的情况。"""
    code = str(order.get("product_type") or "").strip()
    if code:
        row = (
            await db.execute(
                text(
                    """SELECT code, duration_days
                       FROM config_membership_package
                       WHERE code = :code LIMIT 1"""
                ),
                {"code": code},
            )
        ).mappings().first()
        if row:
            return row

    name = str(order.get("product_name") or "").strip()
    if name:
        return (
            await db.execute(
                text(
                    """SELECT code, duration_days
                       FROM config_membership_package
                       WHERE name = :name LIMIT 1"""
                ),
                {"name": name},
            )
        ).mappings().first()
    return None

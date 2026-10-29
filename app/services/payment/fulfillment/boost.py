"""type=4 置顶履约。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.notifications import emit_notification
from app.services.payment.fulfillment.base import FulfillmentError
# 置顶套餐天数目前是代码常量；复用旧模块，避免两份天数口径漂移。
from app.services.payments import BOOST_PACKAGES


class BoostFulfillmentHandler:
    """置顶权益发放与退款回收。

    幂等依赖 ``user_boost.uk_user_boost_order_no``。
    """

    order_type = 4

    async def fulfill(self, db: AsyncSession, order: Mapping[str, Any]) -> dict[str, Any]:
        package = BOOST_PACKAGES.get(str(order.get("product_type") or ""))
        if package is None:
            raise FulfillmentError("置顶套餐不存在，无法发放权益")

        now = datetime.now(UTC).replace(tzinfo=None)
        end_at = now + timedelta(days=int(package.days))
        await db.execute(
            text(
                """INSERT INTO user_boost
                   (user_id, target_user_id, amount, order_no, start_at, end_at, status)
                   VALUES (:user_id, :user_id, :amount, :order_no,
                           :start_at, :end_at, 1)
                   ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)"""
            ),
            {
                "user_id": order["user_id"],
                "amount": order["amount"],
                "order_no": order["order_no"],
                "start_at": now,
                "end_at": end_at,
            },
        )

        await emit_notification(
            db,
            recipient_user_id=int(order["user_id"]),
            actor_user_id=None,
            event_type="system",
            title="置顶已生效",
            content=f"你的置顶权益已生效，有效期至 {end_at:%Y-%m-%d %H:%M}。",
            target_type="payment_order",
            target_id=int(order["id"]),
        )

        boost = (
            await db.execute(
                text(
                    """SELECT id FROM user_boost
                       WHERE order_no = :order_no LIMIT 1"""
                ),
                {"order_no": order["order_no"]},
            )
        ).scalar()
        return {
            "fulfilled": True,
            "boost_id": int(boost) if boost is not None else None,
            "end_at": end_at,
        }

    async def revoke(
        self,
        db: AsyncSession,
        order: Mapping[str, Any],
        refund: Mapping[str, Any],
    ) -> None:
        await db.execute(
            text(
                """UPDATE user_boost SET status = 3
                   WHERE order_no = :order_no AND status = 1"""
            ),
            {"order_no": order["order_no"]},
        )

"""type=2 爆灯履约。"""

from __future__ import annotations

from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.notifications import emit_notification
from app.services.payment.fulfillment.base import FulfillmentError


class SpotlightFulfillmentHandler:
    """爆灯权益发放与退款回收。

    幂等依赖 ``user_boost.uk_user_boost_order_no``。
    """

    order_type = 2

    async def fulfill(self, db: AsyncSession, order: Mapping[str, Any]) -> dict[str, Any]:
        target_user_id = order.get("product_id")
        if target_user_id is None:
            raise FulfillmentError("爆灯订单缺少目标用户")
        target_user_id = int(target_user_id)
        if target_user_id == int(order["user_id"]):
            raise FulfillmentError("不能给自己发爆灯")

        await db.execute(
            text(
                """INSERT INTO user_boost
                   (user_id, target_user_id, amount, order_no, start_at, end_at, status)
                   VALUES (:user_id, :target_user_id, :amount, :order_no,
                           UTC_TIMESTAMP(), DATE_ADD(UTC_TIMESTAMP(), INTERVAL 1 DAY), 1)
                   ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)"""
            ),
            {
                "user_id": order["user_id"],
                "target_user_id": target_user_id,
                "amount": order["amount"],
                "order_no": order["order_no"],
            },
        )

        # 通知失败不影响权益发放；emit_notification 自身会处理拉黑/偏好。
        await emit_notification(
            db,
            recipient_user_id=target_user_id,
            actor_user_id=int(order["user_id"]),
            event_type="superlike",
            title="收到爆灯",
            content="有人对你发出了爆灯信号",
            target_type="user",
            target_id=int(order["user_id"]),
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
            "target_user_id": target_user_id,
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

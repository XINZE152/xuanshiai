"""type=5 活动报名履约。"""

from __future__ import annotations

from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.payment.fulfillment.base import FulfillmentError


class ActivityFulfillmentHandler:
    """活动报名支付状态更新与退款释放名额。

    幂等依赖 ``activity_signup.uk_activity_signup_order``；同一订单重复执行
    只更新同一行，不会重复加人。
    """

    order_type = 5

    async def fulfill(self, db: AsyncSession, order: Mapping[str, Any]) -> dict[str, Any]:
        activity_id = _activity_id(order)
        if activity_id is None:
            raise FulfillmentError("活动订单缺少 activity_id")

        # 先按「活动 + 用户」更新已有报名；报名通常由 signup_activity 先行创建。
        updated = await db.execute(
            text(
                """UPDATE activity_signup
                   SET pay_status = 'paid', order_no = :order_no,
                       paid_amount = :amount, updated_at = UTC_TIMESTAMP()
                   WHERE activity_id = :activity_id AND user_id = :user_id"""
            ),
            {
                "activity_id": activity_id,
                "user_id": order["user_id"],
                "order_no": order["order_no"],
                "amount": order["amount"],
            },
        )
        inserted = False
        if updated.rowcount == 0:
            # 没有预报名记录时补一条；status 保持 0（待审核），不因支付绕过审核。
            result = await db.execute(
                text(
                    """INSERT INTO activity_signup
                       (activity_id, user_id, status, order_no, pay_status, paid_amount)
                       VALUES (:activity_id, :user_id, 0, :order_no, 'paid', :amount)
                       ON DUPLICATE KEY UPDATE
                           order_no = VALUES(order_no),
                           pay_status = 'paid',
                           paid_amount = VALUES(paid_amount),
                           updated_at = UTC_TIMESTAMP()"""
                ),
                {
                    "activity_id": activity_id,
                    "user_id": order["user_id"],
                    "order_no": order["order_no"],
                    "amount": order["amount"],
                },
            )
            inserted = result.rowcount == 1

        if inserted:
            # 新报名才增加人数；预报名已由 signup_activity 计过人数。
            await db.execute(
                text(
                    """UPDATE offline_activity
                       SET current_people = current_people + 1,
                           status = CASE
                             WHEN max_people > 0 AND current_people + 1 >= max_people
                                  AND status = 1 THEN 2
                             ELSE status
                           END,
                           updated_at = UTC_TIMESTAMP()
                       WHERE id = :activity_id"""
                ),
                {"activity_id": activity_id},
            )

        signup = (
            await db.execute(
                text(
                    """SELECT id, status, pay_status FROM activity_signup
                       WHERE activity_id = :activity_id AND user_id = :user_id
                       LIMIT 1"""
                ),
                {"activity_id": activity_id, "user_id": order["user_id"]},
            )
        ).mappings().first()
        return {
            "fulfilled": True,
            "activity_id": activity_id,
            "signup_id": int(signup["id"]) if signup else None,
            "pay_status": signup["pay_status"] if signup else "paid",
        }

    async def revoke(
        self,
        db: AsyncSession,
        order: Mapping[str, Any],
        refund: Mapping[str, Any],
    ) -> None:
        signup = (
            await db.execute(
                text(
                    """SELECT id, activity_id, status, pay_status
                       FROM activity_signup
                       WHERE order_no = :order_no
                       FOR UPDATE"""
                ),
                {"order_no": order["order_no"]},
            )
        ).mappings().first()
        if not signup:
            return
        if str(signup["pay_status"]) == "refunded":
            return

        # 报名软状态置为已取消；支付状态置为 refunded。
        updated = await db.execute(
            text(
                """UPDATE activity_signup
                   SET status = 2, pay_status = 'refunded',
                       cancel_reason = :reason, updated_at = UTC_TIMESTAMP()
                   WHERE id = :id AND pay_status = 'paid'"""
            ),
            {
                "id": signup["id"],
                "reason": str(refund.get("reason") or "支付退款")[:255],
            },
        )
        if updated.rowcount != 1:
            return

        # 释放名额：只有原本计入 current_people 的报名才需要减一。
        await db.execute(
            text(
                """UPDATE offline_activity
                   SET current_people = GREATEST(current_people - 1, 0),
                       status = CASE
                         WHEN status = 2 AND (max_people = 0 OR current_people - 1 < max_people)
                              THEN 1
                         ELSE status
                       END,
                       updated_at = UTC_TIMESTAMP()
                   WHERE id = :activity_id"""
            ),
            {"activity_id": int(signup["activity_id"])},
        )


def _activity_id(order: Mapping[str, Any]) -> int | None:
    """活动订单约定 product_id = offline_activity.id；兼容数字 product_type。"""
    value = order.get("product_id")
    if value is None:
        value = order.get("product_type")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None

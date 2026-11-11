"""type=7 积分充值履约：人民币购买积分到账与退款冲正。

幂等依赖 ``user_points.uk_user_points_biz_key``：
- 发放：``POINTS:{order_no}``
- 退款冲正：``REFUND:{order_no}``

与 type=6 虚拟商品积分分支同口径（``user_points.type=5``）；
``source_type='points_recharge'`` 供后台积分明细按 ``recharge`` 类别过滤。
履约按 ``product_id`` 读目录快照，不过滤 ``enabled``——已支付订单即使商品
事后下架也必须到账；运营改价/改量应新建商品而不是改旧行。
"""

from __future__ import annotations

from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.payment.fulfillment.base import (
    FulfillmentError,
    PermanentFulfillmentError,
)
from app.services.points import _balance

_POINT_TYPE_PURCHASE = 5  # user_points.type=5：后台发放/支付购买
_SOURCE_TYPE = "points_recharge"


class PointsRechargeFulfillmentHandler:
    """积分充值发放与退款扣回；不 commit，事务由 Worker 统一控制。"""

    order_type = 7

    async def fulfill(self, db: AsyncSession, order: Mapping[str, Any]) -> dict[str, Any]:
        quantity = await _load_quantity(db, order)
        biz_key = f"POINTS:{order['order_no']}"
        # 锁用户行，保证同一用户并发积分变动时余额计算正确。
        await _lock_user(db, int(order["user_id"]))
        existing_balance = await db.scalar(
            text("SELECT balance FROM user_points WHERE biz_key = :biz_key LIMIT 1"),
            {"biz_key": biz_key},
        )
        if existing_balance is not None:
            return {
                "fulfilled": True,
                "already": True,
                "points": quantity,
                "balance": int(existing_balance),
            }

        before = await _balance(db, order["user_id"], True)
        after = before + quantity
        await db.execute(
            text(
                """INSERT INTO user_points
                   (user_id, type, amount, balance, `desc`, source_type, biz_key)
                   VALUES (:user_id, :type, :amount, :balance, :description, :source_type, :biz_key)
                   ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)"""
            ),
            {
                "user_id": order["user_id"],
                "type": _POINT_TYPE_PURCHASE,
                "amount": quantity,
                "balance": after,
                "description": f"积分充值:{order['order_no']}"[:255],
                "source_type": _SOURCE_TYPE,
                "biz_key": biz_key,
            },
        )
        return {"fulfilled": True, "points": quantity, "balance": after}

    async def revoke(
        self,
        db: AsyncSession,
        order: Mapping[str, Any],
        refund: Mapping[str, Any],
    ) -> None:
        """退款冲正：最多扣回本单充值额，余额不足按剩余余额扣，不扣成负数。"""
        quantity = await _load_quantity(db, order)
        biz_key = f"REFUND:{order['order_no']}"
        await _lock_user(db, int(order["user_id"]))
        if await db.scalar(
            text("SELECT id FROM user_points WHERE biz_key = :biz_key LIMIT 1"),
            {"biz_key": biz_key},
        ):
            return
        before = await _balance(db, order["user_id"], True)
        deduct = min(before, quantity)
        if deduct <= 0:
            return
        await db.execute(
            text(
                """INSERT INTO user_points
                   (user_id, type, amount, balance, `desc`, source_type, biz_key)
                   VALUES (:user_id, :type, :amount, :balance, :description, :source_type, :biz_key)
                   ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)"""
            ),
            {
                "user_id": order["user_id"],
                "type": _POINT_TYPE_PURCHASE,
                "amount": -deduct,
                "balance": before - deduct,
                "description": f"积分充值退款冲正:{order['order_no']}"[:255],
                "source_type": _SOURCE_TYPE,
                "biz_key": biz_key,
            },
        )


async def _lock_user(db: AsyncSession, user_id: int) -> None:
    """锁用户行，串行化同一用户的积分余额变动。"""
    locked = await db.scalar(
        text("SELECT id FROM users WHERE id = :id FOR UPDATE"),
        {"id": user_id},
    )
    if locked is None:
        raise FulfillmentError("用户不存在，无法入账")


async def _load_quantity(db: AsyncSession, order: Mapping[str, Any]) -> int:
    product_id = order.get("product_id")
    row = None
    if product_id is not None:
        row = (
            await db.execute(
                text(
                    """SELECT id, quantity, resource_type FROM admin_recharge_item
                       WHERE id = :id LIMIT 1"""
                ),
                {"id": product_id},
            )
        ).mappings().first()
    if not row:
        raise FulfillmentError(f"充值商品不存在：product_id={product_id}")
    if str(row["resource_type"]) != "points":
        raise PermanentFulfillmentError(
            f"订单商品不是积分充值：product_id={product_id} resource_type={row['resource_type']}"
        )
    quantity = int(row["quantity"] or 0)
    if quantity <= 0:
        raise FulfillmentError(f"充值商品积分数量配置异常：product_id={product_id}")
    return quantity
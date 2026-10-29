"""type=6 虚拟商品履约（积分/虚拟次数）。"""

from __future__ import annotations

from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.payment.fulfillment.base import FulfillmentError
from app.services.points import _balance

# 积分商品 code -> 次数账户 quota_code；与 app/services/points.py 的兑换口径一致。
_QUOTA_CODE_BY_PRODUCT = {
    "extra_apply": "apply",
    "browse_unlock": "browse",
    "paper_plane_unlock": "paper_plane",
}
_POINT_TYPE_PURCHASE = 5  # user_points.type=5：后台发放/支付购买


class VirtualGoodsFulfillmentHandler:
    """虚拟商品发放与退款扣回。

    次数权益依赖 ``user_quota_grant.uk_quota_grant_order``；
    积分流水依赖 ``user_points.uk_user_points_biz_key``：
    - 发放：``POINTS:{order_no}``
    - 退款冲正：``REFUND:{order_no}``
    """

    order_type = 6

    async def fulfill(self, db: AsyncSession, order: Mapping[str, Any]) -> dict[str, Any]:
        product = await _load_product(db, order)
        if not product:
            raise FulfillmentError("虚拟商品不存在或未配置")
        kind = str(product.get("product_type") or "").lower()
        quantity = _quantity(product.get("value"), default=1)
        code = str(product["code"])

        if kind in {"points", "point"}:
            description = _purchase_description(order["order_no"])
            biz_key = f"POINTS:{order['order_no']}"
            # 锁用户行，保证同一用户并发积分变动时余额计算正确；不 commit。
            await _lock_user(db, int(order["user_id"]))
            existing_balance = await db.scalar(
                text(
                    """SELECT balance FROM user_points
                       WHERE biz_key = :biz_key LIMIT 1"""
                ),
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
                       (user_id, type, amount, balance, `desc`, biz_key)
                       VALUES (:user_id, :type, :amount, :balance, :description, :biz_key)
                       ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)"""
                ),
                {
                    "user_id": order["user_id"],
                    "type": _POINT_TYPE_PURCHASE,
                    "amount": quantity,
                    "balance": after,
                    "description": description,
                    "biz_key": biz_key,
                },
            )
            return {"fulfilled": True, "points": quantity, "balance": after}

        # 其余统一按虚拟次数处理；未知 code 使用原 code 作为 quota_code。
        quota_code = _QUOTA_CODE_BY_PRODUCT.get(code, code)[:32]
        await db.execute(
            text(
                """INSERT INTO user_quota_grant
                   (user_id, quota_code, remaining, source, order_no)
                   VALUES (:user_id, :quota_code, :remaining, 'payment', :order_no)
                   ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)"""
            ),
            {
                "user_id": order["user_id"],
                "quota_code": quota_code,
                "remaining": quantity,
                "order_no": order["order_no"],
            },
        )
        return {
            "fulfilled": True,
            "quota_code": quota_code,
            "quantity": quantity,
        }

    async def revoke(
        self,
        db: AsyncSession,
        order: Mapping[str, Any],
        refund: Mapping[str, Any],
    ) -> None:
        product = await _load_product(db, order)
        if not product:
            return
        kind = str(product.get("product_type") or "").lower()

        if kind in {"points", "point"}:
            quantity = _quantity(product.get("value"), default=1)
            description = _refund_description(order["order_no"])
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
                       (user_id, type, amount, balance, `desc`, biz_key)
                       VALUES (:user_id, :type, :amount, :balance, :description, :biz_key)
                       ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)"""
                ),
                {
                    "user_id": order["user_id"],
                    "type": _POINT_TYPE_PURCHASE,
                    "amount": -deduct,
                    "balance": before - deduct,
                    "description": description,
                    "biz_key": biz_key,
                },
            )
            return

        # 次数权益：只回收该订单尚未使用的剩余次数。
        await db.execute(
            text(
                """UPDATE user_quota_grant
                   SET remaining = 0
                   WHERE order_no = :order_no AND remaining > 0"""
            ),
            {"order_no": order["order_no"]},
        )


async def _lock_user(db: AsyncSession, user_id: int) -> None:
    """锁用户行，串行化同一用户的积分余额变动。"""
    locked = await db.scalar(
        text("SELECT id FROM users WHERE id = :id FOR UPDATE"),
        {"id": user_id},
    )
    if locked is None:
        raise FulfillmentError("用户不存在，无法入账")


async def _load_product(
    db: AsyncSession,
    order: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    """按 product_id 优先、product_type code 兜底解析积分商品目录。"""
    product_id = order.get("product_id")
    if product_id is not None:
        row = (
            await db.execute(
                text(
                    """SELECT id, code, name, product_type, points_cost, value
                       FROM config_point_product WHERE id = :id LIMIT 1"""
                ),
                {"id": product_id},
            )
        ).mappings().first()
        if row:
            return row

    code = str(order.get("product_type") or "").strip()
    if code:
        return (
            await db.execute(
                text(
                    """SELECT id, code, name, product_type, points_cost, value
                       FROM config_point_product WHERE code = :code LIMIT 1"""
                ),
                {"code": code},
            )
        ).mappings().first()
    return None


def _quantity(value: Any, *, default: int) -> int:
    try:
        quantity = int(value) if value is not None else default
    except (TypeError, ValueError):
        quantity = default
    return max(1, quantity)


def _purchase_description(order_no: str) -> str:
    return f"虚拟商品购买:{order_no}"[:255]


def _refund_description(order_no: str) -> str:
    return f"虚拟商品退款冲正:{order_no}"[:255]

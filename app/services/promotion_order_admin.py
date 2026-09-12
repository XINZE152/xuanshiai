"""会员服务-推广管理（推广服务订单）后台服务。"""

from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.promotion_order_admin import (
    PromotionOrder,
    PromotionOrderPage,
    PromotionOrderStatistics,
    PromotionOrderUpdate,
)

_ORDER_SELECT = """SELECT o.id, o.order_no, o.user_id, o.product_name, o.amount, o.pay_status,
    o.pay_method, o.status, o.remark, o.paid_at, o.created_at, o.updated_at,
    u.nickname AS user_nickname
    FROM promotion_order o LEFT JOIN users u ON u.id = o.user_id"""


def _money(value: Any) -> str:
    """Decimal -> 字符串（保留 2 位小数，避免前端精度丢失）。"""
    if value is None:
        return "0.00"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _order(row: Any) -> PromotionOrder:
    payload = dict(row)
    payload["amount"] = _money(payload.get("amount"))
    return PromotionOrder(**payload)


async def list_orders(
    db: AsyncSession,
    page: int,
    page_size: int,
    pay_status: str | None = None,
    status: str | None = None,
    search: str | None = None,
) -> PromotionOrderPage:
    where = ["1 = 1"]
    params: dict[str, Any] = {"limit": page_size, "offset": (page - 1) * page_size}
    if pay_status:
        where.append("o.pay_status = :pay_status")
        params["pay_status"] = pay_status
    if status:
        where.append("o.status = :status")
        params["status"] = status
    if search:
        where.append("(o.order_no LIKE CONCAT('%', :search, '%') OR u.nickname LIKE CONCAT('%', :search, '%')"
                     " OR o.product_name LIKE CONCAT('%', :search, '%') OR o.user_id = :search_id)")
        params["search"] = search
        params["search_id"] = int(search) if search.isdigit() else 0
    clause = " AND ".join(where)
    rows = await db.execute(text(f"{_ORDER_SELECT} WHERE {clause} ORDER BY o.id DESC LIMIT :limit OFFSET :offset"), params)
    total = int((await db.execute(text(f"""SELECT COUNT(*) FROM promotion_order o
        LEFT JOIN users u ON u.id = o.user_id WHERE {clause}"""),
        {key: value for key, value in params.items() if key not in ("limit", "offset")})).scalar() or 0)
    return PromotionOrderPage(
        items=[_order(row) for row in rows.mappings().all()],
        page=page, page_size=page_size, total=total, has_more=page * page_size < total,
    )


async def get_order(db: AsyncSession, order_id: int) -> PromotionOrder:
    row = (await db.execute(text(f"{_ORDER_SELECT} WHERE o.id = :id"), {"id": order_id})).mappings().first()
    if not row:
        raise HTTPException(404, detail="推广订单不存在")
    return _order(row)


async def update_order(db: AsyncSession, order_id: int, body: PromotionOrderUpdate, actor_id: int) -> PromotionOrder:
    await get_order(db, order_id)
    values = body.model_dump(exclude_unset=True, exclude_none=True)
    # 支付状态改为已支付时补记支付时间；退款/未支付则清空。
    if values.get("pay_status") == "paid":
        await db.execute(text("UPDATE promotion_order SET paid_at = COALESCE(paid_at, UTC_TIMESTAMP()) WHERE id = :id"), {"id": order_id})
    elif "pay_status" in values:
        await db.execute(text("UPDATE promotion_order SET paid_at = NULL WHERE id = :id"), {"id": order_id})
    if values:
        assignments = ", ".join(f"{key} = :{key}" for key in values)
        await db.execute(text(f"UPDATE promotion_order SET {assignments}, updated_at = UTC_TIMESTAMP() WHERE id = :id"), {**values, "id": order_id})
    await db.execute(text("""INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id)
        VALUES (:actor, 'promotion_order.update', 'promotion_order', :id)"""), {"actor": actor_id, "id": order_id})
    await db.commit()
    return await get_order(db, order_id)


async def delete_order(db: AsyncSession, order_id: int, actor_id: int) -> bool:
    await get_order(db, order_id)
    await db.execute(text("DELETE FROM promotion_order WHERE id = :id"), {"id": order_id})
    await db.execute(text("""INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id)
        VALUES (:actor, 'promotion_order.delete', 'promotion_order', :id)"""), {"actor": actor_id, "id": order_id})
    await db.commit()
    return True


async def order_statistics(db: AsyncSession) -> PromotionOrderStatistics:
    row = (await db.execute(text("""SELECT COUNT(*) AS total,
        SUM(pay_status = 'paid') AS paid_count,
        SUM(status = 'processing') AS processing_count,
        SUM(CASE WHEN pay_status = 'paid' THEN amount ELSE 0 END) AS paid_amount
        FROM promotion_order"""))).mappings().one()
    return PromotionOrderStatistics(
        total=int(row["total"] or 0),
        paid_count=int(row["paid_count"] or 0),
        paid_amount=_money(row["paid_amount"]),
        processing_count=int(row["processing_count"] or 0),
    )

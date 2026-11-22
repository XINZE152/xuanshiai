"""Q57-b 人民币积分充值：商品目录读取与服务端定价下单（order_type=7）。

目录复用 ``admin_recharge_item``（``resource_type='points'``），定价是运营数据
不是代码；订单、预下单、回调、履约、对账全部复用现有支付基建，本模块不做
任何积分入账——入账只经 ``fulfillment/points_recharge.py``。
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.payments import PaidOrderResponse
from app.schemas.points import (
    AdminPointRechargePackage,
    CreatePointRechargeOrderRequest,
    PointRechargePackage,
)
from app.services.payments import _create_order

POINTS_RECHARGE_ORDER_TYPE = 7
_PRODUCT_CODE = "points_recharge"


async def list_admin_recharge_packages(db: AsyncSession) -> list[AdminPointRechargePackage]:
    result = await db.execute(
        text("""SELECT id, name, quantity, price, enabled, sort
            FROM admin_recharge_item WHERE resource_type = 'points' AND tenant_id = 1
            ORDER BY sort, id""")
    )
    return [
        AdminPointRechargePackage(
            id=int(row["id"]),
            name=str(row["name"]),
            points=int(row["quantity"]),
            price=Decimal(str(row["price"])),
            enabled=bool(row["enabled"]),
            sort=int(row["sort"]),
        )
        for row in result.mappings().all()
    ]


async def save_admin_recharge_packages(
    db: AsyncSession, items: list[AdminPointRechargePackage]
) -> list[AdminPointRechargePackage]:
    if len(items) > 20:
        raise HTTPException(422, detail="积分充值套餐最多配置20项")
    ids = [item.id for item in items if item.id is not None]
    if len(ids) != len(set(ids)):
        raise HTTPException(422, detail="套餐记录不能重复提交")

    existing_rows = await db.execute(
        text("""SELECT id, quantity, price FROM admin_recharge_item
            WHERE resource_type = 'points' AND tenant_id = 1 FOR UPDATE""")
    )
    existing = {int(row["id"]): row for row in existing_rows.mappings().all()}
    existing_ids = set(existing)
    if any(item_id not in existing_ids for item_id in ids):
        raise HTTPException(409, detail="套餐数据已变化，请重新加载后再保存")

    retained: set[int] = set()
    for index, item in enumerate(items):
        values = {
            "name": item.name.strip(),
            "quantity": item.points,
            "price": item.price,
            "sort": item.sort if item.sort else index,
            "enabled": int(item.enabled),
        }
        if not values["name"]:
            raise HTTPException(422, detail="套餐名称不能为空")
        if item.id is None:
            result = await db.execute(
                text("""INSERT INTO admin_recharge_item
                    (tenant_id, name, resource_type, quantity, price, sort, enabled)
                    VALUES (1, :name, 'points', :quantity, :price, :sort, :enabled)"""),
                values,
            )
            retained.add(int(result.lastrowid))
        elif (
            int(existing[item.id]["quantity"]) != item.points
            or Decimal(str(existing[item.id]["price"])) != item.price
        ):
            result = await db.execute(
                text("""INSERT INTO admin_recharge_item
                    (tenant_id, name, resource_type, quantity, price, sort, enabled)
                    VALUES (1, :name, 'points', :quantity, :price, :sort, :enabled)"""),
                values,
            )
            retained.add(int(result.lastrowid))
        else:
            await db.execute(
                text("""UPDATE admin_recharge_item
                    SET name=:name, quantity=:quantity, price=:price, sort=:sort, enabled=:enabled
                    WHERE id=:id AND tenant_id=1 AND resource_type='points'"""),
                {**values, "id": item.id},
            )
            retained.add(item.id)

    for item_id in existing_ids - retained:
        await db.execute(
            text("UPDATE admin_recharge_item SET enabled=0 WHERE id=:id"), {"id": item_id}
        )
    await db.commit()
    return await list_admin_recharge_packages(db)


async def list_recharge_packages(db: AsyncSession) -> list[PointRechargePackage]:
    result = await db.execute(
        text(
            """SELECT id, name, quantity, price FROM admin_recharge_item
               WHERE resource_type = 'points' AND enabled = 1 AND tenant_id = 1
               ORDER BY sort, id"""
        )
    )
    return [
        PointRechargePackage(
            id=int(row["id"]),
            name=str(row["name"]),
            points=int(row["quantity"]),
            price=Decimal(str(row["price"])),
        )
        for row in result.mappings().all()
    ]


async def create_recharge_order(
    db: AsyncSession,
    user_id: int,
    body: CreatePointRechargeOrderRequest,
    idempotency_key: str,
) -> PaidOrderResponse:
    """按目录服务端定价下单；body 只有 package_id，客户端无法传价。

    幂等语义与置顶/爆灯一致：同一 ``Idempotency-Key`` 返回首次创建的订单；
    更换商品必须使用新键。
    """
    row = (
        await db.execute(
            text(
                """SELECT id, name, quantity, price FROM admin_recharge_item
                   WHERE id = :id AND resource_type = 'points'
                     AND enabled = 1 AND tenant_id = 1
                   LIMIT 1"""
            ),
            {"id": body.package_id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(404, detail="充值商品不存在或已下架")
    price = Decimal(str(row["price"]))
    if price <= 0 or int(row["quantity"]) <= 0:
        raise HTTPException(409, detail="充值商品配置异常，请联系客服")
    return await _create_order(
        db,
        user_id,
        POINTS_RECHARGE_ORDER_TYPE,
        int(row["id"]),
        _PRODUCT_CODE,
        str(row["name"]),
        price,
        idempotency_key,
    )

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
from app.schemas.points import CreatePointRechargeOrderRequest, PointRechargePackage
from app.services.payments import _create_order

POINTS_RECHARGE_ORDER_TYPE = 7
_PRODUCT_CODE = "points_recharge"


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
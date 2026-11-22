"""Test payment orders and deterministic local fulfillment."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.schemas.payments import (
    BoostPackage,
    BoostStatus,
    CreateBoostOrderRequest,
    PaidOrderResponse,
    SpotlightPaymentRequest,
    SpotlightPriceResponse,
    TestPaymentRequest,
    TestPaymentResponse,
)
from app.services.discovery import _ensure_target
from app.services.notifications import emit_notification
from app.services.admin_config import get_runtime_value


DEFAULT_BOOST_PACKAGES = {
    "boost_1d": BoostPackage(code="boost_1d", name="置顶1天", days=1, price=Decimal("1.00"), recommended=False),
    "boost_7d": BoostPackage(code="boost_7d", name="置顶7天", days=7, price=Decimal("5.00"), recommended=True),
    "boost_30d": BoostPackage(code="boost_30d", name="置顶30天", days=30, price=Decimal("15.00"), recommended=False),
}

# 爆灯单价的运行期配置坐标。展示接口与下单共用这两个常量，
# 改价只需改运营配置，不要在代码里再写第二个字面量。
SPOTLIGHT_PRICE_NAMESPACE = "finance"
SPOTLIGHT_PRICE_KEY = "spotlight_price"
SPOTLIGHT_PRICE_FALLBACK = "9.90"


def _paid_order(row) -> PaidOrderResponse:
    return PaidOrderResponse(
        order_no=row["order_no"], product_code=str(row["product_type"]), product_name=row["product_name"],
        amount=Decimal(str(row["amount"])), status=int(row["status"]), expire_at=row["expire_at"],
        payment_required=int(row["status"]) == 0,
    )


async def _create_order(
    db: AsyncSession,
    user_id: int,
    order_type: int,
    product_id: int | None,
    product_code: str,
    product_name: str,
    amount: Decimal,
    idempotency_key: str,
) -> PaidOrderResponse:
    if not idempotency_key or len(idempotency_key) > 128:
        raise HTTPException(422, detail="Idempotency-Key is required")
    existing = await db.execute(text("""SELECT order_no, product_type, product_name, amount, status, expire_at
        FROM payment_order WHERE user_id=:user_id AND type=:type AND idempotency_key=:key"""), {
        "user_id": user_id, "type": order_type, "key": idempotency_key,
    })
    row = existing.mappings().first()
    if row:
        return _paid_order(row)
    order_no = f"TEST{order_type}{datetime.now(UTC):%Y%m%d%H%M%S}{secrets.token_hex(5).upper()}"
    expire_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=30)
    # real 模式下这些订单会走真实微信预支付，标记为微信支付而不是测试支付。
    pay_type = 1 if settings.wechat_pay_mode == "real" else 4
    await db.execute(text("""INSERT INTO payment_order
        (user_id, order_no, type, product_id, product_type, product_name, amount, pay_type, status, expire_at, idempotency_key)
        VALUES (:user_id, :order_no, :type, :product_id, :product_type, :product_name, :amount, :pay_type, 0, :expire_at, :key)"""), {
        "user_id": user_id, "order_no": order_no, "type": order_type, "product_id": product_id,
        "product_type": product_code, "product_name": product_name, "amount": amount,
        "pay_type": pay_type,
        "expire_at": expire_at, "key": idempotency_key,
    })
    await db.commit()
    return PaidOrderResponse(order_no=order_no, product_code=product_code, product_name=product_name, amount=amount, status=0, expire_at=expire_at, payment_required=True)


async def list_boost_packages(db: AsyncSession) -> list[BoostPackage]:
    config = await get_runtime_value(db, "finance", "boost_packages", None)
    if not isinstance(config, list):
        return list(DEFAULT_BOOST_PACKAGES.values())
    packages: dict[str, BoostPackage] = {}
    for row in config:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or "")
        if code not in DEFAULT_BOOST_PACKAGES:
            continue
        default = DEFAULT_BOOST_PACKAGES[code]
        try:
            days = int(row.get("days", default.days))
            price = Decimal(str(row.get("price", default.price)))
        except (ValueError, TypeError, ArithmeticError):
            continue
        name = str(row.get("name") or default.name).strip()
        if not 1 <= days <= 3650 or not price.is_finite() or not 0 <= price <= Decimal("1000000") or not name or len(name) > 64:
            continue
        packages[code] = BoostPackage(code=code, name=name, days=days, price=price, recommended=bool(row.get("recommended", default.recommended)))
    return sorted(packages.values(), key=lambda item: (item.days, item.code)) or list(DEFAULT_BOOST_PACKAGES.values())


async def get_boost_package(db: AsyncSession, code: str) -> BoostPackage | None:
    return next((item for item in await list_boost_packages(db) if item.code == code), None)


async def create_boost_order(db: AsyncSession, user_id: int, body: CreateBoostOrderRequest, idempotency_key: str) -> PaidOrderResponse:
    package = await get_boost_package(db, body.package_code)
    if not package:
        raise HTTPException(404, detail="置顶套餐不存在")
    return await _create_order(db, user_id, 4, user_id, package.code, package.name, package.price, idempotency_key)


async def get_boost_status(db: AsyncSession, user_id: int) -> BoostStatus:
    row = (await db.execute(text("""SELECT end_at, order_no FROM user_boost
        WHERE user_id=:user_id AND target_user_id=:user_id AND status=1
          AND (start_at IS NULL OR start_at <= UTC_TIMESTAMP())
          AND (end_at IS NULL OR end_at > UTC_TIMESTAMP())
        ORDER BY end_at DESC LIMIT 1"""), {"user_id": user_id})).mappings().first()
    if not row:
        return BoostStatus(active=False, remaining_days=0, expires_at=None, order_no=None)
    remaining = max(0, (row["end_at"] - datetime.now(UTC).replace(tzinfo=None)).days)
    return BoostStatus(active=True, remaining_days=remaining, expires_at=row["end_at"], order_no=row["order_no"])


async def get_paid_order(db: AsyncSession, user_id: int, order_no: str, order_type: int | None = None) -> PaidOrderResponse:
    condition = "AND type=:type" if order_type is not None else ""
    params = {"user_id": user_id, "order_no": order_no, "type": order_type}
    row = (await db.execute(text(f"""SELECT order_no, product_type, product_name, amount, status, expire_at
        FROM payment_order WHERE user_id=:user_id AND order_no=:order_no {condition}"""), params)).mappings().first()
    if not row:
        raise HTTPException(404, detail="订单不存在")
    return _paid_order(row)


async def get_spotlight_price(db: AsyncSession) -> SpotlightPriceResponse:
    """爆灯单价。展示与下单读同一处运行期配置，前端不要用常量兜底成另一个价。"""
    try:
        amount = Decimal(str(await get_runtime_value(
            db, SPOTLIGHT_PRICE_NAMESPACE, SPOTLIGHT_PRICE_KEY, SPOTLIGHT_PRICE_FALLBACK,
        )))
    except (ValueError, TypeError, ArithmeticError):
        amount = Decimal(SPOTLIGHT_PRICE_FALLBACK)
    if not amount.is_finite() or amount < 0 or amount > Decimal("1000000"):
        amount = Decimal(SPOTLIGHT_PRICE_FALLBACK)
    return SpotlightPriceResponse(amount=amount)


async def _ensure_realname_verified(db: AsyncSession, user_id: int) -> None:
    """付费爆灯与免费爆灯（``create_superlike``）共用同一道实名门禁。

    免费路径在后端强制实名；若付费路径不校验，用户额度用完后花 9.9 就能绕过，
    既与产品规则冲突，也不符合实名制要求。
    """
    status = (
        await db.execute(
            text("SELECT COALESCE(realname_status, 0) FROM user_auth WHERE user_id = :user_id"),
            {"user_id": user_id},
        )
    ).scalar()
    if int(status or 0) != 2:
        raise HTTPException(403, detail="请先完成实名认证后再爆灯")


async def create_spotlight_order(db: AsyncSession, user_id: int, body: SpotlightPaymentRequest, idempotency_key: str) -> PaidOrderResponse:
    await _ensure_target(db, user_id, body.target_user_id)
    await _ensure_realname_verified(db, user_id)
    # 单价走运行期配置，代码只留兜底 —— 避免再次出现「后端写死 5.00、前端 9.9」的口径分裂。
    # 历史订单金额不回改：payment_order.amount 已落库，改价只影响新订单。
    spot_price = (await get_spotlight_price(db)).amount
    return await _create_order(db, user_id, 2, body.target_user_id, "superlike", "爆灯", spot_price, idempotency_key)


async def complete_test_payment(db: AsyncSession, user_id: int, body: TestPaymentRequest) -> TestPaymentResponse:
    if not settings.is_test_mode:
        raise HTTPException(403, detail="测试支付只允许在 development/testing 环境使用")
    if settings.wechat_pay_mode == "real":
        raise HTTPException(403, detail="真实微信支付已启用，测试支付已关闭")
    transaction_id = body.transaction_id or f"TEST-PAY-{secrets.token_hex(8).upper()}"
    async with db.begin():
        result = await db.execute(text("""SELECT * FROM payment_order
            WHERE order_no=:order_no AND user_id=:user_id FOR UPDATE"""), {"order_no": body.order_no, "user_id": user_id})
        order = result.mappings().first()
        if not order:
            raise HTTPException(404, detail="订单不存在")
        if int(order["status"]) == 1:
            return TestPaymentResponse(order_no=body.order_no, status=1, transaction_id=order["transaction_id"], payment_required=False, fulfilled=True)
        if int(order["status"]) != 0:
            raise HTTPException(409, detail="订单当前不可支付")
        if not body.success:
            await db.execute(text("UPDATE payment_order SET status=2, transaction_id=:transaction_id, pay_time=UTC_TIMESTAMP() WHERE id=:id"), {"transaction_id": transaction_id, "id": order["id"]})
            return TestPaymentResponse(order_no=body.order_no, status=2, transaction_id=transaction_id, payment_required=False, fulfilled=False)
        await db.execute(text("UPDATE payment_order SET status=1, transaction_id=:transaction_id, pay_time=UTC_TIMESTAMP() WHERE id=:id"), {"transaction_id": transaction_id, "id": order["id"]})
        order_type = int(order["type"])
        if order_type == 1:
            package_code = order["product_type"]
            package = (await db.execute(text("SELECT code, duration_days FROM config_membership_package WHERE code=:code"), {"code": package_code})).mappings().first()
            # Repair orders created before the product_type/product_name fix.
            # Those rows contain a numeric product_type (legacy product id)
            # and the display name in product_name.
            if not package and str(package_code).isdigit():
                package = (await db.execute(text("SELECT code, duration_days FROM config_membership_package WHERE name=:name AND is_active=1 LIMIT 1"), {"name": order["product_name"]})).mappings().first()
                if package:
                    package_code = package["code"]
                    await db.execute(text("UPDATE payment_order SET product_type=:package_code WHERE id=:id"), {"package_code": package_code, "id": order["id"]})
            if not package:
                raise HTTPException(422, detail="会员套餐不存在")
            start_at = datetime.now(UTC).replace(tzinfo=None)
            await db.execute(text("""INSERT INTO user_membership (user_id, package_type, amount, order_no, start_at, end_at, status)
                VALUES (:user_id, :package_type, :amount, :order_no, :start_at, :end_at, 1)"""), {
                "user_id": user_id, "package_type": package["code"], "amount": order["amount"], "order_no": order["order_no"],
                "start_at": start_at, "end_at": start_at + timedelta(days=int(package["duration_days"])),
            })
        elif order_type in (2, 4):
            if order_type == 2:
                target_user_id = int(order["product_id"])
                await db.execute(text("""INSERT INTO user_boost (user_id, target_user_id, amount, order_no, start_at, end_at, status)
                    VALUES (:user_id, :target_user_id, :amount, :order_no, UTC_TIMESTAMP(), DATE_ADD(UTC_TIMESTAMP(), INTERVAL 1 DAY), 1)"""), {
                    "user_id": user_id, "target_user_id": target_user_id, "amount": order["amount"], "order_no": order["order_no"],
                })
                await emit_notification(
                    db,
                    recipient_user_id=target_user_id,
                    actor_user_id=user_id,
                    event_type="superlike",
                    title="收到爆灯",
                    content="有人对你发出了爆灯信号",
                    target_type="user",
                    target_id=user_id,
                )
            else:
                package = await get_boost_package(db, str(order["product_type"]))
                if not package:
                    raise HTTPException(422, detail="置顶套餐不存在")
                start_at = datetime.now(UTC).replace(tzinfo=None)
                await db.execute(text("""INSERT INTO user_boost (user_id, target_user_id, amount, order_no, start_at, end_at, status)
                    VALUES (:user_id, :user_id, :amount, :order_no, :start_at, :end_at, 1)"""), {
                    "user_id": user_id, "amount": order["amount"], "order_no": order["order_no"],
                    "start_at": start_at, "end_at": start_at + timedelta(days=package.days),
                })
        elif order_type != 3:
            raise HTTPException(422, detail="不支持的支付订单类型")
    return TestPaymentResponse(order_no=body.order_no, status=1, transaction_id=transaction_id, payment_required=False, fulfilled=True)

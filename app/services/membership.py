import json
import logging
import re
import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Mapping

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import payment_callback_signature
from app.schemas.membership import (
    CreateMembershipOrderRequest,
    MembershipHistoryItem,
    MembershipHistoryPage,
    MembershipOrderResponse,
    MembershipPackage,
    MembershipStatus,
)

logger = logging.getLogger(__name__)

DEFAULT_RIGHTS = {
    "apply_daily_limit": 3,
    "apply_bonus": 0,
    "superlike_daily_limit": 1,
    "browse_daily_limit": 20,
    "visitor_detail": False,
    "browse_history_scope": "today",
}

_SQL_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SQL_QUALIFIED_IDENTIFIER = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?"
)

ACTIVE_MEMBERSHIP_CONDITION_SQL = """{membership_alias}.status = 1
  AND ({membership_alias}.start_at IS NULL OR {membership_alias}.start_at <= UTC_TIMESTAMP())
  AND ({membership_alias}.end_at IS NULL OR {membership_alias}.end_at > UTC_TIMESTAMP())"""

ACTIVE_MEMBERSHIP_WHERE_SQL = """{membership_alias}.user_id = :{user_id_param}
  AND """ + ACTIVE_MEMBERSHIP_CONDITION_SQL

ACTIVE_MEMBERSHIP_EXISTS_SQL = """EXISTS (SELECT 1 FROM user_membership {membership_alias}
               WHERE {membership_alias}.user_id = :{user_id_param} AND {membership_alias}.status = 1
                 AND ({membership_alias}.start_at IS NULL OR {membership_alias}.start_at <= UTC_TIMESTAMP())
                 AND ({membership_alias}.end_at IS NULL OR {membership_alias}.end_at > UTC_TIMESTAMP()))"""


def _validate_sql_identifier(value: str) -> str:
    if not _SQL_IDENTIFIER.fullmatch(value):
        raise ValueError("invalid SQL identifier")
    return value


def active_membership_where_sql(*, membership_alias: str, user_id_param: str) -> str:
    return ACTIVE_MEMBERSHIP_WHERE_SQL.format(
        membership_alias=_validate_sql_identifier(membership_alias),
        user_id_param=_validate_sql_identifier(user_id_param),
    )


def active_membership_exists_sql(*, membership_alias: str, user_id_param: str) -> str:
    return ACTIVE_MEMBERSHIP_EXISTS_SQL.format(
        membership_alias=_validate_sql_identifier(membership_alias),
        user_id_param=_validate_sql_identifier(user_id_param),
    )


def active_membership_exists_for_column_sql(
    *, membership_alias: str, user_id_column: str
) -> str:
    """Build an active-membership EXISTS predicate correlated to a safe column."""
    alias = _validate_sql_identifier(membership_alias)
    if not _SQL_QUALIFIED_IDENTIFIER.fullmatch(user_id_column):
        raise ValueError("invalid SQL identifier")
    return (
        f"EXISTS (SELECT 1 FROM user_membership {alias} WHERE "
        f"{alias}.user_id = {user_id_column} AND "
        + ACTIVE_MEMBERSHIP_CONDITION_SQL.format(membership_alias=alias)
        + ")"
    )


async def has_active_membership(db: AsyncSession, user_id: int) -> bool:
    """Return whether a user currently has an effective VIP membership.

    This is the authorization source for member-only features.  It deliberately
    queries on every call so an expiry or status update is not hidden by a
    process-local cache.
    """
    result = await db.execute(
        text("SELECT " + active_membership_exists_sql(
            membership_alias="membership", user_id_param="user_id"
        )),
        {"user_id": user_id},
    )
    return bool(result.scalar())


async def get_active_membership_row(
    db: AsyncSession, user_id: int
) -> Mapping[str, Any] | None:
    """Load the current effective membership and package rights in one query."""
    result = await db.execute(
        text(
            "SELECT m.package_type, m.start_at, m.end_at, p.rights "
            "FROM user_membership m "
            "LEFT JOIN config_membership_package p ON p.code = m.package_type "
            "WHERE "
            + active_membership_where_sql(
                membership_alias="m", user_id_param="user_id"
            )
            + " ORDER BY m.end_at DESC LIMIT 1"
        ),
        {"user_id": user_id},
    )
    return result.mappings().first()


def parse_package_rights(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _rights(value, vip: bool = False) -> dict:
    rights = {**DEFAULT_RIGHTS, **parse_package_rights(value)}
    if vip:
        rights.update(
            apply_daily_limit=3 + int(rights.get("apply_bonus") or 0),
            superlike_daily_limit=3,
            browse_daily_limit=20,
            visitor_detail=True,
            browse_history_scope="all",
        )
    return rights


async def list_packages(db: AsyncSession) -> list[MembershipPackage]:
    # quotas 也依赖本模块的有效会员查询，延迟导入避免循环依赖。
    from app.services.quotas import resolve_membership_daily_limits

    result = await db.execute(text("SELECT code,name,duration_days,price,original_price,daily_price,badge,rights FROM config_membership_package WHERE is_active=1 ORDER BY sort,id"))
    rows = result.mappings().all()
    limits = await resolve_membership_daily_limits(db, [parse_package_rights(row["rights"]) for row in rows])
    return [
        MembershipPackage(
            code=row["code"], name=row["name"], duration_days=row["duration_days"],
            price=settings.membership_price_override(row["code"], "price", float(row["price"])),
            original_price=settings.membership_price_override(row["code"], "original_price", float(row["original_price"]) if row["original_price"] is not None else None),
            daily_price=settings.membership_price_override(row["code"], "daily_price", float(row["daily_price"]) if row["daily_price"] is not None else None),
            badge=row["badge"], rights=_rights(row["rights"]), daily_limits=daily_limits,
        )
        for row, daily_limits in zip(rows, limits, strict=True)
    ]


async def get_status(db: AsyncSession, user_id: int) -> MembershipStatus:
    from app.services.quotas import resolve_membership_daily_limits

    row = await get_active_membership_row(db, user_id)
    raw_rights = row["rights"] if row else None
    (daily_limits,) = await resolve_membership_daily_limits(
        db, [parse_package_rights(raw_rights) if row else None]
    )
    return MembershipStatus(
        is_vip=bool(row),
        package_type=row["package_type"] if row else None,
        start_at=row["start_at"] if row else None,
        end_at=row["end_at"] if row else None,
        rights=_rights(raw_rights, bool(row)),
        daily_limits=daily_limits,
    )


async def history(db: AsyncSession, user_id: int, page: int, page_size: int) -> MembershipHistoryPage:
    total = int((await db.execute(text("SELECT COUNT(*) FROM user_membership WHERE user_id=:id"), {"id": user_id})).scalar() or 0)
    result = await db.execute(text("SELECT id,package_type,amount,order_no,start_at,end_at,status FROM user_membership WHERE user_id=:id ORDER BY created_at DESC,id DESC LIMIT :limit OFFSET :offset"), {"id": user_id, "limit": page_size, "offset": (page - 1) * page_size})
    now = datetime.now(UTC).replace(tzinfo=None)
    items = []
    for row in result.mappings():
        is_vip = (
            row["status"] == 1
            and (row["start_at"] is None or row["start_at"] <= now)
            and (row["end_at"] is None or row["end_at"] > now)
        )
        items.append(
            MembershipHistoryItem(
                id=row["id"],
                package_type=row["package_type"],
                amount=float(row["amount"]) if row["amount"] is not None else None,
                order_no=row["order_no"],
                start_at=row["start_at"],
                end_at=row["end_at"],
                status=row["status"],
                is_vip=is_vip,
                rights=_rights(None, is_vip),
            )
        )
    return MembershipHistoryPage(items=items, page=page, page_size=page_size, total=total, has_more=page * page_size < total)


async def create_order(db: AsyncSession, user_id: int, body: CreateMembershipOrderRequest, idempotency_key: str | None) -> MembershipOrderResponse:
    if not idempotency_key or len(idempotency_key) > 128:
        raise HTTPException(422, detail="Idempotency-Key is required")
    existing = await db.execute(text("SELECT order_no,product_type,product_name,amount,pay_type,status,expire_at FROM payment_order WHERE user_id=:uid AND idempotency_key=:key"), {"uid": user_id, "key": idempotency_key})
    row = existing.mappings().first()
    if row:
        if str(row["product_type"]) != body.package_code:
            raise HTTPException(409, detail="Idempotency-Key 已用于其他会员套餐，请更换新的幂等键")
        return MembershipOrderResponse(order_no=row["order_no"], package_code=str(row["product_type"]), product_name=row["product_name"], amount=float(row["amount"]), pay_type=row["pay_type"], status=row["status"], expire_at=row["expire_at"], payment_required=row["status"] == 0)
    package = (await db.execute(text("SELECT id,code,name,price FROM config_membership_package WHERE code=:code AND is_active=1"), {"code": body.package_code})).mappings().first()
    if not package:
        raise HTTPException(404, detail="Membership package not found")
    price = settings.membership_price_override(package["code"], "price", float(package["price"]))
    order_no = f"VIP{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}{secrets.token_hex(5).upper()}"
    expire_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=30)
    # real 模式下新订单走真实微信支付，必须标记为 1（微信），不能因为
    # ENVIRONMENT=development 就写成 4（测试支付），否则对账/报表会把真实
    # 订单误判为测试单。
    pay_type = 1 if settings.wechat_pay_mode == "real" else (4 if settings.is_test_mode else 1)
    # 会员属虚拟商品：建单即落 wechat_xpay，使旧 JSAPI ``/prepay`` 的
    # ``channel != 'wechat'`` 校验直接 409，从接口层堵住「普通微信支付买会员」。
    # 函数内导入，避免 membership ↔ payment.xpay 的循环依赖。
    from app.services.payment.xpay import XPAY_CHANNEL

    await db.execute(text("INSERT INTO payment_order (user_id,order_no,type,product_id,product_type,product_name,amount,pay_type,channel,status,expire_at,idempotency_key) VALUES (:uid,:order_no,1,:pid,:code,:name,:amount,:pay_type,:channel,0,:expire_at,:key)"), {"uid": user_id, "order_no": order_no, "pid": package["id"], "code": package["code"], "name": package["name"], "amount": price, "pay_type": pay_type, "channel": XPAY_CHANNEL, "expire_at": expire_at, "key": idempotency_key})
    await db.commit()
    return MembershipOrderResponse(order_no=order_no, package_code=package["code"], product_name=package["name"], amount=price, pay_type=pay_type, status=0, expire_at=expire_at, payment_required=True)


async def get_order(db: AsyncSession, user_id: int, order_no: str) -> MembershipOrderResponse:
    row = (await db.execute(text("SELECT product_type,product_name,amount,pay_type,status,expire_at,order_no FROM payment_order WHERE user_id=:uid AND order_no=:order_no"), {"uid": user_id, "order_no": order_no})).mappings().first()
    if not row:
        raise HTTPException(404, detail="Order not found")
    return MembershipOrderResponse(order_no=row["order_no"], package_code=str(row["product_type"]), product_name=row["product_name"], amount=float(row["amount"]), pay_type=row["pay_type"], status=row["status"], expire_at=row["expire_at"], payment_required=row["status"] == 0)


async def pay_order_with_balance(db: AsyncSession, user_id: int, order_no: str, idempotency_key: str) -> MembershipOrderResponse:
    if not idempotency_key or len(idempotency_key) < 8 or len(idempotency_key) > 128:
        raise HTTPException(422, detail="Idempotency-Key 长度必须为 8-128")
    result = await db.execute(text("""SELECT po.id, po.order_no, po.product_type, po.product_name, po.amount,
        po.pay_type, po.status, po.expire_at, cp.code, cp.duration_days
        FROM payment_order po JOIN config_membership_package cp ON cp.code = po.product_type
        WHERE po.user_id = :user_id AND po.order_no = :order_no AND po.type = 1 FOR UPDATE"""), {"user_id": user_id, "order_no": order_no})
    order = result.mappings().first()
    if not order:
        raise HTTPException(404, detail="会员订单不存在")
    if int(order["status"]) == 1:
        return MembershipOrderResponse(order_no=order["order_no"], package_code=order["code"], product_name=order["product_name"], amount=float(order["amount"]), pay_type=order["pay_type"], status=1, expire_at=order["expire_at"], payment_required=False)
    if int(order["status"]) != 0:
        raise HTTPException(409, detail="订单当前不可支付")
    if order["expire_at"] and order["expire_at"] <= datetime.now(UTC).replace(tzinfo=None):
        raise HTTPException(409, detail="订单已过期")
    await db.execute(text("SELECT id FROM account_ledger WHERE account_type = 'user' AND account_id = :user_id FOR UPDATE"), {"user_id": user_id})
    balance = (await db.execute(text("""SELECT COALESCE(SUM(CASE WHEN state = 'AVAILABLE' AND direction = 'CREDIT' THEN amount
        WHEN state = 'AVAILABLE' AND direction = 'DEBIT' THEN -amount ELSE 0 END), 0) FROM account_ledger WHERE account_type = 'user' AND account_id = :user_id"""), {"user_id": user_id})).scalar()
    amount = Decimal(str(order["amount"]))
    if Decimal(str(balance or 0)) < amount:
        raise HTTPException(409, detail="余额不足")
    await db.execute(text("""UPDATE payment_order SET status = 1, pay_type = 2, pay_time = UTC_TIMESTAMP(),
        transaction_id = :transaction_id WHERE id = :id AND status = 0"""), {"transaction_id": f"BALANCE-{order['id']}-{idempotency_key[:32]}", "id": order["id"]})
    await db.execute(text("""INSERT INTO account_ledger (account_type, account_id, direction, amount, state, source_type, source_id, idempotency_key)
        VALUES ('user', :user_id, 'DEBIT', :amount, 'AVAILABLE', 'membership_order', :order_id, :ledger_key)
        ON DUPLICATE KEY UPDATE id = id"""), {"user_id": user_id, "amount": amount, "order_id": order["id"], "ledger_key": f"membership-balance:{order['id']}"})
    start_at = datetime.now(UTC).replace(tzinfo=None)
    await db.execute(text("""INSERT INTO user_membership (user_id, package_type, amount, order_no, start_at, end_at, status)
        VALUES (:user_id, :package_type, :amount, :order_no, :start_at, :end_at, 1)"""), {"user_id": user_id, "package_type": order["code"], "amount": amount, "order_no": order["order_no"], "start_at": start_at, "end_at": start_at + timedelta(days=int(order["duration_days"]))})
    await db.commit()
    return MembershipOrderResponse(order_no=order["order_no"], package_code=order["code"], product_name=order["product_name"], amount=float(amount), pay_type=2, status=1, expire_at=order["expire_at"], payment_required=False)


def _payment_sign_secret() -> str:
    """Resolve the callback verification key (D-21).

    ``real`` mode requires an explicitly configured key (fail-closed 503 when
    missing); ``mock`` mode uses the local mock secret and is rejected outright
    in production by settings validation.
    """
    if settings.wechat_payment_mode == "real":
        key = settings.wechat_payment_sign_key
        if key is None or not key.get_secret_value().strip():
            raise HTTPException(503, detail="微信支付回调验签密钥未配置")
        return key.get_secret_value()
    return settings.wechat_payment_mock_secret


def verify_payment_callback(body) -> None:
    """Verify callback signature (D-21).

    Both mock and real modes run through this single check so the fulfillment
    pipeline is identical; only the key source differs.
    """
    if settings.wechat_payment_mode == "real":
        raise HTTPException(503, detail="微信支付 v3 通知验签尚未实现，real 模式已关闭")
    expected = payment_callback_signature(body.order_no, body.transaction_id, body.amount, _payment_sign_secret())
    if not secrets.compare_digest(expected, body.signature):
        raise HTTPException(401, detail="支付回调签名校验失败")


async def _record_callback_anomaly(db: AsyncSession, body, reason: str) -> None:
    """Persist a post-verification anomaly for reconciliation and alerting (D-21).

    Only reached after the signature checks out, so it cannot be spammed by an
    anonymous caller who lacks the signing key.
    """
    await db.execute(text("""INSERT INTO business_audit_log
        (actor_user_id, action, resource_type, resource_id, reason)
        VALUES (NULL, 'payment.callback.rejected', 'payment_order', NULL, :reason)"""), {"reason": reason[:255]})
    await db.commit()


async def handle_wechat_callback(db: AsyncSession, body) -> None:
    """微信支付回调：验签 → 金额一致性 → 订单归属/状态 → 幂等履约（D-21）。

    编排顺序（mock / real 共用）：
    1. HMAC 验签失败 → 401，仅记日志（避免匿名写库放大攻击面）；
    2. 订单不存在 → 404；金额与订单不一致 → 409；均落业务审计；
    3. 已支付订单重复回调 → 直接返回（幂等）；
    4. 非待支付状态 → 409。
    """
    try:
        verify_payment_callback(body)
    except HTTPException as exc:
        logger.warning(
            "wechat payment callback rejected: signature invalid order_no=%s", body.order_no
        )
        raise exc
    try:
        async with db.begin():
            result = await db.execute(text("SELECT po.id,po.user_id,po.amount,po.type,po.status,po.order_no,cp.code,cp.duration_days FROM payment_order po JOIN config_membership_package cp ON cp.code=po.product_type WHERE po.order_no=:order_no AND po.type=1 FOR UPDATE"), {"order_no": body.order_no})
            order = result.mappings().first()
            if not order:
                raise HTTPException(404, detail="Membership order not found")
            if order["status"] == 1:
                return
            if order["status"] != 0:
                raise HTTPException(409, detail="Membership order cannot be paid")
            if settings.wechat_payment_mode == "real" and body.amount is None:
                raise HTTPException(409, detail="支付回调缺少金额，无法完成一致性校验")
            if body.amount is not None and Decimal(str(body.amount)) != Decimal(str(order["amount"])):
                raise HTTPException(409, detail="支付回调金额与订单金额不一致")
            start_at = datetime.now(UTC).replace(tzinfo=None)
            end_at = start_at + timedelta(days=int(order["duration_days"]))
            await db.execute(text("UPDATE payment_order SET status=1,pay_time=UTC_TIMESTAMP(),transaction_id=:transaction_id WHERE id=:id"), {"transaction_id": body.transaction_id, "id": order["id"]})
            await db.execute(text("INSERT INTO user_membership (user_id,package_type,amount,order_no,start_at,end_at,status) VALUES (:user_id,:package_type,:amount,:order_no,:start_at,:end_at,1)"), {"user_id": order["user_id"], "package_type": order["code"], "amount": order["amount"], "order_no": order["order_no"], "start_at": start_at, "end_at": end_at})
    except HTTPException as exc:
        if exc.status_code in (404, 409):
            await _record_callback_anomaly(db, body, f"{exc.status_code}:{exc.detail}")
        raise

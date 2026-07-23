import json
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.membership import CreateMembershipOrderRequest, MembershipHistoryItem, MembershipHistoryPage, MembershipOrderResponse, MembershipPackage, MembershipStatus

DEFAULT_RIGHTS = {"apply_daily_limit": 3, "superlike_daily_limit": 1, "browse_daily_limit": 20, "visitor_detail": False, "browse_history_scope": "today"}


def _rights(value, vip: bool = False) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = {}
    rights = {**DEFAULT_RIGHTS, **(value if isinstance(value, dict) else {})}
    if vip:
        rights.update(apply_daily_limit=10, superlike_daily_limit=3, browse_daily_limit=None, visitor_detail=True, browse_history_scope="all")
    return rights


async def list_packages(db: AsyncSession) -> list[MembershipPackage]:
    result = await db.execute(text("SELECT code,name,duration_days,price,original_price,daily_price,badge,rights FROM config_membership_package WHERE is_active=1 ORDER BY sort,id"))
    return [MembershipPackage(code=r["code"], name=r["name"], duration_days=r["duration_days"], price=float(r["price"]), original_price=float(r["original_price"]) if r["original_price"] is not None else None, daily_price=float(r["daily_price"]) if r["daily_price"] is not None else None, badge=r["badge"], rights=_rights(r["rights"])) for r in result.mappings()]


async def get_status(db: AsyncSession, user_id: int) -> MembershipStatus:
    result = await db.execute(text("SELECT package_type,start_at,end_at FROM user_membership WHERE user_id=:id AND status=1 AND (start_at IS NULL OR start_at<=UTC_TIMESTAMP()) AND (end_at IS NULL OR end_at>UTC_TIMESTAMP()) ORDER BY end_at DESC LIMIT 1"), {"id": user_id})
    row = result.mappings().first()
    return MembershipStatus(is_vip=bool(row), package_type=row["package_type"] if row else None, start_at=row["start_at"] if row else None, end_at=row["end_at"] if row else None, rights=_rights(None, bool(row)))


async def history(db: AsyncSession, user_id: int, page: int, page_size: int) -> MembershipHistoryPage:
    total = int((await db.execute(text("SELECT COUNT(*) FROM user_membership WHERE user_id=:id"), {"id": user_id})).scalar() or 0)
    result = await db.execute(text("SELECT id,package_type,amount,order_no,start_at,end_at,status FROM user_membership WHERE user_id=:id ORDER BY created_at DESC,id DESC LIMIT :limit OFFSET :offset"), {"id": user_id, "limit": page_size, "offset": (page - 1) * page_size})
    now = datetime.now(UTC).replace(tzinfo=None)
    items = [MembershipHistoryItem(id=r["id"], package_type=r["package_type"], amount=float(r["amount"]) if r["amount"] is not None else None, order_no=r["order_no"], start_at=r["start_at"], end_at=r["end_at"], status=r["status"], is_vip=r["status"] == 1 and (r["end_at"] is None or r["end_at"] > now), rights=_rights(None, r["status"] == 1)) for r in result.mappings()]
    return MembershipHistoryPage(items=items, page=page, page_size=page_size, total=total, has_more=page * page_size < total)


async def create_order(db: AsyncSession, user_id: int, body: CreateMembershipOrderRequest, idempotency_key: str | None) -> MembershipOrderResponse:
    raise HTTPException(403, detail="会员购买功能暂未开放")
    if not idempotency_key or len(idempotency_key) > 128:
        raise HTTPException(422, detail="请提供有效的 Idempotency-Key")
    existing = await db.execute(text("SELECT order_no,product_type,product_name,amount,pay_type,status,expire_at FROM payment_order WHERE user_id=:uid AND idempotency_key=:key"), {"uid": user_id, "key": idempotency_key})
    row = existing.mappings().first()
    if row:
        return MembershipOrderResponse(order_no=row["order_no"], package_code=str(row["product_type"]), product_name=row["product_name"], amount=float(row["amount"]), pay_type=row["pay_type"], status=row["status"], expire_at=row["expire_at"], payment_required=row["status"] == 0)
    package = (await db.execute(text("SELECT id,code,name,price FROM config_membership_package WHERE code=:code AND is_active=1"), {"code": body.package_code})).mappings().first()
    if not package:
        raise HTTPException(404, detail="会员套餐不存在或已下架")
    order_no = f"VIP{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}{secrets.token_hex(5).upper()}"
    expire_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=30)
    await db.execute(text("INSERT INTO payment_order (user_id,order_no,type,product_id,product_type,product_name,amount,pay_type,status,expire_at,idempotency_key) VALUES (:uid,:order_no,1,:pid,1,:code,:amount,1,0,:expire_at,:key)"), {"uid": user_id, "order_no": order_no, "pid": package["id"], "code": package["code"], "amount": package["price"], "expire_at": expire_at, "key": idempotency_key})
    await db.commit()
    return MembershipOrderResponse(order_no=order_no, package_code=package["code"], product_name=package["name"], amount=float(package["price"]), pay_type=1, status=0, expire_at=expire_at, payment_required=True)


async def get_order(db: AsyncSession, user_id: int, order_no: str) -> MembershipOrderResponse:
    row = (await db.execute(text("SELECT product_type,product_name,amount,pay_type,status,expire_at,order_no FROM payment_order WHERE user_id=:uid AND order_no=:order_no"), {"uid": user_id, "order_no": order_no})).mappings().first()
    if not row:
        raise HTTPException(404, detail="订单不存在")
    return MembershipOrderResponse(order_no=row["order_no"], package_code=str(row["product_type"]), product_name=row["product_name"], amount=float(row["amount"]), pay_type=row["pay_type"], status=row["status"], expire_at=row["expire_at"], payment_required=row["status"] == 0)


async def handle_wechat_callback(db: AsyncSession, body) -> None:
    """Keep the grant boundary closed until WeChat API v3 keys are configured."""
    raise HTTPException(503, detail="微信支付回调验签服务尚未配置")

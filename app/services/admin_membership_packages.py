"""Admin editing for the membership catalog used by the payment flow."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.schemas.admin_config import AdminMembershipPackage, AdminMembershipPackageUpdate


def _as_package(row: Any) -> AdminMembershipPackage:
    code = str(row["code"])
    raw_price = float(row["price"])
    effective = settings.membership_price_override(code, "price", raw_price)
    overridden = settings.membership_price_override(code, "price", None) is not None
    rights = row["rights"]
    if isinstance(rights, str):
        try:
            rights = json.loads(rights)
        except json.JSONDecodeError:
            rights = {}
    if not isinstance(rights, dict):
        rights = {}
    return AdminMembershipPackage(
        code=code, name=row["name"], duration_days=int(row["duration_days"]),
        price=raw_price,
        original_price=float(row["original_price"]) if row["original_price"] is not None else None,
        daily_price=float(row["daily_price"]) if row["daily_price"] is not None else None,
        sort=int(row["sort"] or 0), is_active=bool(row["is_active"]),
        is_recommend=bool(row["is_recommend"]), badge=row["badge"], rights=rights,
        effective_price=float(effective if effective is not None else raw_price),
        price_overridden_by_environment=overridden,
    )


async def list_admin_membership_packages(db: AsyncSession) -> list[AdminMembershipPackage]:
    result = await db.execute(text("""SELECT code,name,duration_days,price,original_price,daily_price,
        sort,is_active,is_recommend,badge,rights FROM config_membership_package ORDER BY sort,id"""))
    return [_as_package(row) for row in result.mappings()]


async def update_admin_membership_package(
    db: AsyncSession, admin_id: int, code: str, body: AdminMembershipPackageUpdate
) -> AdminMembershipPackage:
    row = (await db.execute(
        text("""SELECT code,name,duration_days,price,original_price,daily_price,sort,is_active,
        is_recommend,badge,rights FROM config_membership_package WHERE code=:code FOR UPDATE"""),
        {"code": code},
    )).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="会员套餐不存在；套餐编码不可新增或删除")
    if body.original_price is not None and body.original_price < body.price:
        raise HTTPException(status_code=422, detail="劃线原价不能低于实际售价")
    if settings.membership_price_override(code, "price", None) is not None:
        raise HTTPException(status_code=409, detail="该套餐价格由服务器环境变量锁定，请修改服务器价格配置")
    before = _as_package(row).model_dump(mode="json", exclude={"effective_price", "price_overridden_by_environment"})
    await db.execute(text("""UPDATE config_membership_package SET name=:name,duration_days=:duration_days,
        price=:price,original_price=:original_price,daily_price=:daily_price,sort=:sort,is_active=:is_active,
        is_recommend=:is_recommend,badge=:badge,rights=:rights WHERE code=:code"""), {
        "code": code, "name": body.name.strip(), "duration_days": body.duration_days,
        "price": Decimal(str(body.price)),
        "original_price": Decimal(str(body.original_price)) if body.original_price is not None else None,
        "daily_price": Decimal(str(body.daily_price)) if body.daily_price is not None else None,
        "sort": body.sort, "is_active": int(body.is_active), "is_recommend": int(body.is_recommend),
        "badge": body.badge.strip() if body.badge else None,
        "rights": json.dumps(body.rights, ensure_ascii=False),
    })
    after = body.model_dump(mode="json")
    after["code"] = code
    await db.execute(text("""INSERT INTO admin_config_audit_log
        (namespace,version,action,actor_user_id,change_summary,before_config_json,after_config_json)
        VALUES ('membership_packages',1,'patch',:actor,:summary,:before,:after)"""), {
        "actor": admin_id, "summary": f"更新 VIP 套餐 {code}",
        "before": json.dumps(before, ensure_ascii=False),
        "after": json.dumps(after, ensure_ascii=False),
    })
    await db.commit()
    updated = (await db.execute(text("""SELECT code,name,duration_days,price,original_price,daily_price,
        sort,is_active,is_recommend,badge,rights FROM config_membership_package WHERE code=:code"""),
        {"code": code})).mappings().first()
    return _as_package(updated)

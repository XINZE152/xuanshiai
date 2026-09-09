"""Back-office management for the 服务红娘 分成级别 page.

The page shows a fixed-size table of `commission_level` rows (junior /
intermediate / senior / partner) plus an inline editor. Writes go through
the standard `business_audit_log` channel so back-office operations are
auditable.
"""

import json
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.matchmaker_admin import (
    CommissionLevel,
    CommissionLevelUpdate,
)


_SELECT = """SELECT cl.id, cl.code, cl.name, cl.mode, cl.rate_percent, cl.fixed_amount,
    cl.platform_extra_amount, cl.promotion_condition, cl.sort, cl.status,
    COALESCE(profile.matchmaker_count, 0) AS applicable_matchmaker_count,
    cl.created_at, cl.updated_at
    FROM commission_level cl
    LEFT JOIN (
        SELECT commission_level_id, COUNT(*) AS matchmaker_count
        FROM matchmaker_profile
        WHERE deleted_at IS NULL AND locked = 0
        GROUP BY commission_level_id
    ) profile ON profile.commission_level_id = cl.id"""


def _coerce(row: dict[str, Any]) -> CommissionLevel:
    payload = dict(row)
    payload.setdefault("fixed_amount", None)
    payload.setdefault("promotion_condition", None)
    payload.setdefault("updated_by", None)
    payload.setdefault("applicable_matchmaker_count", 0)
    for key in ("rate_percent", "platform_extra_amount", "fixed_amount"):
        if payload.get(key) is not None and not isinstance(payload[key], Decimal):
            payload[key] = Decimal(str(payload[key]))
    return CommissionLevel.model_validate(payload)


async def list_levels(db: AsyncSession) -> list[CommissionLevel]:
    rows = (await db.execute(text(f"{_SELECT} ORDER BY cl.sort, cl.id"))).mappings().all()
    return [_coerce(dict(row)) for row in rows]


async def get_level(db: AsyncSession, level_id: int) -> CommissionLevel:
    row = (
        await db.execute(text(f"{_SELECT} WHERE cl.id = :id"), {"id": level_id})
    ).mappings().first()
    if not row:
        raise HTTPException(404, detail="分成级别不存在")
    return _coerce(dict(row))


async def update_level(
    db: AsyncSession,
    admin_id: int,
    level_id: int,
    body: CommissionLevelUpdate,
) -> CommissionLevel:
    await get_level(db, level_id)
    values = body.model_dump(exclude_unset=True, exclude_none=True)
    if not values:
        return await get_level(db, level_id)

    # 业务约束：mode=rate 必须有 rate_percent；mode=fixed 必须有 fixed_amount
    if values.get("mode") == "rate":
        values.setdefault("rate_percent", Decimal("0"))
        values["fixed_amount"] = None
    elif values.get("mode") == "fixed":
        if values.get("fixed_amount") is None:
            raise HTTPException(400, detail="mode=fixed 时必须填写 fixed_amount")
        values["rate_percent"] = Decimal("0")

    # 序列化 Decimal → str，方便 SQLAlchemy 走 aiomysql 的 Decimal 通道
    for key in ("rate_percent", "fixed_amount", "platform_extra_amount"):
        if key in values and isinstance(values[key], Decimal):
            values[key] = str(values[key])

    assignments = ", ".join(f"`{key}` = :{key}" for key in values)
    await db.execute(
        text(
            f"UPDATE commission_level SET {assignments}, updated_at = UTC_TIMESTAMP() "
            "WHERE id = :id"
        ),
        {**values, "id": level_id},
    )
    await db.execute(
        text(
            """INSERT INTO business_audit_log
                (actor_user_id, action, resource_type, resource_id, after_json)
                VALUES (:actor, 'commission_level.update', 'commission_level', :id, :after_json)"""
        ),
        {
            "actor": admin_id,
            "id": level_id,
            "after_json": json.dumps(values, ensure_ascii=False, default=str),
        },
    )
    await db.commit()
    return await get_level(db, level_id)
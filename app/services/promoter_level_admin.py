"""Service for the 推广红娘 → 分成配置 (4 固定级别) back-office page."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.promoter_level_admin import (
    PromoterLevelItem,
    PromoterLevelPage,
    PromoterLevelUpdate,
)

LEVELS = {1, 2, 3, 4}


def _dt(value: Any):
    if value is None:
        return None
    return value if hasattr(value, "isoformat") else value


def _num(value: Any) -> str:
    """数字展示口径：去尾零且绝不使用科学计数法（Decimal.normalize 会产生 4E+1）。"""
    if value is None:
        return "0"
    text_value = format(Decimal(str(value)), "f")
    if "." in text_value:
        text_value = text_value.rstrip("0").rstrip(".")
    return text_value or "0"


def _auto_split_label(row: dict[str, Any]) -> str:
    mode = row.get("auto_split_mode")
    if mode == "auto_rate":
        rate = row.get("auto_split_rate")
        if rate is None:
            return "按同比自动计算"
        return f"按同比自动计算：{_num(rate)}%"
    return "自定义固定金额"


def _promote_text(level_id: int, threshold: int | None) -> str:
    if threshold is None or threshold == 0:
        return "默认"
    return f"累计发展有效相亲会员数量>={threshold}人"


_SELECT = """SELECT c.id, c.level_id, c.level_name,
    c.auto_split_mode, c.auto_split_rate,
    c.promote_threshold,
    c.register_reward_male, c.register_reward_female,
    c.consume_commission_mode, c.consume_commission_rate,
    c.updated_at,
    (SELECT COUNT(*) FROM user_matchmaker_apply ma
        WHERE ma.application_type = 'promoter'
          AND ma.status = 1
          AND ma.commission_level_id = c.level_id) AS matchmaker_count
    FROM promoter_level_config c"""


def _item(row: dict[str, Any]) -> PromoterLevelItem:
    return PromoterLevelItem(
        id=int(row["id"]),
        level_id=int(row["level_id"]),
        level_name=row["level_name"],
        auto_split_mode=row["auto_split_mode"],
        auto_split_mode_label=_auto_split_label(row),
        auto_split_rate=Decimal(str(row["auto_split_rate"])) if row.get("auto_split_rate") is not None else None,
        promote_threshold=int(row["promote_threshold"]) if row.get("promote_threshold") is not None else None,
        promote_threshold_text=_promote_text(int(row["level_id"]), int(row["promote_threshold"]) if row.get("promote_threshold") is not None else None),
        matchmaker_count=int(row.get("matchmaker_count") or 0),
        register_reward_male=Decimal(str(row.get("register_reward_male") or 0)),
        register_reward_female=Decimal(str(row.get("register_reward_female") or 0)),
        consume_commission_mode=row.get("consume_commission_mode") or "none",
        consume_commission_rate=Decimal(str(row["consume_commission_rate"])) if row.get("consume_commission_rate") is not None else None,
        updated_at=row.get("updated_at"),
    )


async def list_levels(db: AsyncSession) -> PromoterLevelPage:
    rows = (await db.execute(text(f"{_SELECT} ORDER BY c.level_id"))).mappings().all()
    return PromoterLevelPage(items=[_item(dict(row)) for row in rows])


async def get_level(db: AsyncSession, level_id: int) -> PromoterLevelItem:
    if level_id not in LEVELS:
        raise HTTPException(404, detail="分成级别不存在")
    row = (
        await db.execute(text(f"{_SELECT} WHERE c.level_id = :id"), {"id": level_id})
    ).mappings().first()
    if not row:
        raise HTTPException(404, detail="分成级别不存在")
    return _item(dict(row))


async def update_level(
    db: AsyncSession,
    actor_account_id: int,
    level_id: int,
    body: PromoterLevelUpdate,
) -> PromoterLevelItem:
    if level_id not in LEVELS:
        raise HTTPException(404, detail="分成级别不存在")
    values = body.model_dump(exclude_unset=True)
    # 业务约束：consume_commission_mode=auto_rate 时必须填 rate
    new_mode = values.get("consume_commission_mode")
    if new_mode == "auto_rate" and values.get("consume_commission_rate") is None:
        # 没传 rate 时尝试取现有值；若无现存值则要求
        existing = await get_level(db, level_id)
        if existing.consume_commission_rate is None:
            raise HTTPException(400, detail="会员消费分成为按比例时必须填写比例")
    if new_mode == "none":
        values["consume_commission_rate"] = None
    if values.get("promote_threshold") is not None and values.get("promote_threshold") == 0:
        # 0 等同于不限制，按 NULL 存储
        values["promote_threshold"] = None
    for key in ("register_reward_male", "register_reward_female", "consume_commission_rate"):
        if key in values and isinstance(values[key], Decimal):
            values[key] = str(values[key])
    values["updated_by"] = actor_account_id
    assignments = ", ".join(f"`{key}` = :{key}" for key in values)
    await db.execute(
        text(f"UPDATE promoter_level_config SET {assignments} WHERE level_id = :level_id"),
        {**values, "level_id": level_id},
    )
    await db.commit()
    return await get_level(db, level_id)

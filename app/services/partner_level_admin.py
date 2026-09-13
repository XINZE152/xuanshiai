"""Service for the 合伙红娘 → 分成配置 (3 固定级别) back-office page."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.partner_level_admin import (
    PartnerBonusItem,
    PartnerLevelItem,
    PartnerLevelPage,
    PartnerLevelUpdate,
)

LEVELS = {1, 2, 3}


def _num(value: Any) -> str:
    """数字展示口径：去尾零且绝不使用科学计数法（Decimal.normalize 会产生 4E+1）。"""
    if value is None:
        return "0"
    text_value = format(Decimal(str(value)), "f")
    if "." in text_value:
        text_value = text_value.rstrip("0").rstrip(".")
    return text_value or "0"


def _split_label(row: dict[str, Any]) -> str:
    if row.get("auto_split_mode") == "auto_rate":
        rate = row.get("auto_split_rate")
        if rate is None:
            return "按比例自动计算"
        return f"按比例自动计算：{_num(rate)}%"
    return "自定义固定金额"


def _condition_text(row: dict[str, Any]) -> str:
    performance = row.get("promote_performance_threshold")
    members = row.get("promote_member_threshold")
    parts: list[str] = []
    if performance is not None:
        parts.append(f"团队累计业绩>={_num(performance)}元")
    if members is not None:
        parts.append(f"团队累计发展有效相亲会员数量>={int(members)}人")
    if not parts:
        return "默认"
    return " 或 ".join(parts)


def _bonus_items(value: Any) -> list[PartnerBonusItem]:
    """json 列在不同驱动下可能是 str / list / None，统一容错解析。"""
    if value is None:
        return []
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return []
    if not isinstance(value, list):
        return []
    items: list[PartnerBonusItem] = []
    for raw in value:
        if not isinstance(raw, dict) or "name" not in raw:
            continue
        items.append(
            PartnerBonusItem(
                name=str(raw["name"]),
                amount=Decimal(str(raw.get("amount") or 0)),
            )
        )
    return items


_SELECT = """SELECT c.id, c.level_id, c.level_name,
    c.auto_split_mode, c.auto_split_rate,
    c.promote_performance_threshold, c.promote_member_threshold,
    c.register_reward_male, c.register_reward_female, c.promoter_join_reward,
    c.consume_commission_mode, c.consume_commission_rate,
    c.share_bonus, c.bonus_items, c.updated_at,
    (SELECT COUNT(*) FROM partner_team t
        WHERE t.level_id = c.level_id AND t.status = 1) AS partner_count
    FROM partner_level_config c"""


def _item(row: dict[str, Any]) -> PartnerLevelItem:
    return PartnerLevelItem(
        id=int(row["id"]),
        level_id=int(row["level_id"]),
        level_name=row["level_name"],
        auto_split_mode=row["auto_split_mode"],
        auto_split_mode_label=_split_label(row),
        auto_split_rate=Decimal(str(row["auto_split_rate"])) if row.get("auto_split_rate") is not None else None,
        promote_performance_threshold=(
            Decimal(str(row["promote_performance_threshold"]))
            if row.get("promote_performance_threshold") is not None
            else None
        ),
        promote_member_threshold=(
            int(row["promote_member_threshold"]) if row.get("promote_member_threshold") is not None else None
        ),
        promote_condition_text=_condition_text(row),
        partner_count=int(row.get("partner_count") or 0),
        register_reward_male=Decimal(str(row.get("register_reward_male") or 0)),
        register_reward_female=Decimal(str(row.get("register_reward_female") or 0)),
        promoter_join_reward=Decimal(str(row.get("promoter_join_reward") or 0)),
        consume_commission_mode=row.get("consume_commission_mode") or "none",
        consume_commission_rate=(
            Decimal(str(row["consume_commission_rate"]))
            if row.get("consume_commission_rate") is not None
            else None
        ),
        share_bonus=bool(int(row["share_bonus"])) if row.get("share_bonus") is not None else True,
        bonus_items=_bonus_items(row.get("bonus_items")),
        updated_at=row.get("updated_at"),
    )


async def list_levels(db: AsyncSession) -> PartnerLevelPage:
    rows = (await db.execute(text(f"{_SELECT} ORDER BY c.level_id"))).mappings().all()
    return PartnerLevelPage(items=[_item(dict(row)) for row in rows])


async def get_level(db: AsyncSession, level_id: int) -> PartnerLevelItem:
    if level_id not in LEVELS:
        raise HTTPException(404, detail="合伙分成级别不存在")
    row = (
        await db.execute(text(f"{_SELECT} WHERE c.level_id = :id"), {"id": level_id})
    ).mappings().first()
    if not row:
        raise HTTPException(404, detail="合伙分成级别不存在")
    return _item(dict(row))


async def update_level(
    db: AsyncSession,
    actor_account_id: int,
    level_id: int,
    body: PartnerLevelUpdate,
) -> PartnerLevelItem:
    if level_id not in LEVELS:
        raise HTTPException(404, detail="合伙分成级别不存在")
    existing = await get_level(db, level_id)
    values = body.model_dump(exclude_unset=True)

    # 分成为「按比例」时必须能取到比例（新值或既有值）
    new_mode = values.get("consume_commission_mode")
    if new_mode == "auto_rate" and values.get("consume_commission_rate") is None:
        if existing.consume_commission_rate is None:
            raise HTTPException(400, detail="会员消费分成为按比例时必须填写比例")
    if new_mode == "none":
        values["consume_commission_rate"] = None

    new_split = values.get("auto_split_mode")
    if new_split == "auto_rate" and values.get("auto_split_rate") is None:
        if existing.auto_split_rate is None:
            raise HTTPException(400, detail="分成模式为按比例自动计算时必须填写比例")

    # 0 等同不限制，按 NULL 存储，与推广红娘侧口径一致
    for key in ("promote_performance_threshold", "promote_member_threshold"):
        if key in values and values[key] == 0:
            values[key] = None

    if "share_bonus" in values and values["share_bonus"] is not None:
        values["share_bonus"] = int(bool(values["share_bonus"]))
    if "bonus_items" in values and values["bonus_items"] is not None:
        values["bonus_items"] = json.dumps(
            [
                {"name": item.name, "amount": f"{Decimal(str(item.amount)):.2f}"}
                for item in body.bonus_items or []
            ],
            ensure_ascii=False,
        )
    for key in (
        "auto_split_rate",
        "promote_performance_threshold",
        "register_reward_male",
        "register_reward_female",
        "promoter_join_reward",
        "consume_commission_rate",
    ):
        if key in values and isinstance(values[key], Decimal):
            values[key] = str(values[key])

    if values:
        values["updated_by"] = actor_account_id
        assignments = ", ".join(f"`{key}` = :{key}" for key in values)
        await db.execute(
            text(f"UPDATE partner_level_config SET {assignments} WHERE level_id = :level_id"),
            {**values, "level_id": level_id},
        )
        await db.execute(
            text("""INSERT INTO business_audit_log
                (actor_user_id, action, resource_type, resource_id, after_json)
                VALUES (:actor, 'partner_level.update', 'partner_level_config', :level_id, :after_json)"""),
            {
                "actor": actor_account_id,
                "level_id": level_id,
                "after_json": json.dumps(
                    {"level_id": level_id, **{k: str(v) for k, v in values.items() if k != "updated_by"}},
                    ensure_ascii=False,
                ),
            },
        )
        await db.commit()
    return await get_level(db, level_id)

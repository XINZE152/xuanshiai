"""客源线索联系方式唯一性守卫。

业务口径：同一条"有效"客源线索（状态非 LOST/CLOSED）不允许与其他有效线索
使用相同的手机号或微信号；弃海、已关闭线索不占用联系方式，允许重新录入。
数据库层由 customer_lead 的生成列唯一索引兜底，本模块负责给出可读的 409。
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

# 与 customer_lead 生成列 active_phone/active_wechat 的定义保持一致。
LEAD_INACTIVE_STATUSES = ("LOST", "CLOSED")

DUPLICATE_CONTACT_DETAIL = "该联系方式已存在有效客源线索"


def normalize_contact(value: Any) -> str | None:
    """联系方式归一化：去空白，空串视为未提供（None）。"""
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped if stripped else None


async def find_conflicting_lead_id(
    db: AsyncSession,
    phone: str | None,
    wechat: str | None,
    *,
    exclude_lead_id: int | None = None,
) -> int | None:
    """返回占用该联系方式的有效线索 id；phone/wechat 均为空或无冲突时返回 None。"""
    conditions: list[str] = []
    params: dict[str, Any] = {}
    if normalize_contact(phone):
        conditions.append("phone = :conflict_phone")
        params["conflict_phone"] = normalize_contact(phone)
    if normalize_contact(wechat):
        conditions.append("wechat = :conflict_wechat")
        params["conflict_wechat"] = normalize_contact(wechat)
    if not conditions:
        return None
    if exclude_lead_id is not None:
        conditions.append("id != :conflict_exclude_id")
        params["conflict_exclude_id"] = exclude_lead_id
    exclusion = ", ".join(f"'{status}'" for status in LEAD_INACTIVE_STATUSES)
    query = (
        f"SELECT id FROM customer_lead WHERE status NOT IN ({exclusion}) "
        f"AND ({' OR '.join(conditions)}) ORDER BY id LIMIT 1"
    )
    return await db.scalar(text(query), params)


async def ensure_contact_available(
    db: AsyncSession,
    phone: str | None,
    wechat: str | None,
    *,
    exclude_lead_id: int | None = None,
    detail: str = DUPLICATE_CONTACT_DETAIL,
) -> None:
    """联系方式已被有效线索占用时抛出 409。"""
    conflicting_id = await find_conflicting_lead_id(db, phone, wechat, exclude_lead_id=exclude_lead_id)
    if conflicting_id:
        raise HTTPException(409, detail=f"{detail}（ID: {conflicting_id}）")


def raise_duplicate_contact(conflict: IntegrityError | None = None) -> None:
    """唯一索引并发兜底：数据库层撞唯一键时转成 409。"""
    raise HTTPException(409, detail=DUPLICATE_CONTACT_DETAIL)

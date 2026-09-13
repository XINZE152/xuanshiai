"""CRUD service for the generic ``admin_content_item`` table.

Each operational domain (activities, merchants, short videos, gifts, QR codes,
sales libraries, ...) stores rows here with a ``domain`` discriminator, so the
operations console pages can be wired without bespoke tables per feature.
Domain-specific fields live in ``extra_json`` and are validated as loose dicts —
the console renders them from declarative field descriptors.
"""

from __future__ import annotations

import json
import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.admin_content import ContentItem, ContentItemCreate, ContentItemPage, ContentItemUpdate

DOMAIN_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

# 允许写入的域白名单：运营工具、活动、商家、短视频、分店、合伙红娘、会员等。
ALLOWED_DOMAINS: frozenset[str] = frozenset(
    {
        "activity",
        "activity_signup",
        "mutual_activity",
        "mutual_signup",
        "merchant",
        "merchant_product",
        "merchant_order",
        "short_video",
        "video_red_packet",
        "video_comment",
        "video_homepage",
        "video_tip",
        "free_pay_order",
        "sales_library",
        "single_page",
        "landing_page",
        "free_form",
        "lovecard_batch",
        "fan_qrcode",
        "gift",
        "gift_record",
        "gift_exchange",
        "branch_site",
        "branch_matchmaker",
        "branch_report",
        "branch_distribution",
        "partner",
        "partner_relation",
        "partner_bonus_detail",
        "promoter_distribution",
        "offline_vip",
        "vip_contract",
        "popularize_record",
        "good_news",
        "good_news_pennant",
        "community_group",
        "group_signup",
        "interactive_message",
        "tweet_task",
        "sms_broadcast",
        "sms_send_record",
    }
)


def _ensure_domain(domain: str) -> None:
    if not DOMAIN_PATTERN.match(domain) or domain not in ALLOWED_DOMAINS:
        raise HTTPException(404, detail="未知的内容域")


def _row_to_item(row: Any) -> ContentItem:
    mapping = dict(row)
    try:
        extra = json.loads(mapping.get("extra_json") or "{}")
        if not isinstance(extra, dict):
            extra = {}
    except (TypeError, ValueError):
        extra = {}
    return ContentItem(
        id=int(mapping["id"]),
        domain=str(mapping["domain"]),
        title=str(mapping.get("title") or ""),
        subtitle=mapping.get("subtitle"),
        image_url=mapping.get("image_url"),
        amount=float(mapping["amount"]) if mapping.get("amount") is not None else None,
        status=int(mapping.get("status") or 1),
        sort=int(mapping.get("sort") or 0),
        extra=extra,
        created_at=mapping.get("created_at"),
        updated_at=mapping.get("updated_at"),
    )


def _item_to_row(item: ContentItem) -> dict[str, Any]:
    """拍平：extra 字段提升到行顶层，供管理端表格列直接读取。"""
    row: dict[str, Any] = {
        "id": item.id,
        "domain": item.domain,
        "title": item.title,
        "subtitle": item.subtitle,
        "image_url": item.image_url,
        "amount": item.amount,
        "status": item.status,
        "sort": item.sort,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }
    row.update(item.extra)
    return row


async def list_items(
    db: AsyncSession,
    domain: str,
    page: int = 1,
    page_size: int = 20,
    keyword: str | None = None,
    status: int | None = None,
) -> ContentItemPage:
    _ensure_domain(domain)
    conditions = ["tenant_id = 1", "domain = :domain"]
    params: dict[str, Any] = {"domain": domain}
    if keyword:
        conditions.append("(title LIKE :kw OR subtitle LIKE :kw OR extra_json LIKE :kw)")
        params["kw"] = f"%{keyword}%"
    if status in (1, 2):
        conditions.append("status = :status")
        params["status"] = status
    where = " AND ".join(conditions)

    total = int(
        (await db.execute(text(f"SELECT COUNT(*) AS c FROM admin_content_item WHERE {where}"), params)).scalar() or 0
    )
    rows = (
        await db.execute(
            text(
                f"SELECT * FROM admin_content_item WHERE {where} "
                "ORDER BY sort DESC, id DESC LIMIT :limit OFFSET :offset"
            ),
            {**params, "limit": page_size, "offset": (page - 1) * page_size},
        )
    ).mappings().all()
    return ContentItemPage(
        total=total,
        page=page,
        page_size=page_size,
        items=[_item_to_row(_row_to_item(r)) for r in rows],
    )


async def create_item(db: AsyncSession, current_admin_id: int, domain: str, body: ContentItemCreate) -> ContentItem:
    _ensure_domain(domain)
    if body.status not in (1, 2):
        raise HTTPException(422, detail="status 仅支持 1 正常 / 2 停用")
    result = await db.execute(
        text(
            "INSERT INTO admin_content_item "
            "(tenant_id, domain, title, subtitle, image_url, amount, status, sort, extra_json, created_by) "
            "VALUES (1, :domain, :title, :subtitle, :image_url, :amount, :status, :sort, :extra, :created_by)"
        ),
        {
            "domain": domain,
            "title": body.title or "",
            "subtitle": body.subtitle,
            "image_url": body.image_url,
            "amount": body.amount,
            "status": body.status,
            "sort": body.sort,
            "extra": json.dumps(body.extra or {}, ensure_ascii=False),
            "created_by": current_admin_id,
        },
    )
    await db.commit()
    item_id = int(result.lastrowid or 0)
    row = (
        await db.execute(text("SELECT * FROM admin_content_item WHERE id = :id"), {"id": item_id})
    ).mappings().first()
    if row is None:
        raise HTTPException(500, detail="内容项创建失败")
    return _row_to_item(row)


async def update_item(db: AsyncSession, domain: str, item_id: int, body: ContentItemUpdate) -> ContentItem:
    _ensure_domain(domain)
    row = (
        await db.execute(
            text("SELECT * FROM admin_content_item WHERE id = :id AND domain = :domain"),
            {"id": item_id, "domain": domain},
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(404, detail="内容项不存在")

    updates: list[str] = []
    params: dict[str, Any] = {"id": item_id}
    if body.title is not None:
        updates.append("title = :title")
        params["title"] = body.title
    if body.subtitle is not None:
        updates.append("subtitle = :subtitle")
        params["subtitle"] = body.subtitle
    if body.image_url is not None:
        updates.append("image_url = :image_url")
        params["image_url"] = body.image_url
    if body.amount is not None:
        updates.append("amount = :amount")
        params["amount"] = body.amount
    if body.status is not None:
        if body.status not in (1, 2):
            raise HTTPException(422, detail="status 仅支持 1 正常 / 2 停用")
        updates.append("status = :status")
        params["status"] = body.status
    if body.sort is not None:
        updates.append("sort = :sort")
        params["sort"] = body.sort
    if body.extra is not None:
        merged = dict(_row_to_item(row).extra)
        merged.update(body.extra)
        updates.append("extra_json = :extra")
        params["extra"] = json.dumps(merged, ensure_ascii=False)
    if not updates:
        return _row_to_item(row)

    await db.execute(
        text(f"UPDATE admin_content_item SET {', '.join(updates)} WHERE id = :id"),
        params,
    )
    await db.commit()
    fresh = (
        await db.execute(text("SELECT * FROM admin_content_item WHERE id = :id"), {"id": item_id})
    ).mappings().first()
    return _row_to_item(fresh)


async def delete_item(db: AsyncSession, domain: str, item_id: int) -> None:
    _ensure_domain(domain)
    result = await db.execute(
        text("DELETE FROM admin_content_item WHERE id = :id AND domain = :domain"),
        {"id": item_id, "domain": domain},
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(404, detail="内容项不存在")

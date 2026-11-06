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
        "free_pay_item",
        "sales_library",
        "single_page",
        "landing_page",
        "free_form",
        "lovecard_batch",
        "fan_qrcode",
        "fan_qrcode_stat",
        "gift",
        "point_gift",
        "gift_record",
        "gift_exchange",
        "point_exchange",
        "econtract_record",
        "econtract_template",
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
        "ad_slot",
        # A11-CMS：系统更新公告 / 使用帮助文档，复用通用内容表（无新表）。
        # 标题=title，正文/分类等放 extra_json（body / category）。
        "update_report",
        "help_doc",
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
    """列表行：保持 extra 嵌套结构，业务字段不拍平到顶层。

    早期实现把 extra 的键提升到行顶层，会覆盖同名行级列（如 good_news 的
    extra.status 是中文状态文案，会把整型 status 列覆盖成字符串），且与
    docs/api/m8-operational-tools.md 约定的响应结构不符。管理端各页面均按
    item.extra 读取业务字段。
    """
    return {
        "id": item.id,
        "domain": item.domain,
        "title": item.title,
        "subtitle": item.subtitle,
        "image_url": item.image_url,
        "amount": item.amount,
        "status": item.status,
        "sort": item.sort,
        "extra": item.extra,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


# 域特有筛选项（落地页 type、内容单页 qrcode_type、落地页/自由表单 xiangqin_status）。
# 这些字段按 admin_content_item 的设计存放在 extra_json，因此筛选走 JSON_EXTRACT；
# 键名与接口参数同名，前端写入 extra 时需要用相同的键。
EXTRA_FILTER_KEYS: frozenset[str] = frozenset(
    {
        "type",
        "qrcode_type",
        "xiangqin_status",
        "good_news_status",
        "matchmaker_id",
        "group_id",
        "pay",
        "nick",
        # A11-CMS：更新公告按分类筛选（category 存于 extra_json）。
        "category",
        # B11④：自由表单(free_form) 的性别/落地页/客户来源筛选（均存于 extra_json）。
        # 此前前端靠把这些字段塞进 keyword 做 JSON 子串匹配（脆弱 hack），现走正式参数。
        "gender",
        "landing_id",
        "source",
        "qrcode_id",
        "broadcast_id",
        # sales_library（销售匹配库/眼缘库）的到店状态与销售红娘姓名筛选（均存于 extra_json）。
        # 此前前端靠把 arrival/matchmaker 塞进 keyword 做 JSON 子串匹配（脆弱 hack，永远零结果），
        # 后改客户端后处理（仅作用当前页），现走正式后端参数实现跨页全局筛选。
        "arrival",
        "matchmaker",
    }
)

# 搜索方式（search_type）：仅在域内定义过的键才会生效，未定义时退回 title/subtitle/extra_json
# 全字段模糊匹配（历史行为）。键名取自各域 extra 的写入约定。
SEARCH_TYPE_EXTRA_KEYS: dict[str, dict[str, str]] = {
    "free_pay_order": {
        "nickname": "member_nickname",
        "phone": "member_phone",
        "order_no": "order_no",
    },
    "gift_exchange": {
        "nickname": "member_nickname",
        "phone": "member_phone",
        "order_no": "order_no",
    },
}


async def list_items(
    db: AsyncSession,
    domain: str,
    page: int = 1,
    page_size: int = 20,
    keyword: str | None = None,
    status: int | None = None,
    extra_filters: dict[str, str] | None = None,
    search_type: str | None = None,
) -> ContentItemPage:
    _ensure_domain(domain)
    conditions = ["tenant_id = 1", "domain = :domain"]
    params: dict[str, Any] = {"domain": domain}
    if keyword:
        search_column = SEARCH_TYPE_EXTRA_KEYS.get(domain, {}).get(search_type or "")
        if search_column:
            # 键名来自服务端白名单，不拼接用户输入；取值走绑定参数。
            conditions.append(f"JSON_UNQUOTE(JSON_EXTRACT(extra_json, '$.{search_column}')) LIKE :kw")
        else:
            conditions.append("(title LIKE :kw OR subtitle LIKE :kw OR extra_json LIKE :kw)")
        params["kw"] = f"%{keyword}%"
    if status in (1, 2):
        conditions.append("status = :status")
        params["status"] = status
    for key, value in (extra_filters or {}).items():
        # 键名来自服务端白名单，不拼接用户输入；取值走绑定参数。
        if key in EXTRA_FILTER_KEYS and value:
            if key in {"good_news_status", "matchmaker_id"} and domain != "good_news":
                continue
            if key in {"group_id", "pay", "nick"} and domain != "group_signup":
                continue
            if key in {"gender", "landing_id", "source"} and domain != "free_form":
                continue
            if key == "broadcast_id" and domain != "sms_send_record":
                continue
            if key == "qrcode_id" and domain != "fan_qrcode_stat":
                continue
            if key in {"arrival", "matchmaker"} and domain != "sales_library":
                continue
            json_key = "status" if key == "good_news_status" else key
            operator = "LIKE" if key in {"nick", "matchmaker"} else "="
            conditions.append(f"JSON_UNQUOTE(JSON_EXTRACT(extra_json, '$.{json_key}')) {operator} :extra_{key}")
            params[f"extra_{key}"] = f"%{value}%" if key in {"nick", "matchmaker"} else value
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


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _good_news_meta_params(news_id: int, extra: dict[str, Any]) -> dict[str, Any]:
    months = _int_or_none(extra.get("meet_duration_months"))
    if months is None:
        match = re.search(r"\d+", str(extra.get("meet_duration") or ""))
        months = int(match.group()) if match else None
    return {
        "news_id": news_id,
        "stage": str(extra.get("stage") or extra.get("status") or ""),
        "review_status": str(extra.get("review_status") or "PENDING"),
        "public_status": str(extra.get("public_status") or "DRAFT"),
        "source_type": str(extra.get("source_type") or "MANUAL"),
        "source_id": _int_or_none(extra.get("source_id")),
        "from_user_id": _int_or_none(extra.get("from_user_id")),
        "to_user_id": _int_or_none(extra.get("to_user_id")),
        "matchmaker_id": _int_or_none(extra.get("matchmaker_id")),
        "months": months,
        "display_mode": str(extra.get("display_mode") or "MASKED"),
        "verification_level": str(extra.get("verification_level") or "SELF_REPORTED"),
    }


async def _insert_good_news_meta(db: AsyncSession, news_id: int, extra: dict[str, Any]) -> None:
    params = _good_news_meta_params(news_id, extra)
    # 通用内容创建入口不允许通过 extra 直接发布；必须走审核/发布接口。
    params["review_status"] = "PENDING"
    params["public_status"] = "DRAFT"
    await db.execute(
        text(
            "INSERT INTO good_news_meta "
            "(news_id, stage, review_status, public_status, source_type, source_id, from_user_id, to_user_id, "
            "matchmaker_id, meet_duration_months, display_mode, verification_level, submitted_by_role) "
            "VALUES (:news_id, :stage, :review_status, :public_status, :source_type, :source_id, :from_user_id, "
            ":to_user_id, :matchmaker_id, :months, :display_mode, :verification_level, 'ADMIN') "
            "ON DUPLICATE KEY UPDATE stage = :stage, meet_duration_months = :months, "
            "display_mode = :display_mode, verification_level = :verification_level"
        ),
        params,
    )


async def _sync_good_news_meta(db: AsyncSession, news_id: int, extra: dict[str, Any]) -> None:
    params = _good_news_meta_params(news_id, extra)
    await db.execute(
        text(
            "UPDATE good_news_meta SET stage = :stage, meet_duration_months = :months, "
            "display_mode = :display_mode, verification_level = :verification_level WHERE news_id = :news_id"
        ),
        params,
    )


_GOOD_NEWS_REVIEW_EXTRA_KEYS = frozenset(
    {
        "stage",
        "status",
        "feedback_name",
        "partner_name",
        "from_user_id",
        "to_user_id",
        "from_avatar",
        "to_avatar",
        "matchmaker_id",
        "matchmaker_name",
        "meet_duration",
        "meet_duration_months",
        "content",
        "content_html",
        "images",
        "blessing",
        "display_mode",
        "verification_level",
        "source_type",
        "source_id",
        "public",
    }
)


def _good_news_review_changed(
    old_extra: dict[str, Any],
    new_extra: dict[str, Any],
    *,
    old_title: str,
    new_title: str,
    old_subtitle: str | None,
    new_subtitle: str | None,
    old_image_url: str | None,
    new_image_url: str | None,
    old_status: int,
    new_status: int,
) -> bool:
    if old_title != new_title:
        return True
    if (old_subtitle or "") != (new_subtitle or ""):
        return True
    if (old_image_url or "") != (new_image_url or ""):
        return True
    if old_status != new_status:
        return True
    return any(old_extra.get(key) != new_extra.get(key) for key in _GOOD_NEWS_REVIEW_EXTRA_KEYS)


async def _reset_good_news_review(
    db: AsyncSession,
    *,
    news_id: int,
    admin_id: int | None,
    reason: str,
) -> None:
    before = (
        await db.execute(
            text("SELECT review_status, public_status FROM good_news_meta WHERE news_id = :news_id"),
            {"news_id": news_id},
        )
    ).mappings().first()
    if before is None:
        return
    before_status = f"{before.get('review_status') or 'PENDING'}/{before.get('public_status') or 'DRAFT'}"
    after_public = "UNPUBLISHED" if str(before.get("public_status") or "") == "PUBLISHED" else "DRAFT"
    after_status = f"PENDING/{after_public}"
    await db.execute(
        text(
            "UPDATE good_news_meta SET review_status = 'PENDING', public_status = :after_public, "
            "published_at = NULL, reviewed_by = NULL, reviewed_at = NULL, reject_reason = NULL "
            "WHERE news_id = :news_id"
        ),
        {"news_id": news_id, "after_public": after_public},
    )
    await db.execute(
        text(
            "UPDATE good_news_report SET status = 'PENDING', reject_reason = NULL, reviewed_at = NULL "
            "WHERE news_id = :news_id"
        ),
        {"news_id": news_id},
    )
    await db.execute(
        text(
            "INSERT INTO good_news_audit "
            "(news_id, actor_user_id, actor_role, action, reason, before_status, after_status) "
            "VALUES (:news_id, :admin_id, 'ADMIN', 'CONTENT_CHANGED', :reason, :before_status, :after_status)"
        ),
        {
            "news_id": news_id,
            "admin_id": admin_id,
            "reason": reason,
            "before_status": before_status,
            "after_status": after_status,
        },
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
    item_id = int(result.lastrowid or 0)
    if domain == "good_news" and item_id > 0:
        await _insert_good_news_meta(db, item_id, body.extra or {})
    await db.commit()
    row = (
        await db.execute(text("SELECT * FROM admin_content_item WHERE id = :id"), {"id": item_id})
    ).mappings().first()
    if row is None:
        raise HTTPException(500, detail="内容项创建失败")
    return _row_to_item(row)


async def update_item(
    db: AsyncSession,
    domain: str,
    item_id: int,
    body: ContentItemUpdate,
    current_admin_id: int | None = None,
) -> ContentItem:
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
    old_extra = dict(_row_to_item(row).extra)
    merged_extra: dict[str, Any] | None = None
    if body.extra is not None:
        merged_extra = dict(old_extra)
        merged_extra.update(body.extra)
        updates.append("extra_json = :extra")
        params["extra"] = json.dumps(merged_extra, ensure_ascii=False)
    if not updates:
        return _row_to_item(row)

    review_changed = False
    if domain == "good_news":
        review_changed = _good_news_review_changed(
            old_extra,
            merged_extra if merged_extra is not None else old_extra,
            old_title=str(row.get("title") or ""),
            new_title=body.title if body.title is not None else str(row.get("title") or ""),
            old_subtitle=row.get("subtitle"),
            new_subtitle=body.subtitle if body.subtitle is not None else row.get("subtitle"),
            old_image_url=row.get("image_url"),
            new_image_url=body.image_url if body.image_url is not None else row.get("image_url"),
            old_status=int(row.get("status") or 1),
            new_status=body.status if body.status is not None else int(row.get("status") or 1),
        )

    await db.execute(
        text(f"UPDATE admin_content_item SET {', '.join(updates)} WHERE id = :id"),
        params,
    )
    if domain == "good_news":
        if merged_extra is not None:
            await _sync_good_news_meta(db, item_id, merged_extra)
        if review_changed:
            await _reset_good_news_review(
                db,
                news_id=item_id,
                admin_id=current_admin_id,
                reason="内容变更，需重新审核",
            )
    await db.commit()
    fresh = (
        await db.execute(text("SELECT * FROM admin_content_item WHERE id = :id"), {"id": item_id})
    ).mappings().first()
    return _row_to_item(fresh)


async def get_item(db: AsyncSession, domain: str, item_id: int) -> ContentItem:
    """A11-CMS：按域 + ID 取单条内容详情（正文在 extra.body）。不存在返回 404。"""
    _ensure_domain(domain)
    row = (
        await db.execute(
            text("SELECT * FROM admin_content_item WHERE id = :id AND domain = :domain"),
            {"id": item_id, "domain": domain},
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(404, detail="内容项不存在")
    return _row_to_item(row)


async def delete_item(db: AsyncSession, domain: str, item_id: int) -> None:
    _ensure_domain(domain)
    result = await db.execute(
        text("DELETE FROM admin_content_item WHERE id = :id AND domain = :domain"),
        {"id": item_id, "domain": domain},
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(404, detail="内容项不存在")

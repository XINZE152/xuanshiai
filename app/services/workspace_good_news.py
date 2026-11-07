"""GOOD-NEWS-20261104: scoped details, audited pennant submissions and blessings."""
import json

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.matchmaker_workspace import (
    WorkspaceGoodNewsBlessingCreate, WorkspaceGoodNewsBlessingItem, WorkspaceGoodNewsBlessingPage,
    WorkspaceGoodNewsBlessingResult, WorkspaceGoodNewsCreated, WorkspaceGoodNewsItem,
    WorkspacePennantCreate, WorkspacePennantItem,
)
from app.services.matchmaker_workspace import (
    GOOD_NEWS_DOMAIN, GOOD_NEWS_PENNANT_DOMAIN, WorkspaceActor, _audit, _content_extra,
    list_workbench_good_news, list_workbench_good_news_pennants,
)

BLESSING_DOMAIN = "good_news_blessing"


async def find_duplicate_submission(
    db: AsyncSession, actor: WorkspaceActor, domain: str, request_id: str,
) -> WorkspaceGoodNewsCreated | None:
    # Serialize this actor's submissions before checking the JSON idempotency key.
    await db.execute(text("SELECT id FROM users WHERE id = :actor FOR UPDATE"), {"actor": actor.user_id})
    existing = (await db.execute(text("""SELECT id, status FROM admin_content_item
        WHERE tenant_id = 1 AND domain = :domain AND created_by = :actor
        AND JSON_UNQUOTE(JSON_EXTRACT(extra_json, '$.client_request_id')) = :request_id LIMIT 1 FOR UPDATE"""),
        {"domain": domain, "actor": actor.user_id, "request_id": request_id})).mappings().first()
    if existing:
        return WorkspaceGoodNewsCreated(id=int(existing["id"]), pending_review=int(existing["status"]) != 1)
    return None


async def good_news_detail(db: AsyncSession, actor: WorkspaceActor, item_id: int) -> WorkspaceGoodNewsItem:
    page = await list_workbench_good_news(db, actor, "", 1, 1, item_id=item_id)
    if not page.items:
        raise HTTPException(404, detail="喜讯不存在或不在服务范围")
    return page.items[0]


async def pennant_detail(db: AsyncSession, actor: WorkspaceActor, item_id: int) -> WorkspacePennantItem:
    page = await list_workbench_good_news_pennants(db, actor, 1, 1, item_id=item_id)
    if not page.items:
        raise HTTPException(404, detail="锦旗不存在或不在服务范围")
    return page.items[0]


async def create_pennant(db: AsyncSession, actor: WorkspaceActor, body: WorkspacePennantCreate) -> WorkspaceGoodNewsCreated:
    duplicate = await find_duplicate_submission(db, actor, GOOD_NEWS_PENNANT_DOMAIN, body.client_request_id)
    if duplicate is not None:
        return duplicate
    extra = {
        "giver": body.giver.strip(), "target": actor.display_name, "target_user_id": actor.user_id,
        "left_words": body.left_words.strip(), "right_words": body.right_words.strip(),
        "words": body.right_words.strip() + "，" + body.left_words.strip(),
        "date_text": body.date_text.strip(), "maker": "平台在线锦旗",
        "consent_confirmed": True, "client_request_id": body.client_request_id,
        "source": "matchmaker_workspace", "organization_id": actor.organization_id,
    }
    result = await db.execute(text("""INSERT INTO admin_content_item
        (tenant_id, domain, title, subtitle, image_url, status, sort, extra_json, created_by)
        VALUES (1, :domain, :title, :target, :image, 2, 0, :extra, :actor)"""), {
        "domain": GOOD_NEWS_PENNANT_DOMAIN, "title": body.giver.strip(), "target": actor.display_name,
        "image": body.image_url, "extra": json.dumps(extra, ensure_ascii=False), "actor": actor.user_id,
    })
    item_id = int(result.lastrowid)
    await _audit(db, actor.user_id, "matchmaker_workspace.pennant.create", "admin_content_item", item_id)
    await db.commit()
    return WorkspaceGoodNewsCreated(id=item_id, pending_review=True)


def _blessing_params(item_id: int, actor: WorkspaceActor) -> dict:
    return {"domain": BLESSING_DOMAIN, "target_id": str(item_id), "actor": actor.user_id}


_BLESSING_FILTER = """tenant_id = 1 AND domain = :domain AND status = 1
    AND JSON_UNQUOTE(JSON_EXTRACT(extra_json, '$.target_id')) = :target_id"""


async def _lock_news(db: AsyncSession, actor: WorkspaceActor, item_id: int) -> dict:
    params: dict = {"id": item_id, "domain": GOOD_NEWS_DOMAIN, "owner": str(actor.user_id)}
    owner = "JSON_UNQUOTE(JSON_EXTRACT(extra_json, '$.matchmaker_id')) = :owner"
    scope = owner if actor.scope == "SELF" else f"(status = 1 OR {owner})"
    row = (await db.execute(text(f"""SELECT id, status, extra_json FROM admin_content_item
        WHERE id = :id AND tenant_id = 1 AND domain = :domain AND status IN (1, 2)
        AND {scope} FOR UPDATE"""), params)).mappings().first()
    if row is None:
        raise HTTPException(404, detail="喜讯不存在或不在服务范围")
    return dict(row)


async def create_blessing(
    db: AsyncSession, actor: WorkspaceActor, item_id: int, body: WorkspaceGoodNewsBlessingCreate,
) -> WorkspaceGoodNewsBlessingResult:
    row = await _lock_news(db, actor, item_id)
    if int(row["status"]) != 1:
        raise HTTPException(409, detail="喜讯待审核，通过后才能送祝福")
    extra = _content_extra(row)
    count = max(0, int(extra.get("blessing_count") or 0))
    params = _blessing_params(item_id, actor)
    # This current read runs under the content-row lock, so concurrent sends cannot both insert.
    exists = await db.scalar(text(f"SELECT id FROM admin_content_item WHERE {_BLESSING_FILTER} AND created_by = :actor LIMIT 1 FOR UPDATE"), params)
    if exists:
        return WorkspaceGoodNewsBlessingResult(blessing_count=count, blessed=True, created=False)
    await db.execute(text("""INSERT INTO admin_content_item
        (tenant_id, domain, title, status, sort, extra_json, created_by)
        VALUES (1, :domain, :message, 1, 0, :extra, :actor)"""), {
        "domain": BLESSING_DOMAIN, "message": body.message,
        "extra": json.dumps({"target_id": item_id, "actor_user_id": actor.user_id}, ensure_ascii=False),
        "actor": actor.user_id,
    })
    extra["blessing_count"] = count + 1
    await db.execute(text("UPDATE admin_content_item SET extra_json = :extra WHERE id = :id"), {
        "id": item_id, "extra": json.dumps(extra, ensure_ascii=False),
    })
    await _audit(db, actor.user_id, "matchmaker_workspace.good_news.bless", "admin_content_item", item_id)
    await db.commit()
    return WorkspaceGoodNewsBlessingResult(blessing_count=count + 1, blessed=True, created=True)


async def list_blessings(
    db: AsyncSession, actor: WorkspaceActor, item_id: int, page: int, page_size: int,
) -> WorkspaceGoodNewsBlessingPage:
    await good_news_detail(db, actor, item_id)
    params = _blessing_params(item_id, actor)
    total = int(await db.scalar(text(f"SELECT COUNT(*) FROM admin_content_item WHERE {_BLESSING_FILTER}"), params) or 0)
    blessed = bool(await db.scalar(text(f"SELECT id FROM admin_content_item WHERE {_BLESSING_FILTER} AND created_by = :actor LIMIT 1"), params))
    rows = (await db.execute(text("""SELECT c.id, c.title AS message, c.created_at,
        COALESCE(u.nickname, '会员') AS nickname, u.avatar FROM admin_content_item c
        LEFT JOIN users u ON u.id = c.created_by
        WHERE c.tenant_id = 1 AND c.domain = :domain AND c.status = 1
        AND JSON_UNQUOTE(JSON_EXTRACT(c.extra_json, '$.target_id')) = :target_id
        ORDER BY c.created_at DESC, c.id DESC LIMIT :limit OFFSET :offset"""), {
        **params, "limit": page_size, "offset": (page - 1) * page_size,
    })).mappings().all()
    return WorkspaceGoodNewsBlessingPage(items=[WorkspaceGoodNewsBlessingItem(**dict(row)) for row in rows],
        total=total, page=page, has_more=page * page_size < total, blessed=blessed)

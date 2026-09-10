"""Data-scope-aware message administration services."""

import json
import re

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentMatchmakerAdmin, CurrentUser
from app.schemas.message_admin import (
    AdminAnnouncementCreate,
    AdminAnnouncementItem,
    AdminMessageItem,
    AdminMessagePage,
)

_PHONE_PATTERN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")


def _redact_content(value: str | None) -> str | None:
    if value is None:
        return None
    return _PHONE_PATTERN.sub("1**********", value)


def _message_scope(admin: CurrentMatchmakerAdmin, params: dict[str, object]) -> str:
    account = admin.account
    if account.data_scope == "ALL" or "*" in admin.permissions:
        return "1=1"
    if account.data_scope == "SELF":
        if account.matchmaker_user_id is None:
            return "1=0"
        params["scope_matchmaker_id"] = account.matchmaker_user_id
        return "EXISTS (SELECT 1 FROM resource_assignment scope_assignment WHERE scope_assignment.status=1 AND scope_assignment.matchmaker_id=:scope_matchmaker_id AND scope_assignment.user_id IN (chat_message.from_user_id, chat_message.to_user_id))"
    if account.organization_id is None:
        return "1=0"
    params["scope_organization_id"] = account.organization_id
    return "EXISTS (SELECT 1 FROM resource_assignment scope_assignment WHERE scope_assignment.status=1 AND scope_assignment.organization_id IN (SELECT id FROM organization WHERE id=:scope_organization_id OR parent_id=:scope_organization_id) AND scope_assignment.user_id IN (chat_message.from_user_id, chat_message.to_user_id))"


def _message_item(row: dict) -> AdminMessageItem:
    data = dict(row)
    data["content"] = _redact_content(data.get("content"))
    return AdminMessageItem(**data)


async def list_admin_messages(
    db: AsyncSession,
    admin: CurrentUser,
    page: int,
    page_size: int,
    user_id: int | None = None,
    session_id: int | None = None,
    message_type: int | None = None,
) -> AdminMessagePage:
    params: dict[str, object] = {"limit": page_size, "offset": (page - 1) * page_size}
    where = [_message_scope(admin, params)]
    if user_id is not None:
        where.append("(chat_message.from_user_id=:user_id OR chat_message.to_user_id=:user_id)")
        params["user_id"] = user_id
    if session_id is not None:
        where.append("chat_message.session_id=:session_id")
        params["session_id"] = session_id
    if message_type is not None:
        where.append("chat_message.type=:message_type")
        params["message_type"] = message_type
    clause = " AND ".join(where)
    rows = (await db.execute(text(f"""SELECT id, session_id, from_user_id, to_user_id, type,
        content, media_url, is_read, revoked_at, created_at FROM chat_message
        WHERE {clause} ORDER BY id DESC LIMIT :limit OFFSET :offset"""), params)).mappings().all()
    count_params = {key: value for key, value in params.items() if key not in {"limit", "offset"}}
    total = int((await db.execute(text(f"SELECT COUNT(*) FROM chat_message WHERE {clause}"), count_params)).scalar() or 0)
    return AdminMessagePage(
        items=[_message_item(dict(row)) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
    )


async def moderate_admin_message(
    db: AsyncSession,
    admin: CurrentMatchmakerAdmin,
    message_id: int,
    action: str,
    reason: str,
) -> AdminMessageItem:
    params: dict[str, object] = {"id": message_id}
    row = (await db.execute(text("""SELECT id, session_id, from_user_id, to_user_id, type,
        content, media_url, is_read, revoked_at, created_at FROM chat_message
        WHERE id=:id FOR UPDATE"""), params)).mappings().first()
    if not row:
        raise HTTPException(404, detail="\u6d88\u606f\u4e0d\u5b58\u5728")
    before = dict(row)
    if action == "recall":
        await db.execute(text("UPDATE chat_message SET revoked_at=COALESCE(revoked_at, UTC_TIMESTAMP()) WHERE id=:id"), {"id": message_id})
    elif action == "restore":
        await db.execute(text("UPDATE chat_message SET revoked_at=NULL WHERE id=:id"), {"id": message_id})
    else:
        raise HTTPException(422, detail="\u6d88\u606f\u5904\u7f6e\u52a8\u4f5c\u4ec5\u652f\u6301 recall \u6216 restore")
    updated = (await db.execute(text("""SELECT id, session_id, from_user_id, to_user_id, type,
        content, media_url, is_read, revoked_at, created_at FROM chat_message WHERE id=:id"""), {"id": message_id})).mappings().one()
    await db.execute(text("""INSERT INTO business_audit_log
        (actor_user_id, action, resource_type, resource_id, before_json, after_json, reason)
        VALUES (:actor, :action, 'chat_message', :id, :before_json, :after_json, :reason)"""), {
        "actor": admin.id,
        "action": f"message.{action}",
        "id": message_id,
        "before_json": json.dumps({**before, "content": _redact_content(before.get("content"))}, ensure_ascii=False, default=str),
        "after_json": json.dumps({**dict(updated), "content": _redact_content(updated.get("content"))}, ensure_ascii=False, default=str),
        "reason": reason,
    })
    await db.commit()
    return _message_item(dict(updated))


async def create_admin_announcement(
    db: AsyncSession,
    admin: CurrentMatchmakerAdmin,
    body: AdminAnnouncementCreate,
) -> AdminAnnouncementItem:
    if admin.account.data_scope != "ALL" and "*" not in admin.permissions:
        raise HTTPException(403, detail="\u516c\u544a\u53d1\u5e03\u4ec5\u5141\u8bb8\u5168\u5c40\u8303\u56f4\u7ba1\u7406\u5458\u64cd\u4f5c")
    result = await db.execute(text("""INSERT INTO admin_announcement
        (tenant_id, category, title, link_to, published_at)
        VALUES (1, :category, :title, :link_to, :published_at)"""), body.model_dump())
    announcement_id = int(result.lastrowid)
    await db.execute(text("""INSERT INTO business_audit_log
        (actor_user_id, action, resource_type, resource_id, after_json)
        VALUES (:actor, 'announcement.create', 'admin_announcement', :id, :after_json)"""), {
        "actor": admin.account.id,
        "id": announcement_id,
        "after_json": json.dumps(body.model_dump(), ensure_ascii=False, default=str),
    })
    await db.commit()
    row = (await db.execute(text("""SELECT id, category, title, link_to, published_at, created_at
        FROM admin_announcement WHERE id=:id"""), {"id": announcement_id})).mappings().one()
    return AdminAnnouncementItem(**dict(row))

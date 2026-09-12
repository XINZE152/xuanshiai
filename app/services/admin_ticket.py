"""平台管理端工单反馈 业务逻辑。

设计要点：
- 写入走 business_audit_log 通道；
- 工单号格式 YYYYMMDD + 6 位当日序号（每天从 1 重起）；
- 列表查询支持按状态/类型/关键字过滤；
- 回复即把状态推到「已处理」并记录处理人 + 时间。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.admin_ticket import (
    AdminTicketCreate,
    AdminTicketItem,
    AdminTicketPage,
    AdminTicketReply,
    AdminTicketStatistics,
)


_BASE_SELECT = """
SELECT
    t.id, t.ticket_no, t.submitter_user_id, t.submitter_name, t.feedback_type,
    t.title, t.content, t.status, t.reply_content, t.replied_by, t.replied_at,
    t.created_at, t.updated_at,
    COALESCE(rev.display_name, '') AS replier_name
FROM admin_ticket t
LEFT JOIN matchmaker_admin_account rev ON rev.id = t.replied_by
"""


def _coerce(row: dict[str, Any]) -> AdminTicketItem:
    payload = dict(row)
    payload.setdefault("submitter_user_id", None)
    payload.setdefault("submitter_name", None)
    payload.setdefault("reply_content", None)
    payload.setdefault("replied_by", None)
    payload.setdefault("replied_at", None)
    payload.setdefault("replier_name", None)
    return AdminTicketItem.model_validate(payload)


def _generate_ticket_no(cursor) -> str:
    """生成 YYYYMMDD-NNNNNN 格式工单号，N=当日序号（不足 6 位前补 0）。"""
    today = datetime.utcnow().strftime("%Y%m%d")
    cursor.execute(
        text(
            "SELECT COUNT(*) FROM admin_ticket "
            "WHERE ticket_no LIKE :prefix"
        ),
        {"prefix": f"{today}-%"},
    )
    seq = int(cursor.scalar() or 0) + 1
    return f"{today}-{seq:06d}"


async def list_tickets(
    db: AsyncSession,
    *,
    page: int,
    page_size: int,
    status: str,
    feedback_type: str,
    keyword: str | None,
) -> AdminTicketPage:
    where: list[str] = []
    params: dict[str, Any] = {"limit": page_size, "offset": (page - 1) * page_size}
    if status and status != "all":
        where.append("t.status = :status")
        params["status"] = status
    if feedback_type and feedback_type != "all":
        where.append("t.feedback_type = :ftype")
        params["ftype"] = feedback_type
    if keyword:
        where.append("(t.title LIKE :kw OR t.ticket_no LIKE :kw OR t.submitter_name LIKE :kw)")
        params["kw"] = f"%{keyword}%"
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    count_sql = f"SELECT COUNT(*) FROM admin_ticket t {where_sql}"
    total = int(
        (
            await db.execute(
                text(count_sql),
                {k: v for k, v in params.items() if k not in ("limit", "offset")},
            )
        ).scalar()
        or 0
    )
    rows = (
        await db.execute(
            text(
                f"{_BASE_SELECT} {where_sql} "
                "ORDER BY FIELD(t.status, '待处理', '处理中', '已处理'), t.created_at DESC "
                "LIMIT :limit OFFSET :offset"
            ),
            params,
        )
    ).mappings().all()
    items = [_coerce(dict(row)) for row in rows]
    return AdminTicketPage(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
    )


async def get_ticket(db: AsyncSession, ticket_id: int) -> AdminTicketItem:
    row = (
        await db.execute(text(f"{_BASE_SELECT} WHERE t.id = :id"), {"id": ticket_id})
    ).mappings().first()
    if not row:
        raise HTTPException(404, detail="工单不存在")
    return _coerce(dict(row))


async def create_ticket(
    db: AsyncSession,
    *,
    body: AdminTicketCreate,
    submitter_user_id: int | None,
    submitter_name: str | None,
) -> AdminTicketItem:
    # 用同步连接生成工单号（避免并发取号冲突，借助 uniq key 兜底）
    raw = await db.execute(text("SELECT 1"))
    _ = raw.scalar()
    # aiomysql 没有直接同步 cursor，复用同一 db.execute 计数即可（同连接同步）
    today = datetime.utcnow().strftime("%Y%m%d")
    cnt = int(
        (
            await db.execute(
                text("SELECT COUNT(*) FROM admin_ticket WHERE ticket_no LIKE :prefix"),
                {"prefix": f"{today}-%"},
            )
        ).scalar()
        or 0
    )
    ticket_no = f"{today}-{cnt + 1:06d}"

    try:
        result = await db.execute(
            text(
                """INSERT INTO admin_ticket
                    (ticket_no, submitter_user_id, submitter_name, feedback_type,
                     title, content, status)
                    VALUES (:ticket_no, :submitter_user_id, :submitter_name, :feedback_type,
                            :title, :content, '待处理')"""
            ),
            {
                "ticket_no": ticket_no,
                "submitter_user_id": submitter_user_id,
                "submitter_name": submitter_name,
                "feedback_type": body.feedback_type,
                "title": body.title,
                "content": body.content,
            },
        )
        new_id = int(result.lastrowid or 0)
        await db.commit()
    except Exception as exc:  # 并发撞号时重试一次
        await db.rollback()
        if "uk_admin_ticket_no" in str(exc):
            cnt2 = int(
                (
                    await db.execute(
                        text(
                            "SELECT COUNT(*) FROM admin_ticket WHERE ticket_no LIKE :prefix"
                        ),
                        {"prefix": f"{today}-%"},
                    )
                ).scalar()
                or 0
            )
            ticket_no = f"{today}-{cnt2 + 1:06d}"
            result = await db.execute(
                text(
                    """INSERT INTO admin_ticket
                        (ticket_no, submitter_user_id, submitter_name, feedback_type,
                         title, content, status)
                        VALUES (:ticket_no, :submitter_user_id, :submitter_name, :feedback_type,
                                :title, :content, '待处理')"""
                ),
                {
                    "ticket_no": ticket_no,
                    "submitter_user_id": submitter_user_id,
                    "submitter_name": submitter_name,
                    "feedback_type": body.feedback_type,
                    "title": body.title,
                    "content": body.content,
                },
            )
            new_id = int(result.lastrowid or 0)
            await db.commit()
        else:
            raise

    if submitter_user_id is not None:
        await db.execute(
            text(
                """INSERT INTO business_audit_log
                    (actor_user_id, action, resource_type, resource_id, after_json)
                    VALUES (:actor, 'ticket.create', 'admin_ticket', :id, :after_json)"""
            ),
            {
                "actor": submitter_user_id,
                "id": new_id,
                "after_json": json.dumps(
                    {"feedback_type": body.feedback_type, "title": body.title},
                    ensure_ascii=False,
                ),
            },
        )
        await db.commit()

    return await get_ticket(db, new_id)


async def reply_ticket(
    db: AsyncSession,
    *,
    ticket_id: int,
    admin_id: int,
    body: AdminTicketReply,
) -> AdminTicketItem:
    row = (
        await db.execute(text("SELECT id, status FROM admin_ticket WHERE id = :id"), {"id": ticket_id})
    ).mappings().first()
    if not row:
        raise HTTPException(404, detail="工单不存在")

    await db.execute(
        text(
            """UPDATE admin_ticket
                  SET reply_content = :reply,
                      replied_by = :admin,
                      replied_at = UTC_TIMESTAMP(),
                      status = '已处理',
                      updated_at = UTC_TIMESTAMP()
                WHERE id = :id"""
        ),
        {"reply": body.reply_content, "admin": admin_id, "id": ticket_id},
    )
    await db.execute(
        text(
            """INSERT INTO business_audit_log
                (actor_user_id, action, resource_type, resource_id, after_json)
                VALUES (:actor, 'ticket.reply', 'admin_ticket', :id, :after_json)"""
        ),
        {
            "actor": admin_id,
            "id": ticket_id,
            "after_json": json.dumps({"status": "已处理"}, ensure_ascii=False),
        },
    )
    await db.commit()
    return await get_ticket(db, ticket_id)


async def update_ticket_status(
    db: AsyncSession,
    *,
    ticket_id: int,
    admin_id: int,
    new_status: str,
) -> AdminTicketItem:
    row = (
        await db.execute(text("SELECT id FROM admin_ticket WHERE id = :id"), {"id": ticket_id})
    ).mappings().first()
    if not row:
        raise HTTPException(404, detail="工单不存在")
    await db.execute(
        text("UPDATE admin_ticket SET status = :s, updated_at = UTC_TIMESTAMP() WHERE id = :id"),
        {"s": new_status, "id": ticket_id},
    )
    await db.execute(
        text(
            """INSERT INTO business_audit_log
                (actor_user_id, action, resource_type, resource_id, after_json)
                VALUES (:actor, 'ticket.status', 'admin_ticket', :id, :after_json)"""
        ),
        {
            "actor": admin_id,
            "id": ticket_id,
            "after_json": json.dumps({"status": new_status}, ensure_ascii=False),
        },
    )
    await db.commit()
    return await get_ticket(db, ticket_id)


async def ticket_statistics(db: AsyncSession) -> AdminTicketStatistics:
    rows = (
        await db.execute(
            text("SELECT status, COUNT(*) AS cnt FROM admin_ticket GROUP BY status")
        )
    ).mappings().all()
    bucket = {row["status"]: int(row["cnt"]) for row in rows}
    return AdminTicketStatistics(
        pending=bucket.get("待处理", 0),
        processing=bucket.get("处理中", 0),
        done=bucket.get("已处理", 0),
    )

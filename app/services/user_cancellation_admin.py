"""账号注销申请 后台业务逻辑。

设计要点：
- 「取消注销」是软动作（status='cancelled'），不修改 users 表；
- 「批准注销」是硬动作，写入 users.status=3 + deletion_* + 清空手机号尾 4 位之外的敏感字段，
  同时保留一条 approved 记录以便审计追溯。
- 所有写入走 business_audit_log 通道。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.user_cancellation_admin import (
    UserCancellationItem,
    UserCancellationPage,
    UserCancellationStatistics,
)


_BASE_SELECT = """
SELECT
    uc.id, uc.user_id, uc.requested_ip, uc.reason, uc.status,
    uc.has_member_profile, uc.has_promoter_link, uc.has_partner_link, uc.has_matchmaker_link,
    uc.reviewed_by, uc.reviewed_at, uc.review_note, uc.created_at, uc.updated_at,
    u.nickname AS user_nickname, u.phone AS user_phone,
    COALESCE(rev.display_name, '') AS reviewer_name
FROM user_cancellation uc
LEFT JOIN users u ON u.id = uc.user_id
LEFT JOIN matchmaker_admin_account rev ON rev.id = uc.reviewed_by
"""


def _coerce(row: dict[str, Any]) -> UserCancellationItem:
    payload = dict(row)
    payload.setdefault("nickname", None)
    payload.setdefault("phone", None)
    payload.setdefault("requested_ip", None)
    payload.setdefault("reason", None)
    payload.setdefault("reviewed_by", None)
    payload.setdefault("reviewed_at", None)
    payload.setdefault("review_note", None)
    payload.setdefault("reviewer_name", None)
    for key in (
        "has_member_profile",
        "has_promoter_link",
        "has_partner_link",
        "has_matchmaker_link",
    ):
        payload[key] = bool(payload.get(key))
    return UserCancellationItem.model_validate(payload)


async def list_cancellations(
    db: AsyncSession,
    *,
    page: int,
    page_size: int,
    status: str,
    keyword: str | None,
) -> UserCancellationPage:
    where: list[str] = []
    params: dict[str, Any] = {"limit": page_size, "offset": (page - 1) * page_size}
    if status and status != "all":
        where.append("uc.status = :status")
        params["status"] = status
    if keyword:
        where.append("(u.nickname LIKE :kw OR u.phone LIKE :kw)")
        params["kw"] = f"%{keyword}%"
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    count_sql = f"SELECT COUNT(*) FROM user_cancellation uc LEFT JOIN users u ON u.id = uc.user_id {where_sql}"
    total = int(
        (await db.execute(text(count_sql), {k: v for k, v in params.items() if k not in ("limit", "offset")})).scalar()
        or 0
    )
    rows = (
        await db.execute(
            text(
                f"{_BASE_SELECT} {where_sql} "
                "ORDER BY FIELD(uc.status, 'pending', 'approved', 'cancelled'), uc.created_at DESC "
                "LIMIT :limit OFFSET :offset"
            ),
            params,
        )
    ).mappings().all()
    items = [_coerce(dict(row)) for row in rows]
    return UserCancellationPage(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
    )


async def get_cancellation(db: AsyncSession, cancellation_id: int) -> UserCancellationItem:
    row = (
        await db.execute(text(f"{_BASE_SELECT} WHERE uc.id = :id"), {"id": cancellation_id})
    ).mappings().first()
    if not row:
        raise HTTPException(404, detail="注销申请不存在")
    return _coerce(dict(row))


async def review_cancellation(
    db: AsyncSession,
    *,
    cancellation_id: int,
    admin_id: int,
    approve: bool,
    note: str | None,
) -> UserCancellationItem:
    """处理注销申请：approve=True 真正注销；approve=False 取消注销。

    批准时要把 users.status 设为 3（注销）、写入 deletion_* 三个时间戳；
    取消时清空 users.deletion_* 字段，恢复正常状态。
    """
    row = (
        await db.execute(
            text("SELECT id, user_id, status FROM user_cancellation WHERE id = :id FOR UPDATE"),
            {"id": cancellation_id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(404, detail="注销申请不存在")
    if row["status"] != "pending":
        raise HTTPException(409, detail=f"该申请已处理（status={row['status']}），不可重复操作")
    user_id = int(row["user_id"])

    if approve:
        # 真正注销
        await db.execute(
            text(
                """UPDATE users
                      SET status = 3,
                          deletion_requested_at = COALESCE(deletion_requested_at, UTC_TIMESTAMP()),
                          deletion_scheduled_at = UTC_TIMESTAMP(),
                          deletion_cancelled_at = NULL,
                          updated_at = UTC_TIMESTAMP()
                    WHERE id = :uid"""
            ),
            {"uid": user_id},
        )
        new_status = "approved"
        action = "cancellation.approve"
    else:
        # 取消注销
        await db.execute(
            text(
                """UPDATE users
                      SET status = 1,
                          deletion_cancelled_at = UTC_TIMESTAMP(),
                          updated_at = UTC_TIMESTAMP()
                    WHERE id = :uid"""
            ),
            {"uid": user_id},
        )
        new_status = "cancelled"
        action = "cancellation.reject"

    await db.execute(
        text(
            """UPDATE user_cancellation
                  SET status = :new_status,
                      reviewed_by = :admin,
                      reviewed_at = UTC_TIMESTAMP(),
                      review_note = :note
                WHERE id = :id"""
        ),
        {"new_status": new_status, "admin": admin_id, "note": note, "id": cancellation_id},
    )

    await db.execute(
        text(
            """INSERT INTO business_audit_log
                (actor_user_id, action, resource_type, resource_id, after_json)
                VALUES (:actor, :action, 'user_cancellation', :id, :after_json)"""
        ),
        {
            "actor": admin_id,
            "action": action,
            "id": cancellation_id,
            "after_json": json.dumps(
                {"approve": approve, "note": note, "user_id": user_id},
                ensure_ascii=False,
            ),
        },
    )
    await db.commit()
    return await get_cancellation(db, cancellation_id)


async def cancellation_statistics(db: AsyncSession) -> UserCancellationStatistics:
    """首页仪表盘用统计：pending 总数、approved_today / cancelled_today。"""
    total = int(
        (await db.execute(text("SELECT COUNT(*) FROM user_cancellation WHERE status = 'pending'"))).scalar() or 0
    )
    approved_today = int(
        (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM user_cancellation "
                    "WHERE status = 'approved' "
                    "AND reviewed_at >= DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%d 00:00:00')"
                )
            )
        ).scalar()
        or 0
    )
    cancelled_today = int(
        (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM user_cancellation "
                    "WHERE status = 'cancelled' "
                    "AND reviewed_at >= DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%d 00:00:00')"
                )
            )
        ).scalar()
        or 0
    )
    return UserCancellationStatistics(
        pending=total,
        approved_today=approved_today,
        cancelled_today=cancelled_today,
    )

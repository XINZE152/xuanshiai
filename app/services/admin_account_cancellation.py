"""后台账号注销申请 业务逻辑。

业务流（与前端「平台账号 → 账号管理 / 注销申请」两个页面对应）：
1. 「删除账号」（DELETE /admin/matchmaker/accounts/{id}）不直接删数据，而是生成一条
   pending 注销申请，并按申请页须知「待处理中的账号将被暂停登录」把账号置为停用；
2. 「取消注销」恢复提交前的账号状态（previous_status 快照）；
3. 「确定注销」账号保持停用，并按弹窗文案清除关联绑定与权限、吊销全部会话。
所有写入走 business_audit_log 通道。
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.admin_account_cancellation import (
    AdminAccountCancellationItem,
    AdminAccountCancellationPage,
)

_BASE_SELECT = """
SELECT
    ac.id, ac.account_id, ac.username, ac.display_name, ac.linked_user_id,
    ac.requested_ip, ac.reason, ac.status, ac.previous_status,
    ac.has_member_profile, ac.has_promoter_link, ac.has_partner_link, ac.has_matchmaker_link,
    ac.reviewed_by, ac.reviewed_at, ac.review_note, ac.created_at, ac.updated_at,
    COALESCE(rev.display_name, '') AS reviewer_name
FROM admin_account_cancellation ac
LEFT JOIN matchmaker_admin_account rev ON rev.id = ac.reviewed_by
"""

_REVOKE_SESSIONS_SQL = (
    "UPDATE matchmaker_admin_session SET status = 2, revoked_at = UTC_TIMESTAMP() "
    "WHERE account_id = :account_id AND status = 1"
)


def _coerce(row: dict[str, Any]) -> AdminAccountCancellationItem:
    payload = dict(row)
    for key in (
        "has_member_profile",
        "has_promoter_link",
        "has_partner_link",
        "has_matchmaker_link",
    ):
        payload[key] = bool(payload.get(key))
    return AdminAccountCancellationItem.model_validate(payload)


async def _get(db: AsyncSession, cancellation_id: int) -> AdminAccountCancellationItem:
    row = (
        await db.execute(text(f"{_BASE_SELECT} WHERE ac.id = :id"), {"id": cancellation_id})
    ).mappings().first()
    if not row:
        raise HTTPException(404, detail="注销申请不存在")
    return _coerce(dict(row))


async def _association_flags(db: AsyncSession, linked_user_id: int | None) -> dict[str, bool]:
    """提交时对四个关联项做快照（口径与 user_candidates 服务一致）。"""
    if not linked_user_id:
        return {
            "has_member_profile": False,
            "has_promoter_link": False,
            "has_partner_link": False,
            "has_matchmaker_link": False,
        }
    row = (
        await db.execute(
            text(
                """SELECT
                    EXISTS(SELECT 1 FROM users u
                        WHERE u.id = :uid AND u.deleted_at IS NULL) AS has_member_profile,
                    EXISTS(SELECT 1 FROM user_matchmaker_apply ma
                        WHERE ma.user_id = :uid AND ma.application_type = 'promoter'
                          AND ma.status IN (1, 2)) AS has_promoter_link,
                    EXISTS(SELECT 1 FROM partner_team t
                        WHERE t.owner_user_id = :uid AND t.status = 1) AS has_partner_link,
                    EXISTS(SELECT 1 FROM user_matchmaker_apply ma
                        WHERE ma.user_id = :uid AND ma.application_type = 'service_matchmaker'
                          AND ma.status IN (1, 2)) AS has_matchmaker_link"""
            ),
            {"uid": linked_user_id},
        )
    ).mappings().first()
    data = dict(row or {})
    return {key: bool(value) for key, value in data.items()}


async def _audit(
    db: AsyncSession,
    *,
    actor_id: int,
    action: str,
    resource_id: int,
    reason: str | None,
) -> None:
    await db.execute(
        text(
            """INSERT INTO business_audit_log
                (actor_user_id, action, resource_type, resource_id, reason)
                VALUES (:actor, :action, 'admin_account_cancellation', :resource_id, :reason)"""
        ),
        {"actor": actor_id, "action": action, "resource_id": resource_id, "reason": reason},
    )


async def submit_cancellation(
    db: AsyncSession,
    *,
    account_id: int,
    actor_id: int,
    reason: str | None = None,
    requested_ip: str | None = None,
) -> AdminAccountCancellationItem:
    """「删除账号」入口：生成 pending 注销申请并暂停该账号登录。"""
    account = (
        await db.execute(
            text(
                "SELECT id, username, display_name, matchmaker_user_id, status "
                "FROM matchmaker_admin_account WHERE id = :id FOR UPDATE"
            ),
            {"id": account_id},
        )
    ).mappings().first()
    if not account:
        raise HTTPException(404, detail="后台账号不存在")
    if account_id == actor_id:
        raise HTTPException(403, detail="不能注销当前登录的账号，请使用其他管理员账号操作")

    pending = (
        await db.execute(
            text(
                "SELECT id FROM admin_account_cancellation "
                "WHERE account_id = :account_id AND status = 'pending'"
            ),
            {"account_id": account_id},
        )
    ).scalar()
    if pending:
        raise HTTPException(409, detail="该账号已有待处理的注销申请")

    flags = await _association_flags(db, account["matchmaker_user_id"])
    result = await db.execute(
        text(
            """INSERT INTO admin_account_cancellation
                (account_id, username, display_name, linked_user_id, requested_ip, reason,
                 status, previous_status,
                 has_member_profile, has_promoter_link, has_partner_link, has_matchmaker_link)
                VALUES (:account_id, :username, :display_name, :linked_user_id, :requested_ip, :reason,
                        'pending', :previous_status,
                        :has_member_profile, :has_promoter_link, :has_partner_link, :has_matchmaker_link)"""
        ),
        {
            "account_id": account_id,
            "username": account["username"],
            "display_name": account["display_name"],
            "linked_user_id": account["matchmaker_user_id"],
            "requested_ip": requested_ip,
            "reason": (reason or "").strip() or None,
            "previous_status": int(account["status"]),
            **flags,
        },
    )
    cancellation_id = int(result.lastrowid)

    # 待处理中的账号暂停登录（申请页须知文案），并吊销已有会话
    await db.execute(
        text("UPDATE matchmaker_admin_account SET status = 2, updated_at = UTC_TIMESTAMP() WHERE id = :id"),
        {"id": account_id},
    )
    await db.execute(text(_REVOKE_SESSIONS_SQL), {"account_id": account_id})

    await _audit(
        db,
        actor_id=actor_id,
        action="account_cancellation.submit",
        resource_id=cancellation_id,
        reason=(reason or "").strip() or None,
    )
    await db.commit()
    return await _get(db, cancellation_id)


async def list_cancellations(
    db: AsyncSession,
    *,
    page: int,
    page_size: int,
    status: str,
    keyword: str | None,
) -> AdminAccountCancellationPage:
    where: list[str] = []
    params: dict[str, Any] = {"limit": page_size, "offset": (page - 1) * page_size}
    if status and status != "all":
        where.append("ac.status = :status")
        params["status"] = status
    if keyword:
        where.append("(ac.username LIKE :kw OR ac.display_name LIKE :kw)")
        params["kw"] = f"%{keyword}%"
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    count_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}
    total = int(
        (
            await db.execute(
                text(f"SELECT COUNT(*) FROM admin_account_cancellation ac {where_sql}"), count_params
            )
        ).scalar()
        or 0
    )
    rows = (
        await db.execute(
            text(
                f"{_BASE_SELECT} {where_sql} "
                "ORDER BY FIELD(ac.status, 'pending', 'approved', 'cancelled'), ac.created_at DESC "
                "LIMIT :limit OFFSET :offset"
            ),
            params,
        )
    ).mappings().all()
    return AdminAccountCancellationPage(
        items=[_coerce(dict(row)) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
    )


async def review_cancellation(
    db: AsyncSession,
    *,
    cancellation_id: int,
    admin_id: int,
    approve: bool,
    note: str | None,
) -> AdminAccountCancellationItem:
    """审核注销申请：approve=True 确定注销；approve=False 取消注销并恢复登录。"""
    application = (
        await db.execute(
            text(
                "SELECT id, account_id, status, previous_status "
                "FROM admin_account_cancellation WHERE id = :id FOR UPDATE"
            ),
            {"id": cancellation_id},
        )
    ).mappings().first()
    if not application:
        raise HTTPException(404, detail="注销申请不存在")
    if application["status"] != "pending":
        raise HTTPException(409, detail=f"该申请已处理（status={application['status']}），不可重复操作")
    account_id = int(application["account_id"])

    if approve:
        # 确定注销：账号保持停用，按弹窗文案清除绑定关系与权限，并吊销全部会话
        await db.execute(
            text(
                "UPDATE matchmaker_admin_account SET status = 2, matchmaker_user_id = NULL, "
                "updated_at = UTC_TIMESTAMP() WHERE id = :id"
            ),
            {"id": account_id},
        )
        await db.execute(
            text("DELETE FROM matchmaker_admin_permission WHERE account_id = :id"), {"id": account_id}
        )
        await db.execute(text(_REVOKE_SESSIONS_SQL), {"account_id": account_id})
        new_status = "approved"
        action = "account_cancellation.approve"
    else:
        # 取消注销：恢复提交前的账号状态（账号可能在此期间被人工调整，按快照恢复）
        await db.execute(
            text(
                "UPDATE matchmaker_admin_account SET status = :previous_status, "
                "updated_at = UTC_TIMESTAMP() WHERE id = :id"
            ),
            {"id": account_id, "previous_status": int(application["previous_status"])},
        )
        new_status = "cancelled"
        action = "account_cancellation.reject"

    await db.execute(
        text(
            """UPDATE admin_account_cancellation
                  SET status = :new_status,
                      reviewed_by = :admin,
                      reviewed_at = UTC_TIMESTAMP(),
                      review_note = :note
                WHERE id = :id"""
        ),
        {"new_status": new_status, "admin": admin_id, "note": note, "id": cancellation_id},
    )

    await _audit(
        db,
        actor_id=admin_id,
        action=action,
        resource_id=cancellation_id,
        reason=note,
    )
    await db.commit()
    return await _get(db, cancellation_id)

"""First-phase matchmaker listings and free service requests."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser
from app.core.config import settings
from app.schemas.matchmaker import (
    MatchmakerAdminServiceRequestUpdate,
    MatchmakerCard,
    MatchmakerPage,
    MatchmakerServiceRequestCreate,
    MatchmakerServiceRequestPage,
    MatchmakerServiceRequestResponse,
    MatchmakerServiceRequestUpdate,
)


def _datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _json_images(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    import json

    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _card(row: Any) -> MatchmakerCard:
    return MatchmakerCard(
        user_id=int(row["user_id"]),
        nickname=row["nickname"],
        avatar=row["avatar"],
        application_type="service_matchmaker",
        intro=row["intro"] or "",
        certification_tags=["平台认证"],
        success_count=int(row["success_count"] or 0),
        rating_score=float(row["rating_score"] or 0),
        rating_count=int(row["rating_count"] or 0),
        is_available=bool(row["role_status"] == 1),
    )


async def list_matchmakers(
    db: AsyncSession, page: int, page_size: int, ranking: bool = False
) -> MatchmakerPage:
    order = "success_count DESC, rating_score DESC, app.reviewed_at DESC, app.id DESC" if ranking else "app.reviewed_at DESC, app.id DESC"
    params = {"limit": page_size, "offset": (page - 1) * page_size}
    query = text(f"""SELECT app.user_id, u.nickname, u.avatar, app.intro, app.cert_images,
        COALESCE(service_stats.success_count, 0) AS success_count,
        COALESCE(rating_stats.rating_score, 0) AS rating_score,
        COALESCE(rating_stats.rating_count, 0) AS rating_count,
        role.status AS role_status
        FROM user_matchmaker_apply app
        JOIN users u ON u.id = app.user_id AND u.status = 1
        JOIN user_role role ON role.user_id = app.user_id
          AND role.role_code = 'service_matchmaker' AND role.status = 1
        LEFT JOIN (SELECT matchmaker_id, COUNT(*) AS success_count
          FROM matchmaker_service WHERE status = 2 GROUP BY matchmaker_id) service_stats
          ON service_stats.matchmaker_id = app.user_id
        LEFT JOIN (SELECT matchmaker_id, AVG(score) AS rating_score, COUNT(*) AS rating_count
          FROM matchmaker_rating GROUP BY matchmaker_id) rating_stats
          ON rating_stats.matchmaker_id = app.user_id
        WHERE app.application_type = 'service_matchmaker' AND app.status = 1
        ORDER BY {order}
        LIMIT :limit OFFSET :offset""")
    result = await db.execute(query, params)
    count = await db.execute(text("""SELECT COUNT(*) FROM user_matchmaker_apply app
        JOIN users u ON u.id = app.user_id AND u.status = 1
        JOIN user_role role ON role.user_id = app.user_id
          AND role.role_code = 'service_matchmaker' AND role.status = 1
        WHERE app.application_type = 'service_matchmaker' AND app.status = 1"""))
    total = int(count.scalar() or 0)
    items = [_card(row) for row in result.mappings().all()]
    return MatchmakerPage(items=items, page=page, page_size=page_size, total=total, has_more=page * page_size < total)


async def get_matchmaker(db: AsyncSession, matchmaker_id: int) -> MatchmakerCard:
    result = await db.execute(text("""SELECT app.user_id, u.nickname, u.avatar, app.intro, app.cert_images,
        COALESCE(service_stats.success_count, 0) AS success_count,
        COALESCE(rating_stats.rating_score, 0) AS rating_score,
        COALESCE(rating_stats.rating_count, 0) AS rating_count,
        role.status AS role_status
        FROM user_matchmaker_apply app
        JOIN users u ON u.id = app.user_id AND u.status = 1
        JOIN user_role role ON role.user_id = app.user_id
          AND role.role_code = 'service_matchmaker' AND role.status = 1
        LEFT JOIN (SELECT matchmaker_id, COUNT(*) AS success_count
          FROM matchmaker_service WHERE status = 2 GROUP BY matchmaker_id) service_stats
          ON service_stats.matchmaker_id = app.user_id
        LEFT JOIN (SELECT matchmaker_id, AVG(score) AS rating_score, COUNT(*) AS rating_count
          FROM matchmaker_rating GROUP BY matchmaker_id) rating_stats
          ON rating_stats.matchmaker_id = app.user_id
        WHERE app.user_id = :matchmaker_id AND app.application_type = 'service_matchmaker'
          AND app.status = 1"""), {"matchmaker_id": matchmaker_id})
    row = result.mappings().first()
    if not row:
        raise HTTPException(404, detail="服务红娘不存在或暂不可用")
    return _card(row)


def _service_response(row: Any) -> MatchmakerServiceRequestResponse:
    return MatchmakerServiceRequestResponse(
        id=int(row["id"]),
        user_id=int(row["user_id"]),
        matchmaker_id=int(row["matchmaker_id"]) if row["matchmaker_id"] is not None else None,
        service_type=int(row["service_type"]),
        status=int(row["status"]),
        requirement=row["requirement"] or "",
        feedback=row["feedback"],
        created_at=_datetime(row["created_at"]),
        updated_at=_datetime(row["updated_at"]),
        start_at=_datetime(row["start_at"]) if row["start_at"] else None,
        end_at=_datetime(row["end_at"]) if row["end_at"] else None,
    )


SERVICE_SELECT = """SELECT id, user_id, matchmaker_id, service_type, status, requirement,
    feedback, created_at, updated_at, start_at, end_at FROM matchmaker_service"""


async def _notify(db: AsyncSession, user_id: int, notification_type: str, title: str, content: str, related_id: int) -> None:
    await db.execute(text("""INSERT INTO user_notification
        (user_id, notification_type, title, content, related_id, is_read)
        VALUES (:user_id, :notification_type, :title, :content, :related_id, 0)"""), {
        "user_id": user_id, "notification_type": notification_type,
        "title": title, "content": content, "related_id": related_id,
    })


async def _consume_quota(db: AsyncSession, user_id: int, service_id: int) -> None:
    """在当前事务中扣减一次牵线服务次数，并写入幂等流水。"""
    await db.execute(text("""INSERT INTO matchmaker_service_quota (user_id, available_count)
        VALUES (:user_id, :initial_count)
        ON DUPLICATE KEY UPDATE user_id = user_id"""), {
        "user_id": user_id, "initial_count": settings.matchmaker_service_default_quota,
    })
    quota = await db.execute(text("""SELECT available_count FROM matchmaker_service_quota
        WHERE user_id = :user_id FOR UPDATE"""), {"user_id": user_id})
    available = int(quota.scalar() or 0)
    if available <= 0:
        raise HTTPException(409, detail="牵线服务次数不足")
    await db.execute(text("""INSERT INTO matchmaker_quota_entry
        (user_id, service_id, entry_type, quantity, idempotency_key)
        VALUES (:user_id, :service_id, 'consume', 1, :key)"""), {
        "user_id": user_id, "service_id": service_id, "key": f"quota:consume:{service_id}",
    })
    await db.execute(text("""UPDATE matchmaker_service_quota SET available_count = available_count - 1,
        used_count = used_count + 1 WHERE user_id = :user_id"""), {"user_id": user_id})


async def _refund_quota(db: AsyncSession, user_id: int, service_id: int) -> None:
    """为失败/取消的牵线申请最多返还一次服务次数。"""
    result = await db.execute(text("""INSERT IGNORE INTO matchmaker_quota_entry
        (user_id, service_id, entry_type, quantity, idempotency_key)
        VALUES (:user_id, :service_id, 'refund', 1, :key)"""), {
        "user_id": user_id, "service_id": service_id, "key": f"quota:refund:{service_id}",
    })
    if result.rowcount:
        await db.execute(text("""UPDATE matchmaker_service_quota SET available_count = available_count + 1,
            refunded_count = refunded_count + 1 WHERE user_id = :user_id"""), {"user_id": user_id})


async def create_service_request(
    db: AsyncSession, current: CurrentUser, request: MatchmakerServiceRequestCreate
) -> MatchmakerServiceRequestResponse:
    if current.realname_status != 2:
        raise HTTPException(403, detail="提交牵线申请前必须完成实名认证")
    if request.matchmaker_id == current.id:
        raise HTTPException(422, detail="不能向自己提交牵线申请")
    target = await db.execute(text("""SELECT app.user_id FROM user_matchmaker_apply app
        JOIN users u ON u.id = app.user_id AND u.status = 1
        JOIN user_role role ON role.user_id = app.user_id
          AND role.role_code = 'service_matchmaker' AND role.status = 1
        WHERE app.user_id = :matchmaker_id AND app.application_type = 'service_matchmaker'
          AND app.status = 1"""), {"matchmaker_id": request.matchmaker_id})
    if not target.scalar():
        raise HTTPException(404, detail="服务红娘不存在或暂不可用")
    existing = await db.execute(text("""SELECT id FROM matchmaker_service
        WHERE user_id = :user_id AND matchmaker_id = :matchmaker_id AND status IN (0, 1)
        LIMIT 1 FOR UPDATE"""), {"user_id": current.id, "matchmaker_id": request.matchmaker_id})
    if existing.scalar():
        raise HTTPException(409, detail="已有处理中牵线申请，不能重复提交")
    result = await db.execute(text("""INSERT INTO matchmaker_service
        (user_id, matchmaker_id, service_type, status, requirement)
        VALUES (:user_id, :matchmaker_id, 2, 0, :requirement)"""), {
        "user_id": current.id, "matchmaker_id": request.matchmaker_id,
        "requirement": request.requirement,
    })
    service_id = int(result.lastrowid)
    await _consume_quota(db, current.id, service_id)
    await _notify(db, request.matchmaker_id, "matchmaker_service_request", "收到新的牵线申请", "有用户向你提交了牵线申请", service_id)
    await db.commit()
    created = await db.execute(text(f"{SERVICE_SELECT} WHERE id = :id"), {"id": service_id})
    return _service_response(created.mappings().one())


async def list_service_requests(
    db: AsyncSession, current: CurrentUser, page: int, page_size: int, assigned: bool = False
) -> MatchmakerServiceRequestPage:
    if assigned:
        role = await db.execute(text("""SELECT 1 FROM user_role WHERE user_id = :user_id
            AND role_code = 'service_matchmaker' AND status = 1 LIMIT 1"""), {"user_id": current.id})
        if not role.scalar():
            raise HTTPException(403, detail="当前用户不是有效服务红娘")
        where, params = "matchmaker_id = :user_id", {"user_id": current.id}
    else:
        where, params = "user_id = :user_id", {"user_id": current.id}
    result = await db.execute(text(f"{SERVICE_SELECT} WHERE {where} ORDER BY created_at DESC, id DESC LIMIT :limit OFFSET :offset"), {**params, "limit": page_size, "offset": (page - 1) * page_size})
    count = await db.execute(text(f"SELECT COUNT(*) FROM matchmaker_service WHERE {where}"), params)
    total = int(count.scalar() or 0)
    items = [_service_response(row) for row in result.mappings().all()]
    return MatchmakerServiceRequestPage(items=items, page=page, page_size=page_size, total=total, has_more=page * page_size < total)


async def update_service_request(
    db: AsyncSession, current: CurrentUser, service_id: int, request: MatchmakerServiceRequestUpdate
) -> MatchmakerServiceRequestResponse:
    row_result = await db.execute(text(f"{SERVICE_SELECT} WHERE id = :id FOR UPDATE"), {"id": service_id})
    row = row_result.mappings().first()
    if not row:
        raise HTTPException(404, detail="牵线申请不存在")
    if row["matchmaker_id"] != current.id:
        raise HTTPException(403, detail="只有被分配的服务红娘可以处理申请")
    if row["status"] not in (0, 1):
        raise HTTPException(409, detail="当前牵线申请状态不能继续处理")
    start_at = "start_at = COALESCE(start_at, UTC_TIMESTAMP())," if request.status == 1 else ""
    end_at = "end_at = UTC_TIMESTAMP()," if request.status in (2, 3) else ""
    await db.execute(text(f"""UPDATE matchmaker_service SET status = :status,
        feedback = :feedback, {start_at} {end_at} updated_at = UTC_TIMESTAMP()
        WHERE id = :id"""), {"status": request.status, "feedback": request.feedback, "id": service_id})
    if request.status == 3:
        await _refund_quota(db, int(row["user_id"]), service_id)
    await _notify(db, row["user_id"], "matchmaker_service_updated", "牵线服务状态更新", "你的牵线申请状态已更新", service_id)
    await db.commit()
    updated = await db.execute(text(f"{SERVICE_SELECT} WHERE id = :id"), {"id": service_id})
    return _service_response(updated.mappings().one())


async def admin_list_service_requests(db: AsyncSession, page: int, page_size: int, status: int | None) -> MatchmakerServiceRequestPage:
    where = "WHERE 1=1"
    params: dict[str, Any] = {"limit": page_size, "offset": (page - 1) * page_size}
    if status is not None:
        where += " AND status = :status"
        params["status"] = status
    result = await db.execute(text(f"{SERVICE_SELECT} {where} ORDER BY created_at DESC, id DESC LIMIT :limit OFFSET :offset"), params)
    count = await db.execute(text(f"SELECT COUNT(*) FROM matchmaker_service {where}"), {key: value for key, value in params.items() if key == "status"})
    total = int(count.scalar() or 0)
    items = [_service_response(row) for row in result.mappings().all()]
    return MatchmakerServiceRequestPage(items=items, page=page, page_size=page_size, total=total, has_more=page * page_size < total)


async def admin_update_service_request(db: AsyncSession, admin_id: int, service_id: int, request: MatchmakerAdminServiceRequestUpdate) -> MatchmakerServiceRequestResponse:
    row_result = await db.execute(text(f"{SERVICE_SELECT} WHERE id = :id FOR UPDATE"), {"id": service_id})
    row = row_result.mappings().first()
    if not row:
        raise HTTPException(404, detail="牵线申请不存在")
    if request.matchmaker_id is not None:
        target = await db.execute(text("""SELECT 1 FROM user_role WHERE user_id = :user_id
            AND role_code = 'service_matchmaker' AND status = 1 LIMIT 1"""), {"user_id": request.matchmaker_id})
        if not target.scalar():
            raise HTTPException(422, detail="只能分配给有效服务红娘")
    updates: list[str] = []
    params: dict[str, Any] = {"id": service_id}
    if request.matchmaker_id is not None:
        updates.append("matchmaker_id = :matchmaker_id")
        params["matchmaker_id"] = request.matchmaker_id
    if request.status is not None:
        updates.append("status = :status")
        params["status"] = request.status
        if request.status == 1:
            updates.append("start_at = COALESCE(start_at, UTC_TIMESTAMP())")
        if request.status in (2, 3):
            updates.append("end_at = UTC_TIMESTAMP()")
    if request.feedback is not None:
        updates.append("feedback = :feedback")
        params["feedback"] = request.feedback
    updates.extend(["updated_at = UTC_TIMESTAMP()"])
    await db.execute(text(f"UPDATE matchmaker_service SET {', '.join(updates)} WHERE id = :id"), params)
    if request.status == 3:
        await _refund_quota(db, int(row["user_id"]), service_id)
    await _notify(db, request.matchmaker_id or row["matchmaker_id"] or row["user_id"], "matchmaker_service_admin_updated", "牵线申请已更新", "管理员更新了牵线申请", service_id)
    await db.commit()
    updated = await db.execute(text(f"{SERVICE_SELECT} WHERE id = :id"), {"id": service_id})
    return _service_response(updated.mappings().one())

"""约见申请、安排和私有反馈服务。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser
from app.schemas.meeting import (
    MeetingFeedbackCreate,
    MeetingDirectCreate,
    MeetingRecordResponse,
    MeetingRequestCreate,
    MatchmakerMeetingRequestCreate,
    MeetingRequestResponse,
    MeetingScheduleCreate,
    MeetingStatistics,
    MeetingStatusUpdate,
    MeetingRecordAdminPage,
    MeetingRequestAdminPage,
    MeetingRecordAdminUpdate,
    MeetingFeedbackAdminItem,
)
from app.services.social import ensure_users_can_interact
from app.services.notifications import emit_notification


def _dt(value: Any) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def _request_response(row: Any) -> MeetingRequestResponse:
    return MeetingRequestResponse(**{**dict(row), "created_at": _dt(row["created_at"]), "updated_at": _dt(row["updated_at"])})


def _record_response(row: Any) -> MeetingRecordResponse:
    return MeetingRecordResponse(**{**dict(row), "scheduled_at": _dt(row["scheduled_at"]), "created_at": _dt(row["created_at"]), "updated_at": _dt(row["updated_at"])})


async def create_meeting_request(db: AsyncSession, current: CurrentUser, request: MeetingRequestCreate) -> MeetingRequestResponse:
    if request.target_user_id == current.id:
        raise HTTPException(422, detail="不能申请与自己约见")
    target = await db.execute(text("SELECT id FROM users WHERE id = :id AND status = 1"), {"id": request.target_user_id})
    if not target.scalar():
        raise HTTPException(404, detail="被约见用户不存在或不可用")
    await ensure_users_can_interact(db, current.id, request.target_user_id)
    duplicate = await db.execute(text("""SELECT id FROM meeting_request
        WHERE user_id = :user_id AND target_user_id = :target_id
          AND status IN ('SUBMITTED', 'CONTACTED', 'ACCEPTED') LIMIT 1"""), {"user_id": current.id, "target_id": request.target_user_id})
    if duplicate.scalar():
        raise HTTPException(409, detail="已有处理中约见申请")
    quota = (await db.execute(text("SELECT id, available_count FROM matchmaker_service_quota WHERE user_id = :id FOR UPDATE"), {"id": current.id})).mappings().first()
    if not quota or int(quota["available_count"] or 0) < 1:
        raise HTTPException(409, detail="约见资源不足")
    await db.execute(text("UPDATE matchmaker_service_quota SET available_count = available_count - 1, used_count = used_count + 1 WHERE id = :id"), {"id": quota["id"]})
    result = await db.execute(text("""INSERT INTO meeting_request (user_id, target_user_id, note)
        VALUES (:user_id, :target_id, :note)"""), {"user_id": current.id, "target_id": request.target_user_id, "note": request.note})
    request_id = int(result.lastrowid)
    await emit_notification(db, recipient_user_id=request.target_user_id, actor_user_id=current.id, event_type="match_application", title="收到约见申请", content="有人向你提交了约见申请，请及时查看", target_type="meeting_request", target_id=request_id)
    await db.commit()
    row = (await db.execute(text("SELECT id, user_id, target_user_id, matchmaker_id, service_id, organization_id, status, note, created_at, updated_at FROM meeting_request WHERE id = :id"), {"id": request_id})).mappings().one()
    return _request_response(row)


async def create_matchmaker_meeting_request(
    db: AsyncSession, current: CurrentUser, request: MatchmakerMeetingRequestCreate
) -> MeetingRequestResponse:
    service_result = await db.execute(text("""SELECT id, user_id, status FROM matchmaker_service
        WHERE id = :service_id AND matchmaker_id = :matchmaker_id FOR UPDATE"""), {
        "service_id": request.service_id, "matchmaker_id": current.id,
    })
    service = service_result.mappings().first()
    if not service:
        raise HTTPException(404, detail="服务单不存在或不属于当前红娘")
    if service["status"] not in (1, 2):
        raise HTTPException(409, detail="只有服务中或服务完成的红娘服务才能发起约见")
    if request.target_user_id == service["user_id"]:
        raise HTTPException(422, detail="不能将服务用户作为约见对象")
    target = await db.execute(text("SELECT id FROM users WHERE id = :id AND status = 1"), {"id": request.target_user_id})
    if not target.scalar():
        raise HTTPException(404, detail="约见对象不存在或不可用")
    await ensure_users_can_interact(db, int(service["user_id"]), request.target_user_id)
    await ensure_users_can_interact(db, current.id, request.target_user_id)
    duplicate = await db.execute(text("""SELECT id FROM meeting_request
        WHERE user_id = :user_id AND target_user_id = :target_id
          AND status IN ('SUBMITTED', 'CONTACTED', 'ACCEPTED') LIMIT 1"""), {
        "user_id": service["user_id"], "target_id": request.target_user_id,
    })
    if duplicate.scalar():
        raise HTTPException(409, detail="已有处理中约见申请")
    quota = (await db.execute(text("SELECT id, available_count FROM matchmaker_service_quota WHERE user_id = :id FOR UPDATE"), {"id": service["user_id"]})).mappings().first()
    if not quota or int(quota["available_count"] or 0) < 1:
        raise HTTPException(409, detail="约见资源不足")
    await db.execute(text("UPDATE matchmaker_service_quota SET available_count = available_count - 1, used_count = used_count + 1 WHERE id = :id"), {"id": quota["id"]})
    result = await db.execute(text("""INSERT INTO meeting_request
        (user_id, target_user_id, matchmaker_id, service_id, note)
        VALUES (:user_id, :target_id, :matchmaker_id, :service_id, :note)"""), {
        "user_id": service["user_id"], "target_id": request.target_user_id,
        "matchmaker_id": current.id, "service_id": request.service_id, "note": request.note,
    })
    request_id = int(result.lastrowid)
    await db.commit()
    result = await db.execute(text("""SELECT id, user_id, target_user_id, matchmaker_id,
        service_id, organization_id, status, note, created_at, updated_at
        FROM meeting_request WHERE id = :id"""), {"id": request_id})
    return _request_response(result.mappings().one())


async def list_my_meeting_requests(db: AsyncSession, current: CurrentUser) -> list[MeetingRequestResponse]:
    result = await db.execute(text("""SELECT id, user_id, target_user_id, matchmaker_id,
        service_id, organization_id, status, note, created_at, updated_at FROM meeting_request
        WHERE user_id = :user_id OR target_user_id = :user_id ORDER BY created_at DESC, id DESC"""), {"user_id": current.id})
    return [_request_response(row) for row in result.mappings().all()]


async def update_meeting_request(db: AsyncSession, current: CurrentUser, request_id: int, request: MeetingStatusUpdate) -> MeetingRequestResponse:
    result = await db.execute(text("""SELECT id, user_id, target_user_id, matchmaker_id,
        service_id, organization_id, status, note, created_at, updated_at FROM meeting_request
        WHERE id = :id FOR UPDATE"""), {"id": request_id})
    row = result.mappings().first()
    if not row:
        raise HTTPException(404, detail="约见申请不存在")
    if current.id not in (row["user_id"], row["target_user_id"]):
        raise HTTPException(403, detail="无权处理该约见申请")
    if row["status"] not in ("SUBMITTED", "CONTACTED", "ACCEPTED"):
        raise HTTPException(409, detail="当前约见申请状态不能修改")
    if request.status in ("DECLINED", "CLOSED") and not request.reason:
        raise HTTPException(422, detail="拒绝或关闭约见申请必须填写原因")
    await db.execute(text("UPDATE meeting_request SET status = :status, updated_at = UTC_TIMESTAMP() WHERE id = :id"), {
        "status": request.status, "id": request_id,
    })
    if request.status in ("DECLINED", "CLOSED"):
        await db.execute(text("UPDATE matchmaker_service_quota SET available_count = available_count + 1, used_count = GREATEST(used_count - 1, 0), refunded_count = refunded_count + 1 WHERE user_id = :id"), {"id": row["user_id"]})
    if request.status == "ACCEPTED":
        await emit_notification(db, recipient_user_id=int(row["target_user_id"]), actor_user_id=int(row["user_id"]), event_type="match_application_accepted", title="约见申请已通过", content="你的约见申请已通过审核", target_type="meeting_request", target_id=request_id)
    elif request.status in ("DECLINED", "CLOSED"):
        await emit_notification(db, recipient_user_id=int(row["user_id"]), actor_user_id=int(row["target_user_id"]), event_type="match_application_rejected", title="约见申请未通过", content=request.reason or "你的约见申请未通过", target_type="meeting_request", target_id=request_id)
    await db.commit()
    result = await db.execute(text("""SELECT id, user_id, target_user_id, matchmaker_id,
        service_id, organization_id, status, note, created_at, updated_at FROM meeting_request WHERE id = :id"""), {"id": request_id})
    return _request_response(result.mappings().one())


async def admin_update_request(db: AsyncSession, request_id: int, request: MeetingStatusUpdate, actor_id: int) -> MeetingRequestResponse:
    result = await db.execute(text("""SELECT id, user_id, target_user_id, matchmaker_id,
        service_id, organization_id, status, note, created_at, updated_at
        FROM meeting_request WHERE id = :id FOR UPDATE"""), {"id": request_id})
    row = result.mappings().first()
    if not row:
        raise HTTPException(404, detail="约见申请不存在")
    if row["status"] not in ("SUBMITTED", "CONTACTED", "ACCEPTED"):
        raise HTTPException(409, detail="当前约见申请状态不能修改")
    if request.status in ("DECLINED", "CLOSED") and not request.reason:
        raise HTTPException(422, detail="拒绝或关闭约见申请必须填写原因")
    await db.execute(text("UPDATE meeting_request SET status = :status, updated_at = UTC_TIMESTAMP() WHERE id = :id"), {"status": request.status, "id": request_id})
    if request.status in ("DECLINED", "CLOSED"):
        await db.execute(text("UPDATE matchmaker_service_quota SET available_count = available_count + 1, used_count = GREATEST(used_count - 1, 0), refunded_count = refunded_count + 1 WHERE user_id = :id"), {"id": row["user_id"]})
    event_type = "match_application_accepted" if request.status == "ACCEPTED" else "match_application_rejected"
    await emit_notification(db, recipient_user_id=int(row["target_user_id"] if request.status == "ACCEPTED" else row["user_id"]), actor_user_id=actor_id, event_type=event_type, title="约见申请已通过" if request.status == "ACCEPTED" else "约见申请未通过", content="你的约见申请已通过审核" if request.status == "ACCEPTED" else (request.reason or "你的约见申请未通过"), target_type="meeting_request", target_id=request_id)
    await db.execute(text("INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id, reason) VALUES (:actor, 'meeting_request.review', 'meeting_request', :id, :reason)"), {"actor": actor_id, "id": request_id, "reason": request.reason})
    await db.commit()
    result = await db.execute(text("SELECT id, user_id, target_user_id, matchmaker_id, service_id, organization_id, status, note, created_at, updated_at FROM meeting_request WHERE id = :id"), {"id": request_id})
    return _request_response(result.mappings().one())


async def schedule_meeting(db: AsyncSession, admin: CurrentUser, request_id: int, request: MeetingScheduleCreate) -> MeetingRecordResponse:
    result = await db.execute(text("""SELECT id, user_id, target_user_id, status FROM meeting_request
        WHERE id = :id FOR UPDATE"""), {"id": request_id})
    row = result.mappings().first()
    if not row:
        raise HTTPException(404, detail="约见申请不存在")
    if row["status"] != "ACCEPTED":
        raise HTTPException(409, detail="只有双方接受的约见申请才能安排约会")
    result = await db.execute(text("""INSERT INTO meeting_record
        (request_id, organizer_id, organization_id, scheduled_at, location, member_visible, sms_remind)
        VALUES (:request_id, :organizer_id, :organization_id, :scheduled_at, :location, :member_visible, :sms_remind)"""), {
        "request_id": request_id, "organizer_id": request.organizer_id,
        "organization_id": request.organization_id, "scheduled_at": request.scheduled_at,
        "location": request.location, "member_visible": int(request.member_visible),
        "sms_remind": int(request.sms_remind),
    })
    meeting_id = int(result.lastrowid)
    await db.execute(text("UPDATE meeting_request SET status = 'ACCEPTED', updated_at = UTC_TIMESTAMP() WHERE id = :id"), {"id": request_id})
    await db.commit()
    result = await db.execute(text("""SELECT id, request_id, organizer_id, organization_id,
        scheduled_at, location, status, cancel_reason, member_visible, sms_remind, created_at, updated_at
        FROM meeting_record WHERE id = :id"""), {"id": meeting_id})
    return _record_response(result.mappings().one())


_ADMIN_RECORD_SELECT = """SELECT mr.id, mr.request_id, mr.organizer_id, mr.organization_id,
    mr.scheduled_at, mr.location, mr.status, mr.cancel_reason, mr.member_visible, mr.sms_remind,
    mr.created_at, mr.updated_at,
    rq.user_id AS from_user_id, uf.nickname AS from_nickname,
    rq.target_user_id AS to_user_id, ut.nickname AS to_nickname,
    uo.nickname AS organizer_name,
    (SELECT COUNT(*) FROM meeting_feedback f WHERE f.meeting_id = mr.id) AS feedback_count
    FROM meeting_record mr
    LEFT JOIN meeting_request rq ON rq.id = mr.request_id
    LEFT JOIN users uf ON uf.id = rq.user_id
    LEFT JOIN users ut ON ut.id = rq.target_user_id
    LEFT JOIN users uo ON uo.id = mr.organizer_id"""


async def admin_meeting_statistics(db: AsyncSession) -> MeetingStatistics:
    """约会管理顶部统计：总安排/总成功 + 本月已安排/待见面/已见面/未见面。"""
    row = (await db.execute(text("""SELECT
        COUNT(*) AS total_arranged,
        SUM(status IN ('CHECKED_IN', 'COMPLETED')) AS total_met,
        SUM(YEAR(scheduled_at) = YEAR(UTC_TIMESTAMP()) AND MONTH(scheduled_at) = MONTH(UTC_TIMESTAMP())) AS month_arranged,
        SUM(YEAR(scheduled_at) = YEAR(UTC_TIMESTAMP()) AND MONTH(scheduled_at) = MONTH(UTC_TIMESTAMP())
            AND status IN ('SCHEDULED', 'REMINDED')) AS month_waiting,
        SUM(YEAR(scheduled_at) = YEAR(UTC_TIMESTAMP()) AND MONTH(scheduled_at) = MONTH(UTC_TIMESTAMP())
            AND status IN ('CHECKED_IN', 'COMPLETED')) AS month_met,
        SUM(YEAR(scheduled_at) = YEAR(UTC_TIMESTAMP()) AND MONTH(scheduled_at) = MONTH(UTC_TIMESTAMP())
            AND status IN ('NO_SHOW', 'CANCELLED')) AS month_not_met
        FROM meeting_record"""))).mappings().one()
    keys = ("total_arranged", "total_met", "month_arranged", "month_waiting", "month_met", "month_not_met")
    return MeetingStatistics(**{key: int(row[key] or 0) for key in keys})


async def admin_create_meeting(db: AsyncSession, body: MeetingDirectCreate, actor_id: int) -> MeetingRecordResponse:
    """约会管理-添加约会：自动建立约见申请（ACCEPTED）并写入约会记录。"""
    if body.from_user_id == body.to_user_id:
        raise HTTPException(422, detail="约会双方不能为同一会员")
    members = await db.execute(
        text("SELECT id FROM users WHERE id IN (:from_id, :to_id) AND status = 1"),
        {"from_id": body.from_user_id, "to_id": body.to_user_id},
    )
    if len(members.all()) != 2:
        raise HTTPException(404, detail="男方或女方会员不存在")
    if not await db.scalar(text("SELECT 1 FROM users WHERE id = :id AND status = 1"), {"id": body.organizer_id}):
        raise HTTPException(404, detail="服务红娘不存在")
    request_result = await db.execute(text("""INSERT INTO meeting_request
        (user_id, target_user_id, matchmaker_id, organization_id, status, note)
        VALUES (:from_id, :to_id, :organizer_id, :organization_id, 'ACCEPTED', '后台添加约会记录')"""), {
        "from_id": body.from_user_id, "to_id": body.to_user_id,
        "organizer_id": body.organizer_id, "organization_id": body.organization_id,
    })
    request_id = int(request_result.lastrowid)
    location = (body.location or "").strip() or "待确定"
    scheduled_at = body.scheduled_at or datetime.now()
    record_status = "COMPLETED" if body.met else "SCHEDULED"
    result = await db.execute(text("""INSERT INTO meeting_record
        (request_id, organizer_id, organization_id, scheduled_at, location, status, member_visible, sms_remind)
        VALUES (:request_id, :organizer_id, :organization_id, :scheduled_at, :location, :status,
                :member_visible, :sms_remind)"""), {
        "request_id": request_id, "organizer_id": body.organizer_id,
        "organization_id": body.organization_id, "scheduled_at": scheduled_at,
        "location": location, "status": record_status,
        "member_visible": int(body.member_visible), "sms_remind": int(body.sms_remind),
    })
    meeting_id = int(result.lastrowid)
    await db.execute(text("""INSERT INTO business_audit_log
        (actor_user_id, action, resource_type, resource_id)
        VALUES (:actor, 'meeting.create', 'meeting_record', :id)"""), {"actor": actor_id, "id": meeting_id})
    await db.commit()
    return await admin_get_meeting(db, meeting_id)


async def create_feedback(db: AsyncSession, current: CurrentUser, meeting_id: int, request: MeetingFeedbackCreate) -> None:
    result = await db.execute(text("""SELECT mr.id, rq.user_id, rq.target_user_id, mr.status
        FROM meeting_record mr JOIN meeting_request rq ON rq.id = mr.request_id
        WHERE mr.id = :id"""), {"id": meeting_id})
    row = result.mappings().first()
    if not row or current.id not in (row["user_id"], row["target_user_id"]):
        raise HTTPException(404, detail="约会记录不存在或无权反馈")
    if row["status"] not in ("COMPLETED", "CHECKED_IN"):
        raise HTTPException(409, detail="约会尚未完成，暂不能反馈")
    await db.execute(text("""INSERT INTO meeting_feedback
        (meeting_id, user_id, target_rating, matchmaker_rating, continue_intent, private_feedback)
        VALUES (:meeting_id, :user_id, :target_rating, :matchmaker_rating, :continue_intent, :private_feedback)"""), {
        "meeting_id": meeting_id, "user_id": current.id, "target_rating": request.target_rating,
        "matchmaker_rating": request.matchmaker_rating, "continue_intent": request.continue_intent,
        "private_feedback": request.private_feedback,
    })
    await db.commit()


async def admin_list_requests(db: AsyncSession, page: int, page_size: int, status: str | None = None, search: str | None = None, matchmaker_id: int | None = None, from_date: str | None = None, to_date: str | None = None, status_group: str | None = None) -> MeetingRequestAdminPage:
    where = ["1 = 1"]
    params: dict[str, object] = {"limit": page_size, "offset": (page - 1) * page_size}
    if status:
        where.append("r.status = :status")
        params["status"] = status
    elif status_group == "pending":
        where.append("r.status IN ('SUBMITTED', 'CONTACTED')")
    elif status_group == "done":
        where.append("r.status IN ('ACCEPTED', 'DECLINED', 'CLOSED')")
    if search:
        where.append("(u.nickname LIKE CONCAT('%', :search, '%') OR t.nickname LIKE CONCAT('%', :search, '%') OR r.user_id = :search_id OR r.target_user_id = :search_id)")
        params["search"] = search
        params["search_id"] = int(search) if search.isdigit() else 0
    if matchmaker_id:
        where.append("r.matchmaker_id = :matchmaker_id")
        params["matchmaker_id"] = matchmaker_id
    if from_date:
        where.append("r.created_at >= :from_date")
        params["from_date"] = from_date
    if to_date:
        where.append("r.created_at < DATE_ADD(:to_date, INTERVAL 1 DAY)")
        params["to_date"] = to_date
    clause = " AND ".join(where)
    rows = await db.execute(text(f"""SELECT r.id, r.user_id, r.target_user_id, r.matchmaker_id,
        r.service_id, r.organization_id, r.status, r.note, r.created_at, r.updated_at,
        u.nickname AS user_nickname, CAST(r.user_id AS CHAR) AS user_member_code,
        t.nickname AS target_nickname, CAST(r.target_user_id AS CHAR) AS target_member_code,
        m.nickname AS matchmaker_name
        FROM meeting_request r
        LEFT JOIN users u ON u.id = r.user_id
        LEFT JOIN users t ON t.id = r.target_user_id
        LEFT JOIN users m ON m.id = r.matchmaker_id
        WHERE {clause} ORDER BY r.id DESC LIMIT :limit OFFSET :offset"""), params)
    total = int((await db.execute(text(f"SELECT COUNT(*) FROM meeting_request r LEFT JOIN users u ON u.id = r.user_id LEFT JOIN users t ON t.id = r.target_user_id WHERE {clause}"),
        {key: value for key, value in params.items() if key not in ("limit", "offset")})).scalar() or 0)
    return MeetingRequestAdminPage(items=[_request_response(row) for row in rows.mappings().all()], page=page, page_size=page_size, total=total, has_more=page * page_size < total)


async def admin_list_meetings(
    db: AsyncSession,
    page: int,
    page_size: int,
    status: str | None = None,
    search: str | None = None,
    organizer_id: int | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    met: str | None = None,
) -> MeetingRecordAdminPage:
    where = ["1 = 1"]
    params: dict[str, object] = {"limit": page_size, "offset": (page - 1) * page_size}
    if status:
        where.append("mr.status = :status")
        params["status"] = status
    if organizer_id:
        where.append("mr.organizer_id = :organizer_id")
        params["organizer_id"] = organizer_id
    if met == "met":
        where.append("mr.status IN ('CHECKED_IN', 'COMPLETED')")
    elif met == "wait":
        where.append("mr.status IN ('SCHEDULED', 'REMINDED')")
    if search:
        where.append("(uf.nickname LIKE CONCAT('%', :search, '%') OR ut.nickname LIKE CONCAT('%', :search, '%')"
                     " OR uf.phone LIKE CONCAT('%', :search, '%') OR ut.phone LIKE CONCAT('%', :search, '%')"
                     " OR rq.user_id = :search_id OR rq.target_user_id = :search_id)")
        params["search"] = search
        params["search_id"] = int(search) if search.isdigit() else 0
    if from_date:
        where.append("mr.scheduled_at >= :from_date")
        params["from_date"] = from_date
    if to_date:
        where.append("mr.scheduled_at < DATE_ADD(:to_date, INTERVAL 1 DAY)")
        params["to_date"] = to_date
    clause = " AND ".join(where)
    rows = await db.execute(text(f"{_ADMIN_RECORD_SELECT} WHERE {clause} ORDER BY mr.scheduled_at DESC, mr.id DESC LIMIT :limit OFFSET :offset"), params)
    total = int((await db.execute(text(f"""SELECT COUNT(*) FROM meeting_record mr
        LEFT JOIN meeting_request rq ON rq.id = mr.request_id
        LEFT JOIN users uf ON uf.id = rq.user_id
        LEFT JOIN users ut ON ut.id = rq.target_user_id
        WHERE {clause}"""),
        {key: value for key, value in params.items() if key not in ("limit", "offset")})).scalar() or 0)
    return MeetingRecordAdminPage(items=[_record_response(row) for row in rows.mappings().all()], page=page, page_size=page_size, total=total, has_more=page * page_size < total)


async def admin_get_meeting(db: AsyncSession, meeting_id: int) -> MeetingRecordResponse:
    row = (await db.execute(text(f"{_ADMIN_RECORD_SELECT} WHERE mr.id = :id"), {"id": meeting_id})).mappings().first()
    if not row:
        raise HTTPException(404, detail="约见记录不存在")
    return _record_response(row)


async def admin_update_meeting(db: AsyncSession, meeting_id: int, body: MeetingRecordAdminUpdate, actor_id: int) -> MeetingRecordResponse:
    current = await admin_get_meeting(db, meeting_id)
    values = body.model_dump(exclude_unset=True)
    if current.status == "CANCELLED" and values.get("status") not in (None, "CANCELLED"):
        raise HTTPException(409, detail="已取消的约见不能恢复")
    if values.get("status") == "CANCELLED" and not values.get("cancel_reason") and not current.cancel_reason:
        raise HTTPException(422, detail="取消约见必须填写原因")
    if values:
        updates = ", ".join(f"{key} = :{key}" for key in values)
        await db.execute(text(f"UPDATE meeting_record SET {updates}, updated_at = UTC_TIMESTAMP() WHERE id = :id"),
            {**values, "id": meeting_id})
    await db.execute(text("""INSERT INTO business_audit_log
        (actor_user_id, action, resource_type, resource_id)
        VALUES (:actor, 'meeting.update', 'meeting_record', :id)"""), {"actor": actor_id, "id": meeting_id})
    await db.commit()
    return await admin_get_meeting(db, meeting_id)


async def admin_feedback(db: AsyncSession, meeting_id: int) -> list[MeetingFeedbackAdminItem]:
    await admin_get_meeting(db, meeting_id)
    rows = await db.execute(text("""SELECT id, meeting_id, user_id, target_rating,
        matchmaker_rating, continue_intent, private_feedback, created_at
        FROM meeting_feedback WHERE meeting_id = :id ORDER BY id ASC"""), {"id": meeting_id})
    return [MeetingFeedbackAdminItem(**dict(row)) for row in rows.mappings().all()]


async def admin_options(db: AsyncSession) -> dict:
    """约会管理页面：返回服务红娘下拉 + 男方/女方候选人下拉 + 约见申请状态枚举。"""

    matchmaker_rows = await db.execute(text("""SELECT u.id, u.nickname, u.avatar, u.phone
        FROM users u JOIN user_role ur ON ur.user_id = u.id AND ur.role_code = 'service_matchmaker' AND ur.status = 1
        WHERE u.status = 1 ORDER BY u.id DESC LIMIT 200"""))
    matchmakers = [dict(row) for row in matchmaker_rows.mappings().all()]

    member_rows = await db.execute(text("""SELECT u.id, u.nickname, u.avatar, u.phone, u.gender, u.birthday
        FROM users u WHERE u.status = 1 ORDER BY u.id DESC LIMIT 200"""))
    candidates = [dict(row) for row in member_rows.mappings().all()]

    return {
        "matchmakers": matchmakers,
        "candidates": candidates,
        "request_status": [
            {"value": "SUBMITTED", "label": "待处理"},
            {"value": "CONTACTED", "label": "已联系"},
            {"value": "ACCEPTED", "label": "已通过"},
            {"value": "DECLINED", "label": "已拒绝"},
            {"value": "CLOSED", "label": "已关闭"},
        ],
        "record_status": [
            {"value": "SCHEDULED", "label": "待见面"},
            {"value": "REMINDED", "label": "已提醒"},
            {"value": "CHECKED_IN", "label": "已签到"},
            {"value": "COMPLETED", "label": "已完成"},
            {"value": "CANCELLED", "label": "已取消"},
            {"value": "NO_SHOW", "label": "未到场"},
        ],
    }


async def admin_delete_request(db: AsyncSession, request_id: int, actor_id: int) -> bool:
    row = (await db.execute(text("SELECT id, status FROM meeting_request WHERE id = :id FOR UPDATE"), {"id": request_id})).mappings().first()
    if not row:
        raise HTTPException(404, detail="约见申请不存在")
    if row["status"] in ("CLOSED",):
        # 软关闭记录：物理删除
        pass
    await db.execute(text("DELETE FROM meeting_request WHERE id = :id"), {"id": request_id})
    await db.execute(text("""INSERT INTO business_audit_log
        (actor_user_id, action, resource_type, resource_id)
        VALUES (:actor, 'meeting_request.delete', 'meeting_request', :id)"""), {"actor": actor_id, "id": request_id})
    await db.commit()
    return True


async def admin_delete_meeting(db: AsyncSession, meeting_id: int, actor_id: int) -> bool:
    row = (await db.execute(text("SELECT id, status FROM meeting_record WHERE id = :id FOR UPDATE"), {"id": meeting_id})).mappings().first()
    if not row:
        raise HTTPException(404, detail="约会记录不存在")
    if row["status"] == "COMPLETED":
        raise HTTPException(409, detail="已完成的约会记录不能删除")
    # 先清掉已挂的反馈
    await db.execute(text("DELETE FROM meeting_feedback WHERE meeting_id = :id"), {"id": meeting_id})
    await db.execute(text("DELETE FROM meeting_record WHERE id = :id"), {"id": meeting_id})
    await db.execute(text("""INSERT INTO business_audit_log
        (actor_user_id, action, resource_type, resource_id)
        VALUES (:actor, 'meeting_record.delete', 'meeting_record', :id)"""), {"actor": actor_id, "id": meeting_id})
    await db.commit()
    return True

"""Read-only record feeds used by the member CRM detail workspace."""

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentMatchmakerAdmin, get_current_matchmaker_admin
from app.db.session import get_db

router = APIRouter(prefix="/admin/members")


class MemberCallRecordCreate(BaseModel):
    direction: str = Field(default="OUTBOUND", pattern="^(INBOUND|OUTBOUND)$")
    status: str = Field(default="COMPLETED", pattern="^(COMPLETED|MISSED|FAILED)$")
    duration_seconds: int = Field(default=0, ge=0, le=86400)
    remark: str | None = Field(default=None, max_length=2000)


async def _page(db: AsyncSession, query: str, count_query: str, member_id: int, page: int, page_size: int) -> dict:
    params = {"id": member_id, "limit": page_size, "offset": (page - 1) * page_size}
    try:
        rows = await db.execute(text(query), params)
        total = int((await db.scalar(text(count_query), {"id": member_id})) or 0)
        return {"items": [dict(row) for row in rows.mappings().all()], "page": page, "page_size": page_size, "total": total, "has_more": page * page_size < total}
    except SQLAlchemyError:
        # Optional CRM tables may not exist in an older deployment. Keep the tab usable.
        await db.rollback()
        return {"items": [], "page": page, "page_size": page_size, "total": 0, "has_more": False}


async def _ensure_member(db: AsyncSession, member_id: int) -> None:
    from fastapi import HTTPException
    if not await db.scalar(text("SELECT 1 FROM users WHERE id = :id"), {"id": member_id}):
        raise HTTPException(404, detail="会员不存在")


def _paging():
    return (Query(1, ge=1, le=1000), Query(20, ge=1, le=100))


@router.get("/{member_id}/match-records")
async def match_records(member_id: int = Path(..., ge=1), page: int = _paging()[0], page_size: int = _paging()[1], current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> dict:
    await _ensure_member(db, member_id)
    return await _page(db, """SELECT a.id, a.from_user_id, a.to_user_id, u.nickname AS target_nickname, a.message, a.status, a.responded_at, a.created_at
        FROM match_apply a LEFT JOIN users u ON u.id = CASE WHEN a.from_user_id = :id THEN a.to_user_id ELSE a.from_user_id END
        WHERE a.from_user_id = :id OR a.to_user_id = :id ORDER BY a.created_at DESC, a.id DESC LIMIT :limit OFFSET :offset""",
        "SELECT COUNT(*) FROM match_apply WHERE from_user_id = :id OR to_user_id = :id", member_id, page, page_size)


@router.get("/{member_id}/recommendations")
async def recommendations(member_id: int = Path(..., ge=1), page: int = _paging()[0], page_size: int = _paging()[1], current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> dict:
    await _ensure_member(db, member_id)
    return await _page(db, """SELECT r.id, r.recommend_date, r.match_score, r.match_reason, r.recommend_source,
        r.is_viewed, r.is_liked, r.is_passed, r.created_at, u.id AS target_user_id, u.nickname AS target_nickname
        FROM user_match_recommend r LEFT JOIN users u ON u.id = r.recommend_user_id
        WHERE r.user_id = :id ORDER BY r.recommend_date DESC, r.id DESC LIMIT :limit OFFSET :offset""",
        "SELECT COUNT(*) FROM user_match_recommend WHERE user_id = :id", member_id, page, page_size)


@router.get("/{member_id}/dating-records")
async def dating_records(member_id: int = Path(..., ge=1), page: int = _paging()[0], page_size: int = _paging()[1], current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> dict:
    await _ensure_member(db, member_id)
    return await _page(db, """SELECT r.id, r.request_id, r.scheduled_at, r.location, r.status, r.cancel_reason, r.created_at,
        q.user_id, q.target_user_id, u.nickname AS target_nickname
        FROM meeting_record r JOIN meeting_request q ON q.id = r.request_id
        LEFT JOIN users u ON u.id = CASE WHEN q.user_id = :id THEN q.target_user_id ELSE q.user_id END
        WHERE q.user_id = :id OR q.target_user_id = :id ORDER BY r.scheduled_at DESC, r.id DESC LIMIT :limit OFFSET :offset""",
        "SELECT COUNT(*) FROM meeting_record r JOIN meeting_request q ON q.id = r.request_id WHERE q.user_id = :id OR q.target_user_id = :id", member_id, page, page_size)


@router.get("/{member_id}/media")
async def media(member_id: int = Path(..., ge=1), page: int = _paging()[0], page_size: int = _paging()[1], current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> dict:
    await _ensure_member(db, member_id)
    return await _page(db, """SELECT id, media_type, file_url, thumbnail_url, mime_type, duration_seconds, sort_order, is_primary, review_status, created_at
        FROM user_media WHERE user_id = :id AND deleted_at IS NULL ORDER BY sort_order ASC, id DESC LIMIT :limit OFFSET :offset""",
        "SELECT COUNT(*) FROM user_media WHERE user_id = :id AND deleted_at IS NULL", member_id, page, page_size)


@router.get("/{member_id}/activity-signups")
async def activity_signups(member_id: int = Path(..., ge=1), page: int = _paging()[0], page_size: int = _paging()[1], current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> dict:
    await _ensure_member(db, member_id)
    return await _page(db, """SELECT s.id, s.activity_id, a.title AS activity_title, a.start_time, a.end_time,
        s.real_name, s.phone, s.remark, s.status, s.cancel_reason, s.created_at, s.updated_at
        FROM activity_signup s LEFT JOIN offline_activity a ON a.id = s.activity_id
        WHERE s.user_id = :id ORDER BY s.created_at DESC, s.id DESC LIMIT :limit OFFSET :offset""",
        "SELECT COUNT(*) FROM activity_signup WHERE user_id = :id", member_id, page, page_size)


@router.get("/{member_id}/private-info")
async def private_info(member_id: int = Path(..., ge=1), current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> dict:
    await _ensure_member(db, member_id)
    row = (await db.execute(text("""SELECT u.id, u.phone, ua.real_name, ua.id_card, ua.company,
        p.family_background, p.single_reason, p.online_status, p.last_active_at
        FROM users u LEFT JOIN user_auth ua ON ua.user_id = u.id LEFT JOIN user_profile p ON p.user_id = u.id
        WHERE u.id = :id"""), {"id": member_id})).mappings().first()
    return {"items": [dict(row)] if row else [], "page": 1, "page_size": 1, "total": 1 if row else 0, "has_more": False}


@router.get("/{member_id}/super-info")
async def super_info(member_id: int = Path(..., ge=1), current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> dict:
    await _ensure_member(db, member_id)
    row = (await db.execute(text("""SELECT u.id, u.status, u.created_at, u.updated_at,
        CASE WHEN v.user_id IS NOT NULL AND (v.end_at IS NULL OR v.end_at > UTC_TIMESTAMP()) THEN 1 ELSE 0 END AS is_vip,
        v.end_at AS vip_end_at, a.matchmaker_id, a.organization_id
        FROM users u LEFT JOIN (SELECT user_id, MAX(end_at) end_at FROM user_membership WHERE status = 1 GROUP BY user_id) v ON v.user_id = u.id
        LEFT JOIN (SELECT user_id, MAX(id) id, MAX(matchmaker_id) matchmaker_id, MAX(organization_id) organization_id FROM resource_assignment WHERE status = 1 GROUP BY user_id) a ON a.user_id = u.id
        WHERE u.id = :id"""), {"id": member_id})).mappings().first()
    return {"items": [dict(row)] if row else [], "page": 1, "page_size": 1, "total": 1 if row else 0, "has_more": False}


@router.get("/{member_id}/call-records")
async def call_records(member_id: int = Path(..., ge=1), page: int = _paging()[0], page_size: int = _paging()[1], current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> dict:
    await _ensure_member(db, member_id)
    return await _page(db, """SELECT id, user_id, direction, status, duration_seconds, remark, created_by, created_at
        FROM member_call_record WHERE user_id = :id ORDER BY created_at DESC, id DESC LIMIT :limit OFFSET :offset""",
        "SELECT COUNT(*) FROM member_call_record WHERE user_id = :id", member_id, page, page_size)


@router.post("/{member_id}/call-records", status_code=201)
async def create_call_record(member_id: int = Path(..., ge=1), body: MemberCallRecordCreate = ..., current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> dict:
    await _ensure_member(db, member_id)
    result = await db.execute(text("""INSERT INTO member_call_record
        (user_id, direction, status, duration_seconds, remark, created_by)
        VALUES (:user_id, :direction, :status, :duration_seconds, :remark, :created_by)"""), {
        **body.model_dump(), "user_id": member_id, "created_by": current.account.id,
    })
    record_id = int(result.lastrowid)
    await db.commit()
    row = (await db.execute(text("""SELECT id, user_id, direction, status, duration_seconds, remark, created_by, created_at
        FROM member_call_record WHERE id = :id"""), {"id": record_id})).mappings().one()
    return dict(row)


@router.get("/{member_id}/source-records")
async def source_records(member_id: int = Path(..., ge=1), page: int = _paging()[0], page_size: int = _paging()[1], current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> dict:
    await _ensure_member(db, member_id)
    # There is no dedicated provenance table yet; expose known attribution history.
    try:
        return await _page(db, """SELECT 'assignment' AS source_type, id AS source_id, source, status, effective_at AS occurred_at
            FROM resource_assignment WHERE user_id = :id
            UNION ALL SELECT 'promotion' AS source_type, id AS source_id, 'promotion' AS source, status, effective_at AS occurred_at
            FROM promotion_attribution WHERE user_id = :id
            ORDER BY occurred_at DESC, source_id DESC LIMIT :limit OFFSET :offset""",
            "SELECT (SELECT COUNT(*) FROM resource_assignment WHERE user_id = :id) + (SELECT COUNT(*) FROM promotion_attribution WHERE user_id = :id)", member_id, page, page_size)
    except SQLAlchemyError:
        await db.rollback()
        return {"items": [], "page": page, "page_size": page_size, "total": 0, "has_more": False}

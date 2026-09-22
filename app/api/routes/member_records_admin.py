"""Read-only record feeds used by the member CRM detail workspace."""

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentMatchmakerAdmin, get_current_matchmaker_admin
from app.db.session import get_db

router = APIRouter(prefix="/admin/members")


class MemberRecommendationCreate(BaseModel):
    """后台手工把一名会员推荐给指定会员。"""

    recommend_user_id: int = Field(..., ge=1, description="要推荐的会员 ID")
    recommend_date: date | None = Field(default=None, description="推荐日期，默认当天")
    match_reason: str | None = Field(default=None, max_length=255, description="推荐理由")


class MemberRecommendationResponse(BaseModel):
    id: int
    user_id: int
    recommend_user_id: int
    recommend_date: date
    match_score: float
    match_reason: str | None = None
    recommend_source: str
    created_at: datetime | None = None


class MemberRecommendationHistoryItem(BaseModel):
    id: int
    recommend_date: date
    match_score: float
    match_reason: str | None = None
    recommend_source: str
    is_viewed: bool
    is_liked: bool
    is_passed: bool
    created_at: datetime | None = None
    target_user_id: int | None = None
    target_nickname: str | None = None
    target_avatar: str | None = None


class MemberRecommendationHistoryPage(BaseModel):
    items: list[MemberRecommendationHistoryItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class MemberMatchRecordItem(BaseModel):
    """会员详情中的单条牵线记录。"""

    id: int
    from_user_id: int
    to_user_id: int
    target_nickname: str | None = None
    message: str | None = None
    status: int
    responded_at: datetime | None = None
    created_at: datetime | None = None


class MemberMatchRecordPage(BaseModel):
    """会员详情牵线记录分页响应。"""

    items: list[MemberMatchRecordItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class MemberMatchQuotaResponse(BaseModel):
    """会员牵线次数账户。"""

    user_id: int
    available_count: int
    used_count: int
    refunded_count: int
    updated_at: datetime | None = None


class MemberDatingRecordItem(BaseModel):
    """会员详情约会记录；from/to 始终标识双方会员。"""

    id: int
    request_id: int
    scheduled_at: datetime | None = None
    location: str | None = None
    status: str
    cancel_reason: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    from_user_id: int
    from_nickname: str | None = None
    to_user_id: int
    to_nickname: str | None = None


class MemberDatingRecordPage(BaseModel):
    items: list[MemberDatingRecordItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class MemberMeetingRequestItem(BaseModel):
    id: int
    user_id: int
    user_nickname: str | None = None
    target_user_id: int
    target_nickname: str | None = None
    service_id: int | None = None
    matchmaker_id: int | None = None
    organization_id: int | None = None
    status: str
    note: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class MemberMeetingRequestPage(BaseModel):
    items: list[MemberMeetingRequestItem]
    page: int
    page_size: int
    total: int
    has_more: bool


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


@router.get("/{member_id}/match-records", response_model=MemberMatchRecordPage, summary="查询会员牵线记录")
async def match_records(member_id: int = Path(..., ge=1), page: int = _paging()[0], page_size: int = _paging()[1], current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> MemberMatchRecordPage:
    await _ensure_member(db, member_id)
    result = await _page(db, """SELECT a.id, a.from_user_id, a.to_user_id, u.nickname AS target_nickname, a.message, a.status, a.responded_at, a.created_at
        FROM match_apply a LEFT JOIN users u ON u.id = CASE WHEN a.from_user_id = :id THEN a.to_user_id ELSE a.from_user_id END
        WHERE a.from_user_id = :id OR a.to_user_id = :id ORDER BY a.created_at DESC, a.id DESC LIMIT :limit OFFSET :offset""",
        "SELECT COUNT(*) FROM match_apply WHERE from_user_id = :id OR to_user_id = :id", member_id, page, page_size)
    return MemberMatchRecordPage(**result)


@router.get("/{member_id}/match-quota", response_model=MemberMatchQuotaResponse, summary="查询会员剩余牵线次数")
async def match_quota(
    member_id: int = Path(..., ge=1, description="会员 ID"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MemberMatchQuotaResponse:
    """查询会员牵线次数账户；没有账户记录时按零次数返回。"""
    await _ensure_member(db, member_id)
    row = (await db.execute(text("""SELECT user_id, available_count, used_count, refunded_count, updated_at
        FROM matchmaker_service_quota WHERE user_id = :id"""), {"id": member_id})).mappings().first()
    if not row:
        return MemberMatchQuotaResponse(user_id=member_id, available_count=0, used_count=0, refunded_count=0)
    return MemberMatchQuotaResponse(**dict(row))


@router.get("/{member_id}/recommend-history", response_model=MemberRecommendationHistoryPage, summary="查询会员已添加的推荐名单")
async def recommendations(member_id: int = Path(..., ge=1, description="接收推荐的会员 ID"), page: int = _paging()[0], page_size: int = _paging()[1], current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> MemberRecommendationHistoryPage:
    """已推荐名单（历史推荐记录）。路径由 ``/recommendations`` 改为 ``/recommend-history``：
    原路径与 ``member_media_admin`` 的「按条件筛选推荐候选人」完全同形，且本文件在 router.py
    中注册更早，会遮蔽后者导致筛选参数被静默忽略。"""
    await _ensure_member(db, member_id)
    return await _page(db, """SELECT r.id, r.recommend_date, r.match_score, r.match_reason, r.recommend_source,
        r.is_viewed, r.is_liked, r.is_passed, r.created_at, u.id AS target_user_id,
        u.nickname AS target_nickname, u.avatar AS target_avatar
        FROM user_match_recommend r LEFT JOIN users u ON u.id = r.recommend_user_id
        WHERE r.user_id = :id ORDER BY r.recommend_date DESC, r.id DESC LIMIT :limit OFFSET :offset""",
        "SELECT COUNT(*) FROM user_match_recommend WHERE user_id = :id", member_id, page, page_size)


@router.post("/{member_id}/recommendations", response_model=MemberRecommendationResponse, status_code=201, summary="手工添加推荐名单")
async def create_recommendation(
    member_id: int = Path(..., ge=1, description="已推荐给谁的会员 ID"),
    body: MemberRecommendationCreate = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MemberRecommendationResponse:
    """从会员 CRM 中选择一名会员，手工加入指定会员的推荐名单。"""
    await _ensure_member(db, member_id)
    if body.recommend_user_id == member_id:
        raise HTTPException(422, detail="不能把会员推荐给自己")
    if not await db.scalar(text("SELECT 1 FROM users WHERE id = :id"), {"id": body.recommend_user_id}):
        raise HTTPException(404, detail="推荐会员不存在")

    recommend_date = body.recommend_date or date.today()
    duplicate = await db.scalar(
        text("""SELECT id FROM user_match_recommend
               WHERE user_id = :user_id AND recommend_user_id = :recommend_user_id
                 AND recommend_date = :recommend_date LIMIT 1"""),
        {"user_id": member_id, "recommend_user_id": body.recommend_user_id, "recommend_date": recommend_date},
    )
    if duplicate:
        raise HTTPException(409, detail="该会员今日已在推荐名单中")

    result = await db.execute(text("""INSERT INTO user_match_recommend
        (user_id, recommend_user_id, recommend_date, match_score, match_reason, recommend_source)
        VALUES (:user_id, :recommend_user_id, :recommend_date, 0, :match_reason, 'manual')"""), {
        "user_id": member_id,
        "recommend_user_id": body.recommend_user_id,
        "recommend_date": recommend_date,
        "match_reason": body.match_reason,
    })
    await db.commit()
    row = (await db.execute(text("""SELECT id, user_id, recommend_user_id, recommend_date,
        match_score, match_reason, recommend_source, created_at
        FROM user_match_recommend WHERE id = :id"""), {"id": int(result.lastrowid)})).mappings().one()
    return MemberRecommendationResponse(**dict(row))


@router.get("/{member_id}/dating-records", response_model=MemberDatingRecordPage, summary="查询会员约会记录")
async def dating_records(
    member_id: int = Path(..., ge=1),
    page: int = _paging()[0],
    page_size: int = _paging()[1],
    status_group: str | None = Query(None, pattern="^(all|waiting|not_met|met)$", description="all全部，waiting待见面，not_met未见面，met成功见面"),
    status: str | None = Query(None, pattern="^(SCHEDULED|REMINDED|CHECKED_IN|COMPLETED|CANCELLED|NO_SHOW)$"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MemberDatingRecordPage:
    await _ensure_member(db, member_id)
    filters = ["(q.user_id = :id OR q.target_user_id = :id)"]
    if status:
        filters.append("r.status = :status")
    elif status_group == "waiting":
        filters.append("r.status IN ('SCHEDULED', 'REMINDED')")
    elif status_group == "not_met":
        filters.append("r.status IN ('CANCELLED', 'NO_SHOW')")
    elif status_group == "met":
        filters.append("r.status IN ('CHECKED_IN', 'COMPLETED')")
    where = " AND ".join(filters)
    params = {"id": member_id, "limit": page_size, "offset": (page - 1) * page_size}
    if status:
        params["status"] = status
    try:
        rows = await db.execute(text(f"""SELECT r.id, r.request_id, r.scheduled_at, r.location, r.status, r.cancel_reason, r.created_at, r.updated_at,
        q.user_id AS from_user_id, uf.nickname AS from_nickname, q.target_user_id AS to_user_id, ut.nickname AS to_nickname
        FROM meeting_record r JOIN meeting_request q ON q.id = r.request_id
        LEFT JOIN users uf ON uf.id = q.user_id LEFT JOIN users ut ON ut.id = q.target_user_id
        WHERE {where} ORDER BY r.scheduled_at DESC, r.id DESC LIMIT :limit OFFSET :offset"""), params)
        total = int((await db.execute(text(f"SELECT COUNT(*) FROM meeting_record r JOIN meeting_request q ON q.id = r.request_id WHERE {where}"), {"id": member_id, **({"status": status} if status else {})})).scalar() or 0)
        return MemberDatingRecordPage(items=[MemberDatingRecordItem(**dict(row)) for row in rows.mappings().all()], page=page, page_size=page_size, total=total, has_more=page * page_size < total)
    except SQLAlchemyError:
        await db.rollback()
        return MemberDatingRecordPage(items=[], page=page, page_size=page_size, total=0, has_more=False)


@router.get("/{member_id}/meeting-requests", response_model=MemberMeetingRequestPage, summary="查询会员约见申请")
async def meeting_requests(
    member_id: int = Path(..., ge=1), page: int = _paging()[0], page_size: int = _paging()[1],
    status: str | None = Query(None, pattern="^(SUBMITTED|CONTACTED|ACCEPTED|DECLINED|CLOSED)$"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db),
) -> MemberMeetingRequestPage:
    await _ensure_member(db, member_id)
    status_clause = " AND q.status = :status" if status else ""
    params = {"id": member_id, "limit": page_size, "offset": (page - 1) * page_size}
    if status:
        params["status"] = status
    try:
        rows = await db.execute(text(f"""SELECT q.id, q.user_id, uf.nickname AS user_nickname, q.target_user_id,
            ut.nickname AS target_nickname, q.service_id, q.matchmaker_id, q.organization_id, q.status, q.note,
            q.created_at, q.updated_at FROM meeting_request q
            LEFT JOIN users uf ON uf.id = q.user_id LEFT JOIN users ut ON ut.id = q.target_user_id
            WHERE (q.user_id = :id OR q.target_user_id = :id){status_clause}
            ORDER BY q.created_at DESC, q.id DESC LIMIT :limit OFFSET :offset"""), params)
        total = int((await db.execute(text(f"SELECT COUNT(*) FROM meeting_request q WHERE (q.user_id = :id OR q.target_user_id = :id){status_clause}"), {"id": member_id, **({"status": status} if status else {})})).scalar() or 0)
        return MemberMeetingRequestPage(items=[MemberMeetingRequestItem(**dict(row)) for row in rows.mappings().all()], page=page, page_size=page_size, total=total, has_more=page * page_size < total)
    except SQLAlchemyError:
        await db.rollback()
        return MemberMeetingRequestPage(items=[], page=page, page_size=page_size, total=0, has_more=False)


@router.get("/{member_id}/media-records")
async def media(member_id: int = Path(..., ge=1), page: int = _paging()[0], page_size: int = _paging()[1], current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> dict:
    """会员媒体原始记录（含审核中的行）。路径由 ``/media`` 改为 ``/media-records``：
    原路径与 ``member_media_admin`` 的媒体审核列表同形且注册更早，会遮蔽后者。"""
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


@router.get("/{member_id}/private-info-records")
async def private_info(member_id: int = Path(..., ge=1), current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> dict:
    """会员基础隐私摘要（手机/实名/家庭背景等）。路径由 ``/private-info`` 改为
    ``/private-info-records``：原路径与 ``member_media_admin`` 的私密资料接口同形且注册更早。"""
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

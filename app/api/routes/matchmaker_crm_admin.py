"""Member CRM endpoints protected by the independent matchmaker admin session."""

import json

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentMatchmakerAdmin, get_current_matchmaker_admin
from app.core.sensitive_fields import mask_contact, redaction_level_from_permissions
from app.db.session import get_db
from app.schemas.matchmaker_crm_admin import MatchRecordCreate, MatchRecordItem, MatchRecordPage, MatchRecordResponse, MemberAssignmentResponse, MemberAssignmentUpdate, MemberDetail, MemberListItem, MemberPage, MemberStatistics, MemberStatusResponse, MemberStatusUpdate
from app.schemas.admin import CertificationReviewRequest, RealnameReviewRequest
from app.services.auth import list_realname_reviews, review_realname
from app.services.certifications import list_certification_reviews, review_certification

router = APIRouter(prefix="/admin/matchmaker")

# 会员归属展示列：每个会员只取一条生效归属，避免 `LEFT JOIN resource_assignment`
# 因一人多单而放大结果行数（历史实现使用非聚合子查询，会在列表分页中产生重复行）。
# 数据可见范围不使用该派生表，而统一走 `CurrentMatchmakerAdmin.scope_exists_clause`，
# 以保证「任一生效归属落在作用域内即可见」的正确语义。
_ASSIGNMENT_DISPLAY_DERIVED = (
    "(SELECT user_id, MAX(matchmaker_id) AS matchmaker_id FROM resource_assignment "
    "WHERE status = 1 GROUP BY user_id)"
)


class MemberBatchStatus(BaseModel):
    member_ids: list[int] = Field(min_length=1, max_length=200)
    status: int = Field(ge=1, le=3)
    reason: str = Field(min_length=1, max_length=255)


@router.get("/match-records", response_model=MatchRecordPage, summary="管理员查询牵线记录")
async def match_records(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, max_length=64),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MatchRecordPage:
    where = ["1=1"]
    params: dict[str, object] = {"limit": page_size, "offset": (page - 1) * page_size}
    if search:
        where.append("(fu.nickname LIKE CONCAT('%', :search, '%') OR tu.nickname LIKE CONCAT('%', :search, '%') OR fu.phone LIKE CONCAT('%', :search, '%') OR tu.phone LIKE CONCAT('%', :search, '%'))")
        params["search"] = search
    # 牵线记录会把双方昵称与手机号一并下发，按「发起方（from）会员归属」收口。
    where.append(current.scope_exists_clause(params, correlation="scope_assignment.user_id = a.from_user_id"))
    clause = " AND ".join(where)
    base = "FROM match_apply a JOIN users fu ON fu.id = a.from_user_id JOIN users tu ON tu.id = a.to_user_id"
    rows = await db.execute(text(f"""SELECT a.id, a.from_user_id, a.to_user_id, a.status, a.created_at, a.responded_at,
        fu.nickname AS from_nickname, tu.nickname AS to_nickname,
        ra.matchmaker_id
        {base} LEFT JOIN (SELECT user_id, MAX(matchmaker_id) AS matchmaker_id FROM resource_assignment WHERE status = 1 GROUP BY user_id) ra ON ra.user_id = a.from_user_id
        WHERE {clause} ORDER BY a.created_at DESC, a.id DESC LIMIT :limit OFFSET :offset"""), params)
    total = int((await db.execute(text(f"SELECT COUNT(*) {base} WHERE {clause}"), {k: v for k, v in params.items() if k not in ("limit", "offset")})).scalar() or 0)
    return MatchRecordPage(items=[MatchRecordItem(**dict(row)) for row in rows.mappings().all()], page=page, page_size=page_size, total=total, has_more=page * page_size < total)


@router.post("/match-records", response_model=MatchRecordResponse, status_code=201, summary="管理员新增牵线记录")
async def create_match_record(
    body: MatchRecordCreate,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MatchRecordResponse:
    if body.from_love_user_id == body.to_love_user_id:
        raise HTTPException(status_code=422, detail="牵线会员与被牵线会员不能相同")
    if body.complete_time < body.create_time:
        raise HTTPException(status_code=422, detail="牵线完成时间不能早于申请时间")
    ids = {"from_id": body.from_love_user_id, "to_id": body.to_love_user_id}
    members = await db.execute(text("SELECT id FROM users WHERE id IN (:from_id, :to_id)"), ids)
    if len(members.all()) != 2:
        raise HTTPException(status_code=404, detail="会员不存在")
    existing = await db.execute(text("SELECT id FROM match_apply WHERE from_user_id=:from_id AND to_user_id=:to_id AND created_at=:created_at LIMIT 1"), {**ids, "created_at": body.create_time})
    if existing.scalar():
        raise HTTPException(status_code=409, detail="该牵线记录已存在")
    result = await db.execute(text("""INSERT INTO match_apply
        (from_user_id, to_user_id, message, status, responded_at, expire_at, created_at, updated_at)
        VALUES (:from_id, :to_id, '后台添加牵线记录', :status, :responded_at, NULL, :created_at, UTC_TIMESTAMP())"""), {
        **ids, "status": body.line_status, "created_at": body.create_time, "responded_at": body.complete_time,
    })
    if body.line_status == 1:
        await db.execute(text("""INSERT INTO user_match (user_id, target_user_id, status)
            VALUES (:left, :right, 1), (:right, :left, 1)
            ON DUPLICATE KEY UPDATE status = 1, updated_at = UTC_TIMESTAMP()"""), {"left": body.from_love_user_id, "right": body.to_love_user_id})
    await db.commit()
    return MatchRecordResponse(id=int(result.lastrowid), from_user_id=body.from_love_user_id, to_user_id=body.to_love_user_id, status=body.line_status, created_at=body.create_time, responded_at=body.complete_time)


async def _member_query(db: AsyncSession, where: str, params: dict, page: int, page_size: int, scope: str) -> MemberPage:
    """会员 CRM 列表查询。

    ``scope`` 必须由调用方通过 `CurrentMatchmakerAdmin.scope_exists_clause`
    生成——本函数不接受手写作用域谓词，也不允许为空（fail-closed）。
    """
    column_rows = await db.execute(text("""SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ('user_profile', 'user_auth')"""))
    available = {(row[0], row[1]) for row in column_rows.all()}

    def auth_expr(name: str) -> str:
        return f"ua.{name}" if ("user_auth", name) in available else "NULL"

    def profile_expr(name: str) -> str:
        return f"p.{name}" if ("user_profile", name) in available else "NULL"

    base = f"""FROM users u LEFT JOIN user_profile p ON p.user_id = u.id
        LEFT JOIN user_auth ua ON ua.user_id = u.id
        LEFT JOIN user_privacy pr ON pr.user_id = u.id
        LEFT JOIN (SELECT user_id, MAX(end_at) AS vip_end_at FROM user_membership WHERE status = 1 GROUP BY user_id) v ON v.user_id = u.id
        LEFT JOIN {_ASSIGNMENT_DISPLAY_DERIVED} a ON a.user_id = u.id
        LEFT JOIN (SELECT user_id, MAX(created_at) last_follow_at, MAX(next_follow_at) next_follow_at FROM member_follow_up GROUP BY user_id) f ON f.user_id = u.id"""
    where = f"({where}) AND ({scope})"
    params = {**params, "limit": page_size, "offset": (page - 1) * page_size}
    sort_by = str(params.pop("sort_by", "created_at"))
    # Keep sort fields server-side whitelisted; never interpolate user input.
    sort_sql = {
        "created_at": "u.created_at DESC, u.id DESC",
        "last_login_at": "COALESCE(u.last_login_at, '1970-01-01') DESC, u.id DESC",
        "last_follow_at": "COALESCE(f.last_follow_at, '1970-01-01') DESC, u.id DESC",
        "next_follow_at": "COALESCE(f.next_follow_at, '9999-12-31') ASC, u.id DESC",
        "id": "u.id DESC",
    }.get(sort_by, "u.created_at DESC, u.id DESC")
    rows = await db.execute(text(f"""SELECT u.id, u.nickname, u.phone, u.gender, u.status, u.created_at,
        COALESCE(u.avatar, JSON_UNQUOTE(JSON_EXTRACT(p.photos, '$[0]'))) AS avatar,
        u.birthday, u.is_married, p.height, p.income, p.hometown, p.residence,
        {profile_expr('constellation')} AS constellation, {profile_expr('zodiac')} AS zodiac,
        {profile_expr('household')} AS household, {profile_expr('ethnicity')} AS ethnicity,
        {profile_expr('house')} AS house, {profile_expr('car')} AS car, {profile_expr('smoking')} AS smoking,
        {profile_expr('drinking')} AS drinking, {profile_expr('religion')} AS religion,
        {profile_expr('marriage_plan')} AS marriage_plan,
        {auth_expr('education')} AS education, {auth_expr('school')} AS school,
        {auth_expr('job')} AS job, {auth_expr('company')} AS company,
        COALESCE({auth_expr('auth_status')}, 0) AS auth_status, {profile_expr('intention_level')} AS intention_level, f.last_follow_at, f.next_follow_at,
        v.vip_end_at, a.matchmaker_id, COALESCE(pr.match_status, 1) AS match_status,
        CASE WHEN v.user_id IS NULL OR (v.vip_end_at IS NOT NULL AND v.vip_end_at <= UTC_TIMESTAMP()) THEN 0 ELSE 1 END AS is_vip
        {base} WHERE {where} ORDER BY {sort_sql} LIMIT :limit OFFSET :offset"""), params)
    count = await db.execute(text(f"SELECT COUNT(*) {base} WHERE {where}"), {k: v for k, v in params.items() if k not in ("limit", "offset")})
    total = int(count.scalar() or 0)
    items = [MemberListItem(**dict(row)) for row in rows.mappings().all()]
    return MemberPage(items=items, page=page, page_size=page_size, total=total, has_more=page * page_size < total)


@router.get("/members", response_model=MemberPage, summary="查询会员 CRM 列表")
async def members(page: int = Query(1, ge=1, le=1000), page_size: int = Query(20, ge=1, le=100), gender: int | None = Query(None, ge=1, le=2), status: int | None = Query(None, ge=1, le=3), vip: bool | None = Query(None), auth_status: int | None = Query(None, ge=0, le=3), assigned: bool | None = Query(None), follow_state: str | None = Query(None, pattern="^(never|due_today|overdue)$"), search: str | None = Query(None, max_length=64), sort_by: str = Query("created_at", pattern="^(created_at|last_login_at|last_follow_at|next_follow_at|id)$"), current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> MemberPage:
    where = "1=1"
    params: dict = {}
    if gender is not None:
        where += " AND u.gender = :gender"
        params["gender"] = gender
    if status is not None:
        where += " AND u.status = :status"
        params["status"] = status
    if search:
        if search.isdigit():
            where += " AND (u.id = :search_id OR u.nickname LIKE CONCAT('%', :search, '%') OR u.phone LIKE CONCAT('%', :search, '%'))"
            params["search_id"] = int(search)
        else:
            where += " AND (u.nickname LIKE CONCAT('%', :search, '%') OR u.phone LIKE CONCAT('%', :search, '%'))"
        params["search"] = search
    if vip is True:
        where += " AND v.user_id IS NOT NULL AND (v.vip_end_at IS NULL OR v.vip_end_at > UTC_TIMESTAMP())"
    if vip is False:
        where += " AND (v.user_id IS NULL OR (v.vip_end_at IS NOT NULL AND v.vip_end_at <= UTC_TIMESTAMP()))"
    if auth_status is not None:
        where += " AND COALESCE(ua.auth_status, 0) = :auth_status"
        params["auth_status"] = auth_status
    if assigned is True:
        where += " AND a.matchmaker_id IS NOT NULL"
    if assigned is False:
        where += " AND a.matchmaker_id IS NULL"
    if follow_state == "never":
        where += " AND f.last_follow_at IS NULL"
    if follow_state == "due_today":
        where += " AND f.next_follow_at >= CURDATE() AND f.next_follow_at < DATE_ADD(CURDATE(), INTERVAL 1 DAY)"
    if follow_state == "overdue":
        where += " AND f.next_follow_at < CURDATE()"
    params["sort_by"] = sort_by
    scope = current.scope_exists_clause(params, correlation="scope_assignment.user_id = u.id")
    return await _member_query(db, where, params, page, page_size, scope)


@router.get("/members/statistics", response_model=MemberStatistics, summary="查询会员统计")
async def member_statistics(current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> MemberStatistics:
    params: dict[str, object] = {}
    scope = current.scope_exists_clause(params, correlation="scope_assignment.user_id = u.id")
    # VIP 子查询必须同口径收口：历史实现统计全平台 VIP，会向门店/组织账号泄露全局规模。
    vip_scope = current.scope_exists_clause(
        params,
        correlation="vip_scope_assignment.user_id = vm.user_id",
        assignment_alias="vip_scope_assignment",
    )
    row = (await db.execute(text(f"""SELECT COUNT(*) total, SUM(u.gender = 1) male, SUM(u.gender = 2) female,
        SUM(u.status = 1) active,
        SUM(a.matchmaker_id IS NULL) unassigned,
        SUM(f.last_follow_at IS NULL) never_followed,
        SUM(f.next_follow_at >= CURDATE() AND f.next_follow_at < DATE_ADD(CURDATE(), INTERVAL 1 DAY)) follow_due_today,
        (SELECT COUNT(DISTINCT vm.user_id) FROM user_membership vm
            WHERE vm.status = 1 AND (vm.end_at IS NULL OR vm.end_at > UTC_TIMESTAMP())
              AND ({vip_scope})) vip
        FROM users u
        LEFT JOIN {_ASSIGNMENT_DISPLAY_DERIVED} a ON a.user_id = u.id
        LEFT JOIN (SELECT user_id, MAX(created_at) last_follow_at, MAX(next_follow_at) next_follow_at FROM member_follow_up GROUP BY user_id) f ON f.user_id = u.id
        WHERE ({scope})"""), params)).mappings().one()
    return MemberStatistics(**{key: int(row[key] or 0) for key in ("total", "male", "female", "vip", "active", "unassigned", "never_followed", "follow_due_today")})


@router.post("/members/batch-status")
async def batch_member_status(
    body: MemberBatchStatus,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    ids = list(dict.fromkeys(body.member_ids))
    placeholders = ",".join(f":id_{index}" for index in range(len(ids)))
    params = {f"id_{index}": value for index, value in enumerate(ids)}
    params.update({"status": body.status, "actor": current.account.id, "reason": body.reason})
    # 批量写操作必须同口径收口，否则可越过单条改状态的越权防线。
    scope = current.scope_exists_clause(params, correlation="scope_assignment.user_id = users.id")
    result = await db.execute(text(f"UPDATE users SET status=:status, updated_at=UTC_TIMESTAMP() WHERE id IN ({placeholders}) AND ({scope})"), params)
    await db.commit()
    return {"updated": int(result.rowcount or 0), "status": body.status}


@router.get("/members/auth")
async def member_auth_list(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    auth_status: int | None = Query(None, ge=0, le=3),
    search: str | None = Query(None, max_length=64),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    where = ["1=1"]
    params = {"limit": page_size, "offset": (page - 1) * page_size}
    if auth_status is not None:
        where.append("COALESCE(ua.auth_status, 0) = :auth_status")
        params["auth_status"] = auth_status
    if search:
        where.append("(u.nickname LIKE CONCAT('%', :search, '%') OR u.phone LIKE CONCAT('%', :search, '%'))")
        params["search"] = search
    # 该接口会下发 real_name / id_card 等高敏字段，必须与会员列表同口径收口。
    where.append(current.scope_exists_clause(params, correlation="scope_assignment.user_id = u.id"))
    clause = " AND ".join(where)
    base = "FROM users u LEFT JOIN user_auth ua ON ua.user_id=u.id"
    rows = await db.execute(text(f"SELECT u.id, u.nickname, u.phone, u.gender, u.birthday, ua.real_name, ua.id_card, COALESCE(ua.auth_status,0) auth_status, ua.updated_at submitted_at {base} WHERE {clause} ORDER BY submitted_at DESC, u.id DESC LIMIT :limit OFFSET :offset"), params)
    total = int((await db.scalar(text(f"SELECT COUNT(*) {base} WHERE {clause}"), {k: v for k, v in params.items() if k not in ('limit', 'offset')})) or 0)
    return {"items": [dict(row) for row in rows.mappings().all()], "page": page, "page_size": page_size, "total": total, "has_more": page * page_size < total}


@router.get("/members/realname-reviews")
async def member_realname_reviews(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    status: int | None = Query(None, ge=0, le=4),
    search: str | None = Query(None, max_length=64),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if status is not None and status not in (1, 4):
        raise HTTPException(422, detail="实名认证状态只支持审核中或人工复核")
    return (await list_realname_reviews(db, page=page, page_size=page_size, status=status, search=search)).model_dump()


@router.patch("/members/{member_id}/realname/review")
async def review_member_realname(
    member_id: int,
    body: RealnameReviewRequest,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return (await review_realname(db, member_id, body.status, body.reason, current.account.id)).model_dump()


@router.get("/members/certification-reviews")
async def member_certification_reviews(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    kind: str | None = Query(None, pattern="^(education|house|marriage)$"),
    status: int | None = Query(None, ge=0, le=3),
    search: str | None = Query(None, max_length=64),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return (await list_certification_reviews(db, page=page, page_size=page_size, viewer=current.account.id, kind=kind, status=status, search=search)).model_dump()


@router.patch("/members/{member_id}/certifications/{kind}/review")
async def review_member_certification(
    member_id: int,
    kind: str = Path(..., pattern="^(education|house|marriage)$"),
    body: CertificationReviewRequest = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return (await review_certification(db, member_id, kind, body)).model_dump()


@router.get("/members/{member_id}", response_model=MemberDetail, summary="查询会员详情")
async def member_detail(member_id: int = Path(..., ge=1), current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> MemberDetail:
    column_rows = await db.execute(text("""SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ('user_profile', 'user_auth')"""))
    available = {(row[0], row[1]) for row in column_rows.all()}
    def profile_expr(name: str) -> str:
        return f"p.{name}" if ("user_profile", name) in available else "NULL"

    def auth_expr(name: str) -> str:
        return f"ua.{name}" if ("user_auth", name) in available else "NULL"
    params: dict[str, object] = {"id": member_id}
    # 越权访问按「不存在」处理（404），避免通过状态码差异枚举他组织会员 ID。
    scope = current.scope_exists_clause(params, correlation="scope_assignment.user_id = u.id")
    row = (await db.execute(text(f"""SELECT u.id, u.nickname, u.phone, u.gender, u.status, u.avatar, u.birthday, u.is_married, u.created_at,
        u.last_login_at, u.register_ip AS ip_location, {profile_expr('residence_city_code')} AS residence_city_code,
        {profile_expr('height')} AS height, {profile_expr('weight')} AS weight,
        {profile_expr('constellation')} AS constellation, {profile_expr('zodiac')} AS zodiac,
        {profile_expr('household')} AS household, {profile_expr('ethnicity')} AS ethnicity,
        {profile_expr('house')} AS house, {profile_expr('car')} AS car, {profile_expr('smoking')} AS smoking,
        {profile_expr('drinking')} AS drinking, {profile_expr('religion')} AS religion,
        {profile_expr('marriage_plan')} AS marriage_plan,
        {profile_expr('hometown_province_code')} AS hometown_province_code,
        {profile_expr('hometown_city_code')} AS hometown_city_code,
        {profile_expr('hometown_district_code')} AS hometown_district_code,
        {profile_expr('residence_province_code')} AS residence_province_code,
        {profile_expr('residence_city_code')} AS residence_city_code,
        {profile_expr('residence_district_code')} AS residence_district_code,
        {profile_expr('household_province_code')} AS household_province_code,
        {profile_expr('household_city_code')} AS household_city_code,
        {profile_expr('household_district_code')} AS household_district_code,
        {profile_expr('income')} AS income, {profile_expr('intention_level')} AS intention_level, {profile_expr('hometown')} AS hometown,
        {profile_expr('residence')} AS residence, {profile_expr('self_intro')} AS self_intro,
        {profile_expr('ideal_partner')} AS ideal_partner, {profile_expr('wechat')} AS wechat, {profile_expr('tags')} AS tags,
        {auth_expr('education')} AS education, {auth_expr('school')} AS school,
        {auth_expr('job')} AS job, {auth_expr('company')} AS company, {auth_expr('auth_status')} AS auth_status,
        COALESCE(pr.match_status, 1) AS match_status,
        v.vip_end_at, a.matchmaker_id,
        CASE WHEN v.user_id IS NULL OR (v.vip_end_at IS NOT NULL AND v.vip_end_at <= UTC_TIMESTAMP()) THEN 0 ELSE 1 END AS is_vip
        FROM users u LEFT JOIN user_profile p ON p.user_id = u.id
        LEFT JOIN user_auth ua ON ua.user_id = u.id
        LEFT JOIN user_privacy pr ON pr.user_id = u.id
        LEFT JOIN (SELECT user_id, MAX(end_at) vip_end_at FROM user_membership WHERE status = 1 GROUP BY user_id) v ON v.user_id = u.id
        LEFT JOIN {_ASSIGNMENT_DISPLAY_DERIVED} a ON a.user_id = u.id WHERE u.id = :id AND ({scope})"""), params)).mappings().first()
    if not row:
        from fastapi import HTTPException
        raise HTTPException(404, detail="会员不存在")
    data = dict(row)
    raw_tags = data.get("tags")
    if isinstance(raw_tags, str):
        try:
            data["tags"] = json.loads(raw_tags)
        except (TypeError, ValueError):
            data["tags"] = None
    # 微信号整列本身就是敏感值，必须按权限分级掩码后再返回；SQL 侧原样取出只是为了把
    # 脱敏口径集中在一处，elevated 档也仅保留首尾若干位。
    data["wechat"] = mask_contact(data.get("wechat"), redaction_level_from_permissions(current.permissions))
    return MemberDetail(**data)


@router.patch("/members/{member_id}/assignment", response_model=MemberAssignmentResponse, summary="修改会员服务红娘")
async def member_assignment(
    member_id: int = Path(..., ge=1),
    body: MemberAssignmentUpdate = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MemberAssignmentResponse:
    guard_params: dict[str, object] = {"id": member_id}
    scope = current.scope_exists_clause(guard_params, correlation="scope_assignment.user_id = u.id")
    if not (await db.execute(text(f"SELECT u.id FROM users u WHERE u.id = :id AND ({scope})"), guard_params)).scalar():
        raise HTTPException(404, detail="会员不存在")
    if body.matchmaker_id is not None and not (await db.execute(text("""SELECT 1 FROM user_role
        WHERE user_id = :id AND role_code = 'service_matchmaker' AND status = 1 LIMIT 1"""), {"id": body.matchmaker_id})).scalar():
        raise HTTPException(422, detail="指定用户不是有效的服务红娘")
    await db.execute(text("""UPDATE resource_assignment SET status = 2,
        ended_at = UTC_TIMESTAMP(), end_reason = 'reassigned'
        WHERE user_id = :user_id AND status = 1"""), {"user_id": member_id})
    if body.matchmaker_id is not None:
        await db.execute(text("""INSERT INTO resource_assignment
            (user_id, organization_id, matchmaker_id, source, assigned_by)
            VALUES (:user_id, :organization_id, :matchmaker_id, 'manual', :assigned_by)"""), {
            "user_id": member_id, "organization_id": current.account.organization_id,
            "matchmaker_id": body.matchmaker_id, "assigned_by": current.account.id,
        })
    await db.execute(text("""INSERT INTO business_audit_log
        (actor_user_id, action, resource_type, resource_id, reason)
        VALUES (:actor, 'member.assignment.update', 'user', :user_id, :reason)"""), {
        "actor": current.account.id, "user_id": member_id,
        "reason": f"matchmaker_id={body.matchmaker_id}",
    })
    await db.commit()
    return MemberAssignmentResponse(user_id=member_id, matchmaker_id=body.matchmaker_id)


@router.patch("/members/{member_id}/status", response_model=MemberStatusResponse, summary="修改会员状态")
async def member_status(member_id: int = Path(..., ge=1), body: MemberStatusUpdate = ..., current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> MemberStatusResponse:
    guard_params: dict[str, object] = {"id": member_id}
    scope = current.scope_exists_clause(guard_params, correlation="scope_assignment.user_id = u.id")
    # 作用域判定与行锁拆成两条语句：MySQL 不允许对派生表/含派生表的查询加锁，
    # EXISTS 判定留在第一条，锁定语句只锁 users 行。
    if not (await db.execute(text(f"SELECT u.id FROM users u WHERE u.id = :id AND ({scope})"), guard_params)).scalar():
        raise HTTPException(404, detail="会员不存在")
    await db.execute(text("SELECT id FROM users WHERE id = :id FOR UPDATE"), {"id": member_id})
    await db.execute(text("UPDATE users SET status = :status, updated_at = UTC_TIMESTAMP() WHERE id = :id"), {"status": body.status, "id": member_id})
    await db.execute(text("INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id, reason) VALUES (:actor, 'member.status.update', 'user', :id, :reason)"), {"actor": current.account.id, "id": member_id, "reason": body.reason})
    await db.commit()
    return MemberStatusResponse(id=member_id, status=body.status, reason=body.reason)


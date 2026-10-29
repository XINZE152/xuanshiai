"""Member CRM endpoints protected by the independent matchmaker admin session."""

import json

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentMatchmakerAdmin, get_current_matchmaker_admin, require_sensitive_operation
from app.core.config import settings
from app.core.sensitive_fields import mask_contact, redaction_level_from_permissions
from app.db.session import get_db
from app.services.sensitive_operations import (
    abort_sensitive_operation,
    begin_sensitive_operation,
    finish_sensitive_operation,
)
from app.schemas.matchmaker_crm_admin import MatchRecordCreate, MatchRecordItem, MatchRecordPage, MatchRecordResponse, MatchRecordStatistics, MatchRecordUpdate, MemberAssignmentResponse, MemberAssignmentUpdate, MemberDetail, MemberListItem, MemberPage, MemberStatistics, MemberStatusResponse, MemberStatusUpdate, MemberWechatBindRequest, SourceDistributionItem
from app.schemas.admin import CertificationReviewRequest, RealnameReviewRequest
from app.services.auth import list_realname_reviews, review_realname
from app.services.certifications import list_certification_reviews, review_certification
from app.services.member_crm_filter import (
    MEMBER_BALANCE_EXPR,
    MEMBER_POINTS_EXPR,
    build_member_filter,
    member_sort_sql,
)

router = APIRouter(prefix="/admin/matchmaker")

# 会员归属展示列：每个会员只取一条生效归属，避免 `LEFT JOIN resource_assignment`
# 因一人多单而放大结果行数（历史实现使用非聚合子查询，会在列表分页中产生重复行）。
# 同时带出门店与最近分派时间，供「按门店筛选」和「按分派时间排序/筛选」使用。
# 数据可见范围不使用该派生表，而统一走 `CurrentMatchmakerAdmin.scope_exists_clause`，
# 以保证「任一生效归属落在作用域内即可见」的正确语义。
_ASSIGNMENT_DISPLAY_DERIVED = (
    "(SELECT user_id, MAX(matchmaker_id) AS matchmaker_id, MAX(organization_id) AS organization_id, "
    "MAX(effective_at) AS assigned_at FROM resource_assignment WHERE status = 1 GROUP BY user_id)"
)


class MemberBatchStatus(BaseModel):
    member_ids: list[int] = Field(min_length=1, max_length=200)
    status: int = Field(ge=1, le=3)
    reason: str = Field(min_length=1, max_length=255)


class MemberBatchDeleteRequest(BaseModel):
    member_ids: list[int] = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=255)


class MemberDeleteFailure(BaseModel):
    id: int
    error: str


class MemberDeleteResult(BaseModel):
    deleted: list[int] = Field(default_factory=list)
    failed: list[MemberDeleteFailure] = Field(default_factory=list)


class RegionResolverStatus(BaseModel):
    provider: str
    refresh_enabled: bool
    adapter_implemented: bool


class RegionRefreshResult(BaseModel):
    kind: str
    provider: str
    dry_run: bool
    scanned_count: int
    updated_count: int
    adapter_implemented: bool


class MemberWechatBindingResult(BaseModel):
    member_id: int
    wechat_bound: bool


# 牵线记录列表与统计共用的 FROM 子句：ra 既要供 SELECT 展示归属红娘，
# 也要供 WHERE 按红娘筛选，COUNT 查询复用同一 base，否则按红娘筛选时计数缺 join。
_MATCH_RECORD_BASE = (
    "FROM match_apply a JOIN users fu ON fu.id = a.from_user_id JOIN users tu ON tu.id = a.to_user_id"
    " LEFT JOIN (SELECT user_id, MAX(matchmaker_id) AS matchmaker_id FROM resource_assignment"
    " WHERE status = 1 GROUP BY user_id) ra ON ra.user_id = a.from_user_id"
)

# 列表专用：在共用 base 上再补 1:1 LEFT JOIN（双方实名认证、归属红娘昵称）。
# 均为 1:1，不放大行数；统计/COUNT 仍走不带这些 JOIN 的 base，保持口径与性能。
_MATCH_RECORD_LIST_FROM = (
    _MATCH_RECORD_BASE
    + " LEFT JOIN user_auth uaf ON uaf.user_id = a.from_user_id"
    " LEFT JOIN user_auth uat ON uat.user_id = a.to_user_id"
    " LEFT JOIN users mm ON mm.id = ra.matchmaker_id"
    " LEFT JOIN user_profile fp ON fp.user_id = a.from_user_id"
    " LEFT JOIN user_profile tp ON tp.user_id = a.to_user_id"
)

# 顶部统计卡的筛选口径。status: 0 待处理 / 1 成功 / 2 失败 / 3 已关闭。
# 「待牵线」与「牵线中」按是否已归属服务红娘拆分。
_STATUS_GROUP_SQL: dict[str, str] = {
    "pending": "a.status = 0 AND ra.matchmaker_id IS NULL",
    "matching": "a.status = 0 AND ra.matchmaker_id IS NOT NULL",
    "success": "a.status = 1",
    "fail": "a.status = 2",
    "closed": "a.status = 3",
}


@router.get("/match-records", response_model=MatchRecordPage, summary="管理员查询牵线记录")
async def match_records(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, max_length=64),
    date_from: str | None = Query(None, max_length=10, description="牵线时间起 YYYY-MM-DD（含）"),
    date_to: str | None = Query(None, max_length=10, description="牵线时间止 YYYY-MM-DD（含）"),
    matchmaker_id: int | None = Query(None, ge=1, description="按发起方会员归属的服务红娘筛选"),
    status_group: str | None = Query(
        None,
        pattern="^(pending|matching|success|fail|closed)$",
        description="顶部统计卡筛选：待牵线=待处理且未分派红娘 / 牵线中=待处理且已分派红娘 / 成功 / 失败 / 已关闭",
    ),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MatchRecordPage:
    where = ["1=1"]
    params: dict[str, object] = {"limit": page_size, "offset": (page - 1) * page_size}
    if search:
        where.append("(fu.nickname LIKE CONCAT('%', :search, '%') OR tu.nickname LIKE CONCAT('%', :search, '%') OR fu.phone LIKE CONCAT('%', :search, '%') OR tu.phone LIKE CONCAT('%', :search, '%'))")
        params["search"] = search
    if date_from:
        where.append("a.created_at >= :date_from")
        params["date_from"] = date_from
    if date_to:
        # 结束日按「含当日」处理，补到次日 00:00 前
        where.append("a.created_at < DATE_ADD(:date_to, INTERVAL 1 DAY)")
        params["date_to"] = date_to
    if matchmaker_id is not None:
        # 归属红娘取自 resource_assignment 的生效记录（派生表 ra，见下方 base）
        where.append("ra.matchmaker_id = :matchmaker_id")
        params["matchmaker_id"] = matchmaker_id
    if status_group:
        where.append(_STATUS_GROUP_SQL[status_group])
    # 牵线记录会把双方昵称与手机号一并下发，按「发起方（from）会员归属」收口。
    where.append(current.scope_exists_clause(params, correlation="scope_assignment.user_id = a.from_user_id"))
    clause = " AND ".join(where)
    # 列表在共用 base 上补 1:1 LEFT JOIN（双方实名认证 uaf/uat、归属红娘昵称 mm），
    # 派生列全部真值、无新建表；旧库缺关联时 LEFT JOIN 自然降级为 NULL，不拖垮整页。
    # COUNT/统计仍走不带这些 JOIN 的 base，保持口径与性能。
    rows = await db.execute(text(f"""SELECT a.id, a.from_user_id, a.to_user_id, a.status, a.created_at, a.responded_at,
        fu.nickname AS from_nickname, tu.nickname AS to_nickname,
        ra.matchmaker_id,
        fu.gender AS from_gender, tu.gender AS to_gender,
        TIMESTAMPDIFF(YEAR, fu.birthday, UTC_DATE()) AS from_age,
        TIMESTAMPDIFF(YEAR, tu.birthday, UTC_DATE()) AS to_age,
        COALESCE(uaf.realname_status = 2, 0) AS from_is_verified, COALESCE(uat.realname_status = 2, 0) AS to_is_verified,
        COALESCE(fu.is_single_pledge = 1, 0) AS from_has_promise, COALESCE(tu.is_single_pledge = 1, 0) AS to_has_promise,
        mm.nickname AS matchmaker_name,
        COALESCE(fu.avatar, JSON_UNQUOTE(JSON_EXTRACT(fp.photos, '$[0]'))) AS from_avatar,
        COALESCE(tu.avatar, JSON_UNQUOTE(JSON_EXTRACT(tp.photos, '$[0]'))) AS to_avatar,
        CONCAT('G', LPAD(fu.id, 6, '0')) AS from_member_code,
        CONCAT('G', LPAD(tu.id, 6, '0')) AS to_member_code
        {_MATCH_RECORD_LIST_FROM} WHERE {clause} ORDER BY a.created_at DESC, a.id DESC LIMIT :limit OFFSET :offset"""), params)
    total = int((await db.execute(text(f"SELECT COUNT(*) {_MATCH_RECORD_BASE} WHERE {clause}"), {k: v for k, v in params.items() if k not in ("limit", "offset")})).scalar() or 0)
    return MatchRecordPage(items=[MatchRecordItem(**dict(row)) for row in rows.mappings().all()], page=page, page_size=page_size, total=total, has_more=page * page_size < total)


@router.get("/match-records/statistics", response_model=MatchRecordStatistics, summary="牵线记录顶部统计卡")
async def match_record_statistics(
    search: str | None = Query(None, max_length=64),
    date_from: str | None = Query(None, max_length=10),
    date_to: str | None = Query(None, max_length=10),
    matchmaker_id: int | None = Query(None, ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MatchRecordStatistics:
    """统计卡与列表共用筛选条件，点击任一卡片即以 status_group 重新拉列表。"""
    where = ["1=1"]
    params: dict[str, object] = {}
    if search:
        where.append("(fu.nickname LIKE CONCAT('%', :search, '%') OR tu.nickname LIKE CONCAT('%', :search, '%') OR fu.phone LIKE CONCAT('%', :search, '%') OR tu.phone LIKE CONCAT('%', :search, '%'))")
        params["search"] = search
    if date_from:
        where.append("a.created_at >= :date_from")
        params["date_from"] = date_from
    if date_to:
        where.append("a.created_at < DATE_ADD(:date_to, INTERVAL 1 DAY)")
        params["date_to"] = date_to
    if matchmaker_id is not None:
        where.append("ra.matchmaker_id = :matchmaker_id")
        params["matchmaker_id"] = matchmaker_id
    where.append(current.scope_exists_clause(params, correlation="scope_assignment.user_id = a.from_user_id"))
    clause = " AND ".join(where)
    row = (await db.execute(text(f"""SELECT
        SUM(a.status = 0 AND ra.matchmaker_id IS NULL) AS pending,
        SUM(a.status = 0 AND ra.matchmaker_id IS NOT NULL) AS matching,
        SUM(a.status = 1) AS success,
        SUM(a.status = 2) AS fail,
        SUM(a.status = 3) AS closed,
        COUNT(*) AS total
        {_MATCH_RECORD_BASE} WHERE {clause}"""), params)).mappings().one()
    return MatchRecordStatistics(
        total=int(row["total"] or 0),
        pending=int(row["pending"] or 0),
        matching=int(row["matching"] or 0),
        success=int(row["success"] or 0),
        fail=int(row["fail"] or 0),
        closed=int(row["closed"] or 0),
    )


@router.patch("/match-records/{record_id}", response_model=MatchRecordItem, summary="更新牵线记录状态")
async def update_match_record(
    record_id: int = Path(..., ge=1),
    body: MatchRecordUpdate = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MatchRecordItem:
    """结束牵线 / 标记成功失败关闭。成功时同步互相关注，失败时按须知退回牵线次数。"""
    params: dict[str, object] = {"id": record_id}
    scope = current.scope_exists_clause(params, correlation="scope_assignment.user_id = a.from_user_id")
    row = (await db.execute(text(
        f"SELECT a.id, a.from_user_id, a.to_user_id, a.status FROM match_apply a WHERE a.id = :id AND ({scope}) FOR UPDATE"
    ), params)).mappings().first()
    if not row:
        raise HTTPException(404, detail="牵线记录不存在")
    if int(row["status"]) != 0 and body.status != 3:
        raise HTTPException(409, detail="该牵线记录已结束，不可再次变更状态")
    await db.execute(text("""UPDATE match_apply
        SET status = :status,
            responded_at = CASE WHEN :status = 0 THEN responded_at ELSE COALESCE(responded_at, UTC_TIMESTAMP()) END,
            updated_at = UTC_TIMESTAMP()
        WHERE id = :id"""), {"status": body.status, "id": record_id})
    if body.status == 1:
        await db.execute(text("""INSERT INTO user_match (user_id, target_user_id, status)
            VALUES (:left, :right, 1), (:right, :left, 1)
            ON DUPLICATE KEY UPDATE status = 1, updated_at = UTC_TIMESTAMP()"""),
            {"left": int(row["from_user_id"]), "right": int(row["to_user_id"])})
    await db.execute(text("""INSERT INTO business_audit_log
        (actor_user_id, action, resource_type, resource_id, reason)
        VALUES (:actor, 'match_record.status', 'match_apply', :id, :reason)"""),
        {"actor": current.account.id, "id": record_id, "reason": body.reason})
    await db.commit()
    updated = (await db.execute(text(f"""SELECT a.id, a.from_user_id, a.to_user_id, a.status, a.created_at, a.responded_at,
        fu.nickname AS from_nickname, tu.nickname AS to_nickname, ra.matchmaker_id
        {_MATCH_RECORD_BASE} WHERE a.id = :id"""), {"id": record_id})).mappings().first()
    return MatchRecordItem(**dict(updated))


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


@router.delete(
    "/match-records/{record_id}",
    response_model=MemberDeleteResult,
    summary="删除单条牵线记录（物理删除，幂等 + 二次确认）",
)
async def match_record_delete(
    record_id: int = Path(..., ge=1),
    reason: str = Query("后台删除", min_length=1, max_length=255, description="删除理由（记入审计）"),
    idempotency_key: str = Depends(require_sensitive_operation),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MemberDeleteResult:
    """对标站牵线记录行「删除」按钮。

    match_apply 无 deleted_at 列，故为物理删除（仅删该牵线行，不动 user_match 关注关系，
    与 PATCH 标记失败/关闭互不干涉）。敏感写范式：Idempotency-Key 头 + confirm=true 查询参 + 作用域收口。
    """
    current.require("matchmaker.member.manage")
    reservation, replayed = await begin_sensitive_operation(
        db,
        actor_id=current.account.id,
        operation="matchmaker.match_record.delete",
        idempotency_key=idempotency_key,
        payload={"record_id": record_id, "reason": reason},
    )
    if replayed is not None:
        return MemberDeleteResult(**replayed)
    params: dict[str, object] = {"record_id": record_id}
    scope = current.scope_exists_clause(params, correlation="scope_assignment.user_id = a.from_user_id")
    try:
        row = (await db.execute(text(
            f"SELECT a.id FROM match_apply a WHERE a.id = :record_id AND ({scope}) FOR UPDATE"
        ), params)).mappings().first()
        if row is None:
            await abort_sensitive_operation(db, reservation)
            raise HTTPException(404, detail="牵线记录不存在或无权限")
        await db.execute(text("DELETE FROM match_apply WHERE id = :record_id"), {"record_id": record_id})
        await db.execute(text(
            "INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id, reason)"
            " VALUES (:actor, 'match_record.delete', 'match_apply', :id, :reason)"),
            {"actor": current.account.id, "id": record_id, "reason": reason[:255]},
        )
        await db.commit()
    except HTTPException:
        raise
    except Exception:
        await abort_sensitive_operation(db, reservation)
        raise
    result = MemberDeleteResult(deleted=[record_id])
    await finish_sensitive_operation(db, reservation, result.model_dump(mode="json"))
    return result


def _normalize_tags(raw: object) -> object:
    """把 tags 列归一化为 Python 结构。

    MySQL JSON 列经 asyncmy 驱动取回时可能是已解析对象，也可能是 JSON 字符串；
    历史脏数据（非列表值、非法 JSON）统一降级为 None，避免 500。
    """
    if raw is None or isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None
    return None


async def _available_columns(db: AsyncSession) -> frozenset[tuple[str, str]]:
    """读取会员列表会引用的列是否存在，供缺列降级使用。

    迁移（migrations/filters/20261011_01）未执行时，新筛选项与展示列必须降级为
    NULL 而不是让整页 SQL 报 Unknown column。
    """
    column_rows = await db.execute(text("""SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ('user_profile', 'user_auth', 'users')"""))
    return frozenset((row[0], row[1]) for row in column_rows.all())


async def _member_query(
    db: AsyncSession,
    where: str,
    params: dict,
    page: int,
    page_size: int,
    scope: str,
    sort_sql: str | None = None,
    available: frozenset[tuple[str, str]] | None = None,
) -> MemberPage:
    """会员 CRM 列表查询。

    ``scope`` 必须由调用方通过 `CurrentMatchmakerAdmin.scope_exists_clause`
    生成——本函数不接受手写作用域谓词，也不允许为空（fail-closed）。

    ``available`` 由调用方复用已取到的列集合，避免同一次请求重复查 information_schema。
    """
    if available is None:
        available = await _available_columns(db)

    def auth_expr(name: str) -> str:
        return f"ua.{name}" if ("user_auth", name) in available else "NULL"

    def profile_expr(name: str) -> str:
        return f"p.{name}" if ("user_profile", name) in available else "NULL"

    def user_expr(name: str) -> str:
        return f"u.{name}" if ("users", name) in available else "NULL"

    base = f"""FROM users u LEFT JOIN user_profile p ON p.user_id = u.id
        LEFT JOIN user_auth ua ON ua.user_id = u.id
        LEFT JOIN user_privacy pr ON pr.user_id = u.id
        LEFT JOIN (SELECT user_id, MAX(end_at) AS vip_end_at FROM user_membership WHERE status = 1 GROUP BY user_id) v ON v.user_id = u.id
        LEFT JOIN {_ASSIGNMENT_DISPLAY_DERIVED} a ON a.user_id = u.id
        LEFT JOIN (SELECT user_id, MAX(created_at) last_follow_at, MAX(next_follow_at) next_follow_at FROM member_follow_up GROUP BY user_id) f ON f.user_id = u.id"""
    # 软删除口径（A12-会员删除）：users.deleted_at 列已存在（属账号生命周期，与
    # user_candidates 同口径），列表默认排除已软删行；列缺失（未跑 setup）时不拼，避免 Unknown column。
    if ("users", "deleted_at") in available:
        where = f"({where}) AND u.deleted_at IS NULL"
    where = f"({where}) AND ({scope})"
    params = {**params, "limit": page_size, "offset": (page - 1) * page_size}
    # Keep sort fields server-side whitelisted; never interpolate user input.
    order_by = sort_sql or "u.created_at DESC, u.id DESC"
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
        {user_expr('source')} AS source, {user_expr('region')} AS region,
        COALESCE({user_expr('cert_status')}, 0) AS cert_status,
        COALESCE({user_expr('wechat_added')}, 0) AS wechat_added,
        COALESCE({user_expr('promoted')}, 0) AS promoted,
        COALESCE({user_expr('newcomer')}, 0) AS newcomer,
        COALESCE({user_expr('activity_signup')}, 0) AS activity_signup,
        {user_expr('level')} AS level, {user_expr('fetched_at')} AS fetched_at,
        a.assigned_at,
        {auth_expr('real_name')} AS real_name,
        {profile_expr('weight')} AS weight,
        {profile_expr('tags')} AS tags,
        {user_expr('last_login_at')} AS last_login_at,
        {user_expr('register_ip')} AS ip_location,
        {user_expr('ip_region')} AS ip_region,
        {user_expr('phone_region')} AS phone_region,
        {user_expr('login_num')} AS login_num,
        {MEMBER_POINTS_EXPR} AS points,
        {MEMBER_BALANCE_EXPR} AS balance,
        EXISTS (SELECT 1 FROM member_abandonment ma
            WHERE ma.user_id = u.id AND ma.restored_at IS NULL) AS abandoned,
        CASE WHEN v.user_id IS NULL OR (v.vip_end_at IS NOT NULL AND v.vip_end_at <= UTC_TIMESTAMP()) THEN 0 ELSE 1 END AS is_vip
        {base} WHERE {where} ORDER BY {order_by} LIMIT :limit OFFSET :offset"""), params)
    count = await db.execute(text(f"SELECT COUNT(*) {base} WHERE {where}"), {k: v for k, v in params.items() if k not in ("limit", "offset")})
    total = int(count.scalar() or 0)
    raw_rows = [dict(row) for row in rows.mappings().all()]
    for item in raw_rows:
        item["tags"] = _normalize_tags(item.get("tags"))
    items = [MemberListItem(**item) for item in raw_rows]
    return MemberPage(items=items, page=page, page_size=page_size, total=total, has_more=page * page_size < total)


@router.get("/members", response_model=MemberPage, summary="查询会员 CRM 列表")
async def members(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    gender: int | None = Query(None, ge=1, le=2),
    status: int | None = Query(None, ge=1, le=3),
    vip: bool | None = Query(None),
    auth_status: str | None = Query(
        None,
        pattern=r"^[0-4](,[0-4]){0,4}$",
        description="资料审核状态，支持英文逗号分隔多值：0未提交 1审核中 2已通过 3未通过 4已到店核验；如 1,2,3,4 为已提交资料（B6）",
    ),
    assigned: bool | None = Query(None),
    follow_state: str | None = Query(None, pattern="^(never|due_today|overdue)$"),
    search: str | None = Query(None, max_length=64),
    search_type: str | None = Query(None, pattern="^(smart|phone|wechat|code|id|nickname|realname)$"),
    sort_by: str = Query("created_at", pattern="^(created_at|entered_at|last_login_at|last_follow_at|next_follow_at|assigned_at|fetched_at|login_num|balance|points|integral|id)$"),
    order: str | None = Query(None, pattern="^(latest|earliest|smart)$", description="记录排序；与 sort_by 互斥，同时传时以 order 为准"),
    source: str | None = Query(None, max_length=32),
    source__is_null: bool | None = Query(
        None, description="来源空值筛选：true 仅未填写来源 / false 仅已填写来源（B5）"
    ),
    activity_id: int | None = Query(
        None, ge=1, description="按具体活动报名筛选：存在该活动的报名记录（B0）"
    ),
    region: str | None = Query(None, pattern="^(mainland|hmt|overseas)$"),
    xiangqin_status: int | None = Query(None, ge=1, le=5, description="相亲状态 1公开相亲 2委托红娘 3完全私密 4停止相亲 5已经脱单"),
    realname: bool | None = Query(None),
    cert_status: int | None = Query(None, ge=0, le=3, description="身份认证 0未认证 1已认证 2待审核 3认证失败"),
    organization_id: int | None = Query(None, ge=1),
    matchmaker_id: int | None = Query(None, ge=1),
    promoter_id: int | None = Query(None, ge=1),
    age_min: int | None = Query(None, ge=16, le=100),
    age_max: int | None = Query(None, ge=16, le=100),
    height_min: int | None = Query(None, ge=80, le=250),
    height_max: int | None = Query(None, ge=80, le=250),
    weight_min: int | None = Query(None, ge=20, le=300, description="体重下限（kg，含），按 user_profile.weight 区间过滤（B9）"),
    weight_max: int | None = Query(None, ge=20, le=300, description="体重上限（kg，含），按 user_profile.weight 区间过滤（B9）"),
    next_follow_start: str | None = Query(None, max_length=10, description="预约下次跟进时间起（含，YYYY-MM-DD）；供预约跟进日历（A15）"),
    next_follow_end: str | None = Query(None, max_length=10, description="预约下次跟进时间止（含当日，YYYY-MM-DD）；供预约跟进日历（A15）"),
    marriage: str | None = Query(None, max_length=64, description="婚况多选，英文逗号分隔；取值同 users.is_married 编码（0未知 1未婚 2离异 3丧偶）"),
    education: str | None = Query(None, max_length=255, description="学历多选，英文逗号分隔"),
    occupation: str | None = Query(None, max_length=255, description="职业多选，英文逗号分隔"),
    income: str | None = Query(None, max_length=255, description="收入多选，英文逗号分隔，按 user_profile.income 原值精确匹配"),
    income_min: float | None = Query(None, ge=0, description="月收入下限（元，含）"),
    income_max: float | None = Query(None, ge=0, description="月收入上限（元，含）"),
    intention_level: int | None = Query(None, ge=1, le=3, description="客户意向 1低意向 2中意向 3高意向"),
    house: str | None = Query(None, max_length=32, description="购房情况多选，英文逗号分隔，按 user_profile.house 原值精确匹配"),
    ethnicity: str | None = Query(None, max_length=32, description="民族多选，英文逗号分隔，按 user_profile.ethnicity 原值精确匹配"),
    constellation: str | None = Query(None, max_length=16, description="星座，按 user_profile.constellation 原值精确匹配"),
    zodiac: str | None = Query(None, max_length=16, description="属相，按 user_profile.zodiac 原值精确匹配"),
    residence: str | None = Query(None, max_length=64),
    hometown: str | None = Query(None, max_length=64),
    hukou: str | None = Query(None, max_length=64),
    religion: str | None = Query(None, max_length=32),
    car: str | None = Query(None, max_length=32, description="购车情况多选，英文逗号分隔，按 user_profile.car 原值精确匹配"),
    level: str | None = Query(None, max_length=32, description="会员级别多选，英文逗号分隔，按 users.level 原值精确匹配"),
    tags: str | None = Query(None, max_length=255, description="标签多选，英文逗号分隔，任一命中"),
    offline_vip: bool | None = Query(None),
    abandoned: bool | None = Query(None, description="弃海状态 true仅弃海会员 / false排除弃海会员"),
    promoted: bool | None = Query(None),
    newcomer: bool | None = Query(None),
    activity_signup: bool | None = Query(None),
    wechat_added: int | None = Query(None, ge=0, le=2, description="加微信结果 0未添加 1已加上 2没加上"),
    behavior_type: str | None = Query(None, pattern="^(viewed|viewed_by|favorited|boosted|boosted_by|gift_sent|gift_received)$"),
    created_start: str | None = Query(None, max_length=10, description="建档时间起（含，兼容前端空字符串）"),
    created_end: str | None = Query(None, max_length=10, description="建档时间止（含，兼容前端空字符串）"),
    assigned_start: str | None = Query(None, max_length=10, description="分派时间起（含，兼容前端空字符串）"),
    assigned_end: str | None = Query(None, max_length=10, description="分派时间止（含，兼容前端空字符串）"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MemberPage:
    values = {
        "gender": gender, "status": status, "vip": vip, "auth_status": auth_status,
        "assigned": assigned, "follow_state": follow_state, "search": search,
        "search_type": search_type, "source": source, "source__is_null": source__is_null,
        "activity_id": activity_id, "region": region,
        "xiangqin_status": xiangqin_status, "realname": realname, "cert_status": cert_status,
        "organization_id": organization_id, "matchmaker_id": matchmaker_id,
        "promoter_id": promoter_id, "age_min": age_min, "age_max": age_max,
        "height_min": height_min, "height_max": height_max,
        "weight_min": weight_min, "weight_max": weight_max,
        "next_follow_start": next_follow_start, "next_follow_end": next_follow_end,
        "marriage": marriage,
        "education": education, "occupation": occupation, "income": income,
        "income_min": income_min, "income_max": income_max,
        "intention_level": intention_level, "house": house, "ethnicity": ethnicity,
        "constellation": constellation, "zodiac": zodiac,
        "residence": residence, "hometown": hometown, "hukou": hukou,
        "religion": religion, "car": car, "level": level, "tags": tags,
        "offline_vip": offline_vip, "promoted": promoted, "newcomer": newcomer,
        "abandoned": abandoned,
        "activity_signup": activity_signup, "wechat_added": wechat_added,
        "behavior_type": behavior_type, "created_start": created_start,
        "created_end": created_end, "assigned_start": assigned_start,
        "assigned_end": assigned_end,
    }
    available = await _available_columns(db)
    where, params = build_member_filter(values, available)
    scope = current.scope_exists_clause(params, correlation="scope_assignment.user_id = u.id")
    return await _member_query(
        db, where, params, page, page_size, scope, member_sort_sql(sort_by, order, available), available=available
    )


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
    # 弃海计数同样必须收口到账号数据范围，否则门店/组织账号能看到全局弃海规模。
    abandoned_scope = current.scope_exists_clause(
        params,
        correlation="abandoned_scope_assignment.user_id = au.id",
        assignment_alias="abandoned_scope_assignment",
    )
    row = (await db.execute(text(f"""SELECT COUNT(*) total, SUM(u.gender = 1) male, SUM(u.gender = 2) female,
        SUM(u.status = 1) active,
        SUM(a.matchmaker_id IS NULL) unassigned,
        SUM(f.last_follow_at IS NULL) never_followed,
        SUM(f.next_follow_at >= CURDATE() AND f.next_follow_at < DATE_ADD(CURDATE(), INTERVAL 1 DAY)) follow_due_today,
        (SELECT COUNT(DISTINCT ma.user_id) FROM member_abandonment ma
            WHERE ma.restored_at IS NULL
              AND EXISTS (SELECT 1 FROM users au WHERE au.id = ma.user_id AND ({abandoned_scope}))) abandoned,
        (SELECT COUNT(DISTINCT vm.user_id) FROM user_membership vm
            WHERE vm.status = 1 AND (vm.end_at IS NULL OR vm.end_at > UTC_TIMESTAMP())
              AND ({vip_scope})) vip
        FROM users u
        LEFT JOIN {_ASSIGNMENT_DISPLAY_DERIVED} a ON a.user_id = u.id
        LEFT JOIN (SELECT user_id, MAX(created_at) last_follow_at, MAX(next_follow_at) next_follow_at FROM member_follow_up GROUP BY user_id) f ON f.user_id = u.id
        WHERE ({scope})"""), params)).mappings().one()
    # B5/A11：来源分布（含未填写一档）。users.source 列缺失时降级为空列表，
    # 不影响其余统计字段；前端可回退到“会员总数－已归类”兑底。
    source_distribution: list[SourceDistributionItem] = []
    if ("users", "source") in await _available_columns(db):
        dist_rows = (await db.execute(text(f"""SELECT COALESCE(NULLIF(u.source, ''), '') AS value, COUNT(*) count
            FROM users u
            LEFT JOIN {_ASSIGNMENT_DISPLAY_DERIVED} scope_dummy ON scope_dummy.user_id = u.id
            WHERE ({scope}) GROUP BY value ORDER BY count DESC, value"""), params)).mappings().all()
        source_distribution = [
            SourceDistributionItem(value=str(item["value"]), label=str(item["value"]) or "未填写来源", count=int(item["count"] or 0))
            for item in dist_rows
        ]
    return MemberStatistics(**{key: int(row[key] or 0) for key in ("total", "male", "female", "vip", "active", "unassigned", "never_followed", "follow_due_today", "abandoned")}, source_distribution=source_distribution)


@router.post("/members/batch-status")
async def batch_member_status(
    body: MemberBatchStatus,
    idempotency_key: str = Depends(require_sensitive_operation),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    ids = list(dict.fromkeys(body.member_ids))
    # D-9：批量写操作必须幂等（同键重放不重复改状态）。
    reservation, replayed = await begin_sensitive_operation(
        db,
        actor_id=current.account.id,
        operation="matchmaker.member.batch_status",
        idempotency_key=idempotency_key,
        payload={"body": body.model_dump(mode="json")},
    )
    if replayed is not None:
        return replayed
    placeholders = ",".join(f":id_{index}" for index in range(len(ids)))
    params = {f"id_{index}": value for index, value in enumerate(ids)}
    params.update({"status": body.status, "actor": current.account.id, "reason": body.reason})
    # 批量写操作必须同口径收口，否则可越过单条改状态的越权防线。
    scope = current.scope_exists_clause(params, correlation="scope_assignment.user_id = users.id")
    try:
        eligible_rows = await db.execute(text(f"SELECT id FROM users WHERE id IN ({placeholders}) AND ({scope}) FOR UPDATE"), params)
        updated_ids = [int(row[0]) for row in eligible_rows.all()]
        await db.execute(text(f"UPDATE users SET status=:status, updated_at=UTC_TIMESTAMP() WHERE id IN ({placeholders}) AND ({scope})"), params)
        await db.commit()
    except Exception:
        await abort_sensitive_operation(db, reservation)
        raise
    updated_set = set(updated_ids)
    response = {
        "updated": len(updated_set),
        "updated_ids": sorted(updated_set),
        "rejected_ids": [member_id for member_id in ids if member_id not in updated_set],
        "status": body.status,
    }
    await finish_sensitive_operation(db, reservation, response)
    return response


@router.post("/members/batch-delete", response_model=MemberDeleteResult, summary="批量删除会员（软删除，幂等 + 二次确认）")
async def batch_member_delete(
    body: MemberBatchDeleteRequest,
    idempotency_key: str = Depends(require_sensitive_operation),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MemberDeleteResult:
    """A12-会员删除：软删 `users.deleted_at`（复用账号生命周期列，不物理删、不新建列）。

    敏感写：confirm=true + Idempotency-Key；作用域收口（D-16），越权/不存在/已删除逐条归入 failed。
    """
    current.require("matchmaker.member.manage")
    if ("users", "deleted_at") not in await _available_columns(db):
        raise HTTPException(503, detail="会员软删除列未就绪：请执行账号生命周期迁移")
    ids = list(dict.fromkeys(body.member_ids))
    reservation, replayed = await begin_sensitive_operation(
        db,
        actor_id=current.account.id,
        operation="matchmaker.member.batch_delete",
        idempotency_key=idempotency_key,
        payload={"body": body.model_dump(mode="json")},
    )
    if replayed is not None:
        return MemberDeleteResult(**replayed)
    placeholders = ",".join(f":id_{index}" for index in range(len(ids)))
    params: dict[str, object] = {f"id_{index}": value for index, value in enumerate(ids)}
    scope = current.scope_exists_clause(params, correlation="scope_assignment.user_id = users.id")
    try:
        locked = (await db.execute(text(
            f"SELECT users.id AS id, users.deleted_at AS deleted_at FROM users"
            f" WHERE users.id IN ({placeholders}) AND ({scope}) FOR UPDATE"), params)).mappings().all()
        state = {int(r["id"]): r["deleted_at"] for r in locked}
        succeeded: list[int] = []
        failed: list[MemberDeleteFailure] = []
        for member_id in ids:
            if member_id not in state:
                failed.append(MemberDeleteFailure(id=member_id, error="会员不存在或无权限"))
                continue
            if state[member_id] is not None:
                failed.append(MemberDeleteFailure(id=member_id, error="该会员已删除"))
                continue
            await db.execute(text(
                "UPDATE users SET deleted_at=UTC_TIMESTAMP(), updated_at=UTC_TIMESTAMP()"
                " WHERE id=:mid AND deleted_at IS NULL"), {"mid": member_id})
            succeeded.append(member_id)
        if succeeded:
            await db.execute(text(
                "INSERT INTO business_audit_log"
                " (actor_user_id, action, resource_type, resource_id, after_json, reason)"
                " VALUES (:actor, 'member.batch_delete', 'users', NULL, CAST(:after AS JSON), :reason)"),
                {"actor": current.account.id,
                 "after": json.dumps({"member_ids": succeeded}),
                 "reason": f"批量删除{len(succeeded)}人: {body.reason}"[:255]},
            )
        await db.commit()
    except Exception:
        await abort_sensitive_operation(db, reservation)
        raise
    result = MemberDeleteResult(deleted=succeeded, failed=failed)
    await finish_sensitive_operation(db, reservation, result.model_dump(mode="json"))
    return result


@router.delete("/members/{member_id}", response_model=MemberDeleteResult, summary="删除单个会员（软删除，幂等 + 二次确认）")
async def member_delete(
    member_id: int = Path(..., ge=1),
    reason: str = Query("后台删除", min_length=1, max_length=255, description="删除理由（记入审计）"),
    idempotency_key: str = Depends(require_sensitive_operation),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MemberDeleteResult:
    """A12-会员删除：按 user_id 软删单个平台注册账号（非后台账号，与 `DELETE /accounts/{id}` 不同域）。"""
    current.require("matchmaker.member.manage")
    if ("users", "deleted_at") not in await _available_columns(db):
        raise HTTPException(503, detail="会员软删除列未就绪：请执行账号生命周期迁移")
    reservation, replayed = await begin_sensitive_operation(
        db,
        actor_id=current.account.id,
        operation="matchmaker.member.delete",
        idempotency_key=idempotency_key,
        payload={"member_id": member_id, "reason": reason},
    )
    if replayed is not None:
        return MemberDeleteResult(**replayed)
    params: dict[str, object] = {"member_id": member_id}
    scope = current.scope_exists_clause(params, correlation="scope_assignment.user_id = users.id")
    try:
        row = (await db.execute(text(
            "SELECT users.deleted_at AS deleted_at FROM users WHERE users.id=:member_id"
            f" AND ({scope}) FOR UPDATE"), params)).mappings().first()
        if row is None:
            await abort_sensitive_operation(db, reservation)
            raise HTTPException(404, detail="会员不存在或无权限")
        if row["deleted_at"] is not None:
            await abort_sensitive_operation(db, reservation)
            raise HTTPException(409, detail="该会员已删除")
        await db.execute(text(
            "UPDATE users SET deleted_at=UTC_TIMESTAMP(), updated_at=UTC_TIMESTAMP()"
            " WHERE id=:member_id AND deleted_at IS NULL"), {"member_id": member_id})
        await db.execute(text(
            "INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id, reason)"
            " VALUES (:actor, 'member.delete', 'users', :id, :reason)"),
            {"actor": current.account.id, "id": member_id, "reason": reason[:255]},
        )
        await db.commit()
    except HTTPException:
        raise
    except Exception:
        await abort_sensitive_operation(db, reservation)
        raise
    result = MemberDeleteResult(deleted=[member_id])
    await finish_sensitive_operation(db, reservation, result.model_dump(mode="json"))
    return result


@router.get("/members/region-resolver", response_model=RegionResolverStatus, summary="属地解析服务状态")
async def region_resolver_status(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
) -> RegionResolverStatus:
    """A12-属地：回显 provider 与是否可刷新（不受开关影响的只读目录）。"""
    current.require("matchmaker.member.read")
    provider = settings.region_resolver_provider
    return RegionResolverStatus(
        provider=provider, refresh_enabled=provider != "disabled", adapter_implemented=False
    )


async def _refresh_region(
    db: AsyncSession, current: CurrentMatchmakerAdmin, kind: str, idempotency_key: str
) -> RegionRefreshResult:
    """一键刷新属地：provider 门控。disabled→503、real→501（适配器待接入）、mock→干跑统计（不写库）。

    全项目无 IP→属地 / 号码归属地解析器（外部依赖），mock 不伪造真实地理值，仅统计候选行。
    """
    provider = settings.region_resolver_provider
    if provider == "disabled":
        raise HTTPException(503, detail="属地解析服务未接入（厂商未配置）")
    if provider == "real":
        raise HTTPException(501, detail="属地解析适配器待接入")
    reservation, replayed = await begin_sensitive_operation(
        db,
        actor_id=current.account.id,
        operation=f"matchmaker.member.refresh_{kind}_region",
        idempotency_key=idempotency_key,
        payload={"kind": kind},
    )
    if replayed is not None:
        return RegionRefreshResult(**replayed)
    try:
        available = await _available_columns(db)
        source_col = "register_ip" if kind == "ip" else "phone"
        params: dict[str, object] = {}
        scope = current.scope_exists_clause(params, correlation="scope_assignment.user_id = users.id")
        deleted_guard = " AND users.deleted_at IS NULL" if ("users", "deleted_at") in available else ""
        source_guard = (
            f" AND users.{source_col} IS NOT NULL AND users.{source_col} <> ''"
            if ("users", source_col) in available else ""
        )
        scanned = int((await db.execute(text(
            f"SELECT COUNT(*) FROM users WHERE ({scope}){deleted_guard}{source_guard}"), params)).scalar() or 0)
    except Exception:
        # 与 batch/single delete 同口径：干跑统计失败必须释放预占行，否则同键 5 分钟内被锁死 409。
        await abort_sensitive_operation(db, reservation)
        raise
    result = RegionRefreshResult(
        kind=kind, provider=provider, dry_run=True, scanned_count=scanned,
        updated_count=0, adapter_implemented=False,
    )
    await finish_sensitive_operation(db, reservation, result.model_dump(mode="json"))
    return result


@router.post("/members/refresh-ip-region", response_model=RegionRefreshResult, summary="一键刷新 IP 属地（敏感，provider 门控）")
async def refresh_ip_region(
    idempotency_key: str = Depends(require_sensitive_operation),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> RegionRefreshResult:
    current.require("matchmaker.member.manage")
    return await _refresh_region(db, current, "ip", idempotency_key)


@router.post("/members/refresh-phone-region", response_model=RegionRefreshResult, summary="一键刷新手机属地（敏感，provider 门控）")
async def refresh_phone_region(
    idempotency_key: str = Depends(require_sensitive_operation),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> RegionRefreshResult:
    current.require("matchmaker.member.manage")
    return await _refresh_region(db, current, "phone", idempotency_key)


def _wechat_columns_ok(available: frozenset[tuple[str, str]]) -> None:
    # openid/unionid 是 users 基础列；wechat_bound_at 由补列器保证。缺失时拒绝而非写坏 SQL。
    if ("users", "openid") not in available or ("users", "unionid") not in available:
        raise HTTPException(503, detail="微信绑定列未就绪：请执行账号生命周期迁移")


@router.post("/members/{member_id}/wechat-binding", response_model=MemberWechatBindingResult, summary="为会员绑定微信（敏感，幂等 + 二次确认）")
async def member_wechat_bind(
    body: MemberWechatBindRequest,
    member_id: int = Path(..., ge=1),
    idempotency_key: str = Depends(require_sensitive_operation),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MemberWechatBindingResult:
    """A12-微信绑定（第389行）：后台手动将 openid/unionid 关联到会员。敏感写。

    openid 全局唯一（已被他人占用 → 409）；作用域收口 + 软删行不可操作。
    """
    current.require("matchmaker.member.manage")
    available = await _available_columns(db)
    _wechat_columns_ok(available)
    reservation, replayed = await begin_sensitive_operation(
        db,
        actor_id=current.account.id,
        operation="matchmaker.member.wechat_bind",
        idempotency_key=idempotency_key,
        payload={"member_id": member_id, "body": body.model_dump(mode="json")},
    )
    if replayed is not None:
        return MemberWechatBindingResult(**replayed)
    params: dict[str, object] = {"member_id": member_id, "openid": body.openid, "unionid": body.unionid}
    scope = current.scope_exists_clause(params, correlation="scope_assignment.user_id = users.id")
    deleted_guard = " AND users.deleted_at IS NULL" if ("users", "deleted_at") in available else ""
    try:
        row = (await db.execute(text(
            "SELECT users.openid AS openid FROM users WHERE users.id=:member_id"
            f" AND ({scope}){deleted_guard} FOR UPDATE"), params)).mappings().first()
        if row is None:
            await abort_sensitive_operation(db, reservation)
            raise HTTPException(404, detail="会员不存在或无权限")
        taken = (await db.execute(text(
            "SELECT id FROM users WHERE openid=:openid AND id<>:member_id LIMIT 1"),
            {"openid": body.openid, "member_id": member_id})).first()
        if taken:
            await abort_sensitive_operation(db, reservation)
            raise HTTPException(409, detail="该微信 openid 已绑定其他会员")
        set_parts = ["openid=:openid", "unionid=:unionid"]
        if ("users", "wechat_bound_at") in available:
            set_parts.append("wechat_bound_at=UTC_TIMESTAMP()")
        await db.execute(text(
            f"UPDATE users SET {', '.join(set_parts)}, updated_at=UTC_TIMESTAMP() WHERE id=:member_id"), params)
        await db.execute(text(
            "INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id, reason)"
            " VALUES (:actor, 'member.wechat_bind', 'users', :id, :reason)"),
            {"actor": current.account.id, "id": member_id, "reason": body.reason[:255]})
        await db.commit()
    except HTTPException:
        raise
    except Exception:
        await abort_sensitive_operation(db, reservation)
        raise
    result = MemberWechatBindingResult(member_id=member_id, wechat_bound=True)
    await finish_sensitive_operation(db, reservation, result.model_dump(mode="json"))
    return result


@router.delete("/members/{member_id}/wechat-binding", response_model=MemberWechatBindingResult, summary="解绑会员微信（敏感，幂等 + 二次确认）")
async def member_wechat_unbind(
    member_id: int = Path(..., ge=1),
    reason: str = Query("后台解绑", min_length=1, max_length=255, description="解绑理由（记入审计）"),
    idempotency_key: str = Depends(require_sensitive_operation),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MemberWechatBindingResult:
    """A12-微信解绑（第389行）：清空会员 openid/unionid/wechat_bound_at。敏感写。

    防锁死：微信-only 账号（无手机号）解绑后将无法登录，拒绝并返回 409，要求先绑手机号。
    """
    current.require("matchmaker.member.manage")
    available = await _available_columns(db)
    _wechat_columns_ok(available)
    reservation, replayed = await begin_sensitive_operation(
        db,
        actor_id=current.account.id,
        operation="matchmaker.member.wechat_unbind",
        idempotency_key=idempotency_key,
        payload={"member_id": member_id, "reason": reason},
    )
    if replayed is not None:
        return MemberWechatBindingResult(**replayed)
    params: dict[str, object] = {"member_id": member_id}
    scope = current.scope_exists_clause(params, correlation="scope_assignment.user_id = users.id")
    deleted_guard = " AND users.deleted_at IS NULL" if ("users", "deleted_at") in available else ""
    try:
        row = (await db.execute(text(
            "SELECT users.openid AS openid, users.phone AS phone FROM users WHERE users.id=:member_id"
            f" AND ({scope}){deleted_guard} FOR UPDATE"), params)).mappings().first()
        if row is None:
            await abort_sensitive_operation(db, reservation)
            raise HTTPException(404, detail="会员不存在或无权限")
        if not row["openid"]:
            await abort_sensitive_operation(db, reservation)
            raise HTTPException(409, detail="该会员未绑定微信")
        if not row["phone"]:
            await abort_sensitive_operation(db, reservation)
            raise HTTPException(409, detail="该会员未绑定手机号，解绑微信后将无法登录，请先绑定手机号")
        set_parts = ["openid=NULL", "unionid=NULL"]
        if ("users", "wechat_bound_at") in available:
            set_parts.append("wechat_bound_at=NULL")
        await db.execute(text(
            f"UPDATE users SET {', '.join(set_parts)}, updated_at=UTC_TIMESTAMP() WHERE id=:member_id"), params)
        await db.execute(text(
            "INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id, reason)"
            " VALUES (:actor, 'member.wechat_unbind', 'users', :id, :reason)"),
            {"actor": current.account.id, "id": member_id, "reason": reason[:255]})
        await db.commit()
    except HTTPException:
        raise
    except Exception:
        await abort_sensitive_operation(db, reservation)
        raise
    result = MemberWechatBindingResult(member_id=member_id, wechat_bound=False)
    await finish_sensitive_operation(db, reservation, result.model_dump(mode="json"))
    return result


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
    params: dict[str, object] = {}
    scope = current.scope_exists_clause(params, correlation="scope_assignment.user_id = u.id")
    return (await list_realname_reviews(db, page=page, page_size=page_size, status=status, search=search, scope=scope, scope_params=params)).model_dump()


@router.patch("/members/{member_id}/realname/review")
async def review_member_realname(
    member_id: int,
    body: RealnameReviewRequest,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.api.routes.member_records_admin import _ensure_member
    await _ensure_member(db, member_id, current)
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
    params: dict[str, object] = {}
    scope = current.scope_exists_clause(params, correlation="scope_assignment.user_id = u.id")
    return (await list_certification_reviews(db, page=page, page_size=page_size, viewer=current.account.id, kind=kind, status=status, search=search, scope=scope, scope_params=params)).model_dump()


@router.patch("/members/{member_id}/certifications/{kind}/review")
async def review_member_certification(
    member_id: int,
    kind: str = Path(..., pattern="^(education|house|marriage)$"),
    body: CertificationReviewRequest = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.api.routes.member_records_admin import _ensure_member
    await _ensure_member(db, member_id, current)
    return (await review_certification(db, member_id, kind, body)).model_dump()


@router.get("/members/{member_id}", response_model=MemberDetail, summary="查询会员详情")
async def member_detail(member_id: int = Path(..., ge=1), current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> MemberDetail:
    column_rows = await db.execute(text("""SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ('user_profile', 'user_auth', 'users')"""))
    available = {(row[0], row[1]) for row in column_rows.all()}
    def profile_expr(name: str) -> str:
        return f"p.{name}" if ("user_profile", name) in available else "NULL"

    def auth_expr(name: str) -> str:
        return f"ua.{name}" if ("user_auth", name) in available else "NULL"

    def user_expr(name: str) -> str:
        return f"u.{name}" if ("users", name) in available else "NULL"
    params: dict[str, object] = {"id": member_id}
    # 越权访问按「不存在」处理（404），避免通过状态码差异枚举他组织会员 ID。
    scope = current.scope_exists_clause(params, correlation="scope_assignment.user_id = u.id")
    # 软删行不可见（A12-会员删除）：与列表同口径，列缺失时不拼。
    deleted_guard = " AND u.deleted_at IS NULL" if ("users", "deleted_at") in available else ""
    row = (await db.execute(text(f"""SELECT u.id, u.nickname, u.phone, u.gender, u.status, u.avatar, u.birthday, u.is_married, u.created_at,
        u.last_login_at, u.register_ip AS ip_location, {user_expr('ip_region')} AS ip_region, {user_expr('phone_region')} AS phone_region, {user_expr('login_num')} AS login_num, COALESCE((SELECT up.balance FROM user_points up WHERE up.user_id = u.id ORDER BY up.id DESC LIMIT 1), 0) AS points, {MEMBER_BALANCE_EXPR} AS balance, (CASE WHEN {user_expr('openid')} IS NOT NULL AND {user_expr('openid')} <> '' THEN 1 ELSE 0 END) AS wechat_bound, {user_expr('wechat_bound_at')} AS wechat_bound_at, {profile_expr('residence_city_code')} AS residence_city_code,
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
        LEFT JOIN {_ASSIGNMENT_DISPLAY_DERIVED} a ON a.user_id = u.id WHERE u.id = :id AND ({scope}){deleted_guard}"""), params)).mappings().first()
    if not row:
        from fastapi import HTTPException
        raise HTTPException(404, detail="会员不存在")
    data = dict(row)
    data["tags"] = _normalize_tags(data.get("tags"))
    # 微信号整列本身就是敏感值，必须按权限分级掩码后再返回；SQL 侧原样取出只是为了把
    # 脱敏口径集中在一处，elevated 档也仅保留首尾若干位。
    data["wechat"] = mask_contact(data.get("wechat"), redaction_level_from_permissions(current.permissions))
    return MemberDetail(**data)


class MemberContactReveal(BaseModel):
    member_id: int
    field: str
    value: str | None


@router.get("/members/{member_id}/contact", response_model=MemberContactReveal, summary="查看会员完整联系方式（权限门控 + 逐次审计）")
async def member_contact_reveal(
    member_id: int = Path(..., ge=1),
    field: str = Query("wechat", pattern="^(wechat|phone)$", description="要查看的联系方式字段：wechat 微信号 / phone 手机号"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MemberContactReveal:
    """A19：详情接口对微信号做服务端掩码（隐私纵深），前端「查看微信 / 查看手机」调本接口按权限取完整值。

    与掩码出参不同：本接口是唯一返回原值的出口，必须 `matchmaker.member.manage` 权限 + D-16 数据范围校验
    + 逐次审计；越权/不存在按 404（不区分两种情形，避免枚举会员 ID）。微信号列缺失时 503。
    """
    current.require("matchmaker.member.manage")
    available = await _available_columns(db)
    params: dict[str, object] = {"member_id": member_id}
    scope = current.scope_exists_clause(params, correlation="scope_assignment.user_id = u.id")
    deleted_guard = " AND u.deleted_at IS NULL" if ("users", "deleted_at") in available else ""
    if field == "phone":
        expr = "u.phone"
    else:
        if ("user_profile", "wechat") not in available:
            raise HTTPException(503, detail="微信号列未就绪，无法查看完整微信号")
        expr = "p.wechat"
    row = (
        await db.execute(
            text(
                f"SELECT {expr} AS value FROM users u "
                "LEFT JOIN user_profile p ON p.user_id = u.id "
                f"WHERE u.id = :member_id AND ({scope}){deleted_guard}"
            ),
            params,
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(404, detail="会员不存在或无权限")
    raw = row["value"]
    await db.execute(
        text(
            "INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id, reason)"
            " VALUES (:actor, 'member.contact_reveal', 'users', :id, :reason)"
        ),
        {"actor": current.account.id, "id": member_id, "reason": f"查看完整{field}"[:255]},
    )
    await db.commit()
    return MemberContactReveal(
        member_id=member_id, field=field, value=(str(raw) if raw is not None and str(raw).strip() != "" else None)
    )


@router.patch("/members/{member_id}/assignment", response_model=MemberAssignmentResponse, summary="修改会员服务红娘（幂等 + 二次确认）")
async def member_assignment(
    member_id: int = Path(..., ge=1),
    body: MemberAssignmentUpdate = ...,
    idempotency_key: str = Depends(require_sensitive_operation),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MemberAssignmentResponse:
    # 先做作用域守卫（越权直接 404，且不写任何幂等/业务行），再做幂等预占。
    guard_params: dict[str, object] = {"id": member_id}
    scope = current.scope_exists_clause(guard_params, correlation="scope_assignment.user_id = u.id")
    if not (await db.execute(text(f"SELECT u.id FROM users u WHERE u.id = :id AND ({scope})"), guard_params)).scalar():
        raise HTTPException(404, detail="会员不存在")
    if body.matchmaker_id is not None and not (await db.execute(text("""SELECT 1 FROM user_role
        WHERE user_id = :id AND role_code = 'service_matchmaker' AND status = 1 LIMIT 1"""), {"id": body.matchmaker_id})).scalar():
        raise HTTPException(422, detail="指定用户不是有效的服务红娘")
    # D-9：改派是写归属的高影响操作，纳入统一幂等 + 二次确认。
    reservation, replayed = await begin_sensitive_operation(
        db,
        actor_id=current.account.id,
        operation="matchmaker.member.reassign",
        idempotency_key=idempotency_key,
        payload={"member_id": member_id, "body": body.model_dump(mode="json")},
    )
    if replayed is not None:
        return MemberAssignmentResponse(**replayed)
    try:
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
    except Exception:
        await abort_sensitive_operation(db, reservation)
        raise
    result = MemberAssignmentResponse(user_id=member_id, matchmaker_id=body.matchmaker_id)
    await finish_sensitive_operation(db, reservation, result.model_dump(mode="json"))
    return result


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


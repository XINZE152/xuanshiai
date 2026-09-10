"""Scoped mobile workspace services for service matchmakers, partners and promoters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import base64
import json
from secrets import token_urlsafe

import httpx
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser
from app.core.config import settings
from app.services.customer_lead_contact import (
    ensure_contact_available,
    normalize_contact,
    raise_duplicate_contact,
)
from app.schemas.matchmaker_workspace import (
    MatchmakerWorkspaceAccess,
    MatchmakerWorkspaceDashboard,
    MatchmakerWorkspaceIdentity,
    MatchmakerWorkspaceProfile,
    MatchmakerWorkspaceProfileUpdate,
    MemberPartnerPreference,
    PartnerCenterProfile,
    PartnerCenterSnapshot,
    PartnerEffectiveMember,
    PartnerJoinRequestCreate,
    PartnerJoinRequestReview,
    PartnerJoinRequestSummary,
    PartnerLevelInfo,
    PartnerTeamMember,
    PartnerTeamUpdate,
    PromoterCenterProfile,
    PromoterCenterSnapshot,
    PromoterRewardItem,
    PromoterLeadCreate,
    PromoterMember,
    PromotionCode,
    WorkspaceAbandonRequest,
    WorkspaceAssignRequest,
    WorkspaceLead,
    WorkspaceLeadConvertRequest,
    WorkspaceLeadConvertResult,
    WorkspaceLeadCreate,
    WorkspaceLeadFollowUp,
    WorkspaceLeadFollowUpCreate,
    WorkspaceLeadFollowUpPage,
    WorkspaceLeadFollower,
    WorkspaceLeadPage,
    WorkspaceLeadUpdate,
    WorkspaceMemberEntryCreate,
    WorkspaceMemberEntryResult,
    WorkspaceIntroduction,
    WorkspaceIntroductionCreate,
    WorkspaceIntroductionCreated,
    WorkspaceIntroductionPage,
    WorkspaceIntroductionPerson,
    WorkspaceMatchCandidate,
    WorkspaceMatchCandidatePage,
    WorkspaceMatchHistoryItem,
    WorkspaceMatchHistoryPage,
    WorkspaceMember,
    WorkspaceMemberContact,
    WorkspaceMemberFollowUp,
    WorkspaceMemberFollowUpCreate,
    WorkspaceMemberFollowUpPage,
    WorkspaceMemberMatchmakerUpdate,
    WorkspaceMemberNote,
    WorkspaceMemberNoteUpdate,
    WorkspaceMemberPage,
    WorkspaceMemberReviewUpdate,
    WorkspaceMeetingRecordSummary,
    WorkspaceMeetingRequest,
    WorkspaceMeetingRequestPage,
    WorkspaceIntroductionStatusUpdate,
    WorkspaceMeetingScheduleCreate,
    WorkspaceMeetingRecordStatusUpdate,
    WorkspaceMeetingStatusUpdate,
    WorkspaceMetric,
)


@dataclass(frozen=True)
class WorkspaceActor:
    user_id: int
    display_name: str
    level: str
    organization_id: int | None

    @property
    def scope(self) -> str:
        return "ORGANIZATION" if self.level == "SUPER" else "SELF"


def _page(total: int, page: int, page_size: int) -> bool:
    return page * page_size < total


async def _audit(
    db: AsyncSession,
    actor_id: int,
    action: str,
    resource_type: str,
    resource_id: int | None,
    reason: str | None = None,
) -> None:
    await db.execute(
        text(
            """INSERT INTO business_audit_log
            (actor_user_id, action, resource_type, resource_id, reason)
            VALUES (:actor_id, :action, :resource_type, :resource_id, :reason)"""
        ),
        {
            "actor_id": actor_id,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "reason": reason,
        },
    )


async def _has_active_role(db: AsyncSession, user_id: int, role_code: str) -> bool:
    value = await db.scalar(
        text(
            """SELECT 1 FROM user_role
            WHERE user_id = :user_id AND role_code = :role_code AND status = 1 LIMIT 1"""
        ),
        {"user_id": user_id, "role_code": role_code},
    )
    return bool(value)


async def workspace_access(
    db: AsyncSession, current: CurrentUser
) -> MatchmakerWorkspaceAccess:
    if not await _has_active_role(db, current.id, "service_matchmaker"):
        return MatchmakerWorkspaceAccess(
            can_access=False,
            role_status="inactive",
            message="服务红娘申请审核通过并激活后可进入管理中心",
        )
    row = (
        await db.execute(
            text(
                """SELECT level, organization_id FROM matchmaker_workspace_profile
                WHERE user_id = :user_id AND status = 1"""
            ),
            {"user_id": current.id},
        )
    ).mappings().first()
    if row and row["level"] == "SUPER" and row["organization_id"] is None:
        return MatchmakerWorkspaceAccess(
            can_access=False,
            role_status="inactive",
            message="超级服务红娘尚未绑定有效组织范围",
        )
    return MatchmakerWorkspaceAccess(
        can_access=True,
        role_status="active",
        scope="ORGANIZATION" if row and row["level"] == "SUPER" else "SELF",
        message="服务红娘工作台已开通",
    )


async def require_workspace(db: AsyncSession, current: CurrentUser) -> WorkspaceActor:
    access = await workspace_access(db, current)
    if not access.can_access:
        raise HTTPException(403, detail=access.message)
    row = (
        await db.execute(
            text(
                """SELECT COALESCE(p.display_name, u.nickname, '服务红娘') AS display_name,
                    COALESCE(p.level, 'NORMAL') AS level, p.organization_id
                FROM users u
                LEFT JOIN matchmaker_workspace_profile p ON p.user_id = u.id AND p.status = 1
                WHERE u.id = :user_id"""
            ),
            {"user_id": current.id},
        )
    ).mappings().one()
    return WorkspaceActor(
        user_id=current.id,
        display_name=str(row["display_name"]),
        level=str(row["level"]),
        organization_id=int(row["organization_id"]) if row["organization_id"] else None,
    )


def _scope_predicate(actor: WorkspaceActor, alias: str, params: dict[str, object]) -> str:
    if actor.scope == "SELF":
        params["scope_matchmaker_id"] = actor.user_id
        return f"{alias}.matchmaker_id = :scope_matchmaker_id"
    if actor.organization_id is None:
        raise HTTPException(403, detail="超级服务红娘尚未绑定有效组织范围")
    params["scope_organization_id"] = actor.organization_id
    return f"{alias}.organization_id = :scope_organization_id"


def _member_scope_predicate(actor: WorkspaceActor, params: dict[str, object]) -> str:
    if actor.scope == "SELF":
        params["scope_matchmaker_id"] = actor.user_id
        return "EXISTS (SELECT 1 FROM resource_assignment ra WHERE ra.user_id = u.id AND ra.status = 1 AND ra.matchmaker_id = :scope_matchmaker_id)"
    if actor.organization_id is None:
        raise HTTPException(403, detail="超级服务红娘尚未绑定有效组织范围")
    params["scope_organization_id"] = actor.organization_id
    return "EXISTS (SELECT 1 FROM resource_assignment ra WHERE ra.user_id = u.id AND ra.status = 1 AND ra.organization_id = :scope_organization_id)"


async def get_workspace_profile(
    db: AsyncSession, actor: WorkspaceActor
) -> MatchmakerWorkspaceProfile:
    return MatchmakerWorkspaceProfile(
        user_id=actor.user_id,
        display_name=actor.display_name,
        level=actor.level,
        scope=actor.scope,
        organization_id=actor.organization_id,
    )


async def update_workspace_profile(
    db: AsyncSession,
    actor: WorkspaceActor,
    request: MatchmakerWorkspaceProfileUpdate,
) -> MatchmakerWorkspaceProfile:
    await db.execute(
        text(
            """INSERT INTO matchmaker_workspace_profile (user_id, display_name, level, organization_id, status)
            VALUES (:user_id, :display_name, 'NORMAL', NULL, 1)
            ON DUPLICATE KEY UPDATE display_name = VALUES(display_name), updated_at = UTC_TIMESTAMP()"""
        ),
        {"user_id": actor.user_id, "display_name": request.display_name},
    )
    await _audit(
        db,
        actor.user_id,
        "matchmaker_workspace.profile.update",
        "matchmaker_workspace_profile",
        actor.user_id,
    )
    await db.commit()
    updated = WorkspaceActor(
        user_id=actor.user_id,
        display_name=request.display_name,
        level=actor.level,
        organization_id=actor.organization_id,
    )
    return await get_workspace_profile(db, updated)


async def workspace_dashboard(
    db: AsyncSession, actor: WorkspaceActor
) -> MatchmakerWorkspaceDashboard:
    member_params: dict[str, object] = {}
    member_scope = _member_scope_predicate(actor, member_params)
    member_count = int(
        await db.scalar(text(f"SELECT COUNT(*) FROM users u WHERE {member_scope}"), member_params)
        or 0
    )
    pending_count = int(
        await db.scalar(
            text(
                f"""SELECT COUNT(*) FROM users u
                LEFT JOIN matchmaker_member_review mr ON mr.user_id = u.id
                WHERE {member_scope} AND COALESCE(mr.status, 'PENDING') = 'PENDING'"""
            ),
            member_params,
        )
        or 0
    )
    introduction_params: dict[str, object] = {}
    introduction_scope = _scope_predicate(actor, "mi", introduction_params)
    intro_count = int(
        await db.scalar(
            text(
                f"""SELECT COUNT(*) FROM matchmaker_introduction mi
                WHERE {introduction_scope} AND mi.status = 'PENDING'"""
            ),
            introduction_params,
        )
        or 0
    )
    intro_success_count = int(
        await db.scalar(
            text(
                f"""SELECT COUNT(*) FROM matchmaker_introduction mi
                WHERE {introduction_scope} AND mi.status = 'SUCCEEDED'"""
            ),
            introduction_params,
        )
        or 0
    )
    today_pending_params: dict[str, object] = {}
    today_pending_scope = _scope_predicate(actor, "ra", today_pending_params)
    today_pending_count = int(
        await db.scalar(
            text(
                f"""SELECT COUNT(DISTINCT ra.user_id) FROM resource_assignment ra
                JOIN matchmaker_member_review mr ON mr.user_id = ra.user_id
                WHERE ra.status = 1 AND {today_pending_scope} AND mr.status = 'PENDING'
                AND mr.created_at >= UTC_DATE()"""
            ),
            today_pending_params,
        )
        or 0
    )
    meeting_params: dict[str, object] = {}
    meeting_scope = _scope_predicate(actor, "mr", meeting_params)
    meeting_request_count = int(
        await db.scalar(
            text(
                f"""SELECT COUNT(*) FROM meeting_request mr WHERE {meeting_scope}
                AND mr.status = 'SUBMITTED'"""
            ),
            meeting_params,
        )
        or 0
    )
    pending_meeting_count = int(
        await db.scalar(
            text(
                f"""SELECT COUNT(*) FROM meeting_record m
                JOIN meeting_request mr ON mr.id = m.request_id
                WHERE {meeting_scope} AND m.status IN ('SCHEDULED', 'REMINDED', 'CHECKED_IN')"""
            ),
            meeting_params,
        )
        or 0
    )
    online_commission = await db.scalar(
        text(
            """SELECT COALESCE(SUM(amount), 0) FROM commission_entry
            WHERE beneficiary_type = 'service_matchmaker' AND beneficiary_id = :user_id
            AND status <> 'REVERSED'"""
        ),
        {"user_id": actor.user_id},
    )
    metrics = [
        WorkspaceMetric(
            key="pending_review",
            label="资料待审",
            value=str(pending_count),
            action="资料待审",
            badge=f"今日+{today_pending_count}" if today_pending_count > 0 else None,
        ),
        WorkspaceMetric(key="served_members", label="我服务的", value=str(member_count), action="我服务的"),
        WorkspaceMetric(
            key="online_commission",
            label="线上分成",
            value=str(online_commission or 0),
            action="分成明细",
            value_prefix="￥",
        ),
        WorkspaceMetric(
            key="offline_performance",
            label="线下业绩",
            value="0",
            action="财务明细",
            value_prefix="￥",
        ),
        WorkspaceMetric(key="pending_introduction", label="待我牵线", value=str(intro_count), action="牵线记录"),
        WorkspaceMetric(key="introduction_succeeded", label="牵线成功", value=str(intro_success_count), action="牵线记录"),
        WorkspaceMetric(key="meeting_requests", label="约见申请", value=str(meeting_request_count), action="约见申请"),
        WorkspaceMetric(key="pending_meeting", label="待见面", value=str(pending_meeting_count), action="约见申请"),
    ]
    return MatchmakerWorkspaceDashboard(
        identity=MatchmakerWorkspaceIdentity(
            greeting="你好", name=actor.display_name, role_name="超级服务红娘" if actor.level == "SUPER" else "服务红娘"
        ),
        store_metrics=metrics,
    )


def _introduction_from_row(row: object) -> WorkspaceIntroduction:
    values = dict(row)  # type: ignore[arg-type]
    return WorkspaceIntroduction(
        id=int(values["id"]),
        status=str(values["status"]),
        failure_reason=values.get("failure_reason"),
        note=values.get("note"),
        created_at=values["created_at"],
        updated_at=values["updated_at"],
        from_user=WorkspaceIntroductionPerson(
            user_id=int(values["from_user_id"]),
            nickname=str(values["from_nickname"] or "未命名会员"),
            avatar=str(values["from_avatar"]) if values.get("from_avatar") else None,
            gender=int(values["from_gender"]) if values.get("from_gender") in {1, 2} else None,
        ),
        to_user=WorkspaceIntroductionPerson(
            user_id=int(values["to_user_id"]),
            nickname=str(values["to_nickname"] or "未命名会员"),
            avatar=str(values["to_avatar"]) if values.get("to_avatar") else None,
            gender=int(values["to_gender"]) if values.get("to_gender") in {1, 2} else None,
        ),
    )


async def list_workspace_introductions(
    db: AsyncSession,
    actor: WorkspaceActor,
    page: int,
    page_size: int,
    status: str | None,
    search: str | None,
) -> WorkspaceIntroductionPage:
    params: dict[str, object] = {"limit": page_size, "offset": (page - 1) * page_size}
    where = [_scope_predicate(actor, "mi", params)]
    if status:
        where.append("mi.status = :status")
        params["status"] = status
    if search:
        where.append(
            "(fu.nickname LIKE CONCAT('%', :search, '%') OR tu.nickname LIKE CONCAT('%', :search, '%') "
            "OR CAST(mi.id AS CHAR) LIKE CONCAT('%', :search, '%'))"
        )
        params["search"] = search
    clause = " AND ".join(where)
    base = """FROM matchmaker_introduction mi
        JOIN users fu ON fu.id = mi.from_user_id
        JOIN users tu ON tu.id = mi.to_user_id"""
    rows = await db.execute(
        text(
            f"""SELECT mi.id, mi.status, mi.failure_reason, mi.note, mi.created_at, mi.updated_at,
            fu.id AS from_user_id, fu.nickname AS from_nickname, fu.avatar AS from_avatar, fu.gender AS from_gender,
            tu.id AS to_user_id, tu.nickname AS to_nickname, tu.avatar AS to_avatar, tu.gender AS to_gender
            {base} WHERE {clause} ORDER BY mi.created_at DESC, mi.id DESC LIMIT :limit OFFSET :offset"""
        ),
        params,
    )
    count_params = {key: value for key, value in params.items() if key not in {"limit", "offset"}}
    total = int(await db.scalar(text(f"SELECT COUNT(*) {base} WHERE {clause}"), count_params) or 0)
    return WorkspaceIntroductionPage(
        items=[_introduction_from_row(row) for row in rows.mappings().all()],
        page=page,
        page_size=page_size,
        total=total,
        has_more=_page(total, page, page_size),
    )


def _meeting_request_from_row(row: object) -> WorkspaceMeetingRequest:
    values = dict(row)  # type: ignore[arg-type]
    meeting = None
    if values.get("meeting_id") is not None:
        meeting = WorkspaceMeetingRecordSummary(
            id=int(values["meeting_id"]),
            status=str(values["meeting_status"]),
            scheduled_at=values["scheduled_at"],
            location=str(values["location"] or "待确认地点"),
            cancel_reason=values.get("cancel_reason"),
        )
    return WorkspaceMeetingRequest(
        id=int(values["id"]),
        status=str(values["status"]),
        matchmaker_name=str(values.get("matchmaker_display_name") or values.get("matchmaker_nickname") or "待分派"),
        note=str(values["note"] or ""),
        created_at=values["created_at"],
        updated_at=values["updated_at"],
        applicant=WorkspaceIntroductionPerson(
            user_id=int(values["applicant_user_id"]),
            nickname=str(values["applicant_nickname"] or "未命名会员"),
            avatar=str(values["applicant_avatar"]) if values.get("applicant_avatar") else None,
            gender=int(values["applicant_gender"]) if values.get("applicant_gender") in {1, 2} else None,
        ),
        target=WorkspaceIntroductionPerson(
            user_id=int(values["target_user_id"]),
            nickname=str(values["target_nickname"] or "未命名会员"),
            avatar=str(values["target_avatar"]) if values.get("target_avatar") else None,
            gender=int(values["target_gender"]) if values.get("target_gender") in {1, 2} else None,
        ),
        meeting=meeting,
    )


async def list_workspace_meeting_requests(
    db: AsyncSession,
    actor: WorkspaceActor,
    page: int,
    page_size: int,
    search: str | None,
) -> WorkspaceMeetingRequestPage:
    params: dict[str, object] = {"limit": page_size, "offset": (page - 1) * page_size}
    where = [_scope_predicate(actor, "mr", params)]
    if search:
        where.append(
            "(au.nickname LIKE CONCAT('%', :search, '%') OR tu.nickname LIKE CONCAT('%', :search, '%') "
            "OR CAST(mr.id AS CHAR) LIKE CONCAT('%', :search, '%'))"
        )
        params["search"] = search
    clause = " AND ".join(where)
    base = """FROM meeting_request mr
        JOIN users au ON au.id = mr.user_id
        JOIN users tu ON tu.id = mr.target_user_id
        LEFT JOIN users mu ON mu.id = mr.matchmaker_id
        LEFT JOIN matchmaker_workspace_profile mwp ON mwp.user_id = mr.matchmaker_id AND mwp.status = 1
        LEFT JOIN (
            SELECT request_id, MAX(id) AS meeting_id FROM meeting_record GROUP BY request_id
        ) latest ON latest.request_id = mr.id
        LEFT JOIN meeting_record m ON m.id = latest.meeting_id"""
    rows = await db.execute(
        text(
            f"""SELECT mr.id, mr.status, mr.note, mr.created_at, mr.updated_at,
            au.id AS applicant_user_id, au.nickname AS applicant_nickname, au.avatar AS applicant_avatar, au.gender AS applicant_gender,
            tu.id AS target_user_id, tu.nickname AS target_nickname, tu.avatar AS target_avatar, tu.gender AS target_gender,
            mu.nickname AS matchmaker_nickname, mwp.display_name AS matchmaker_display_name,
            m.id AS meeting_id, m.status AS meeting_status, m.scheduled_at, m.location, m.cancel_reason
            {base} WHERE {clause} ORDER BY mr.created_at DESC, mr.id DESC LIMIT :limit OFFSET :offset"""
        ),
        params,
    )
    count_params = {key: value for key, value in params.items() if key not in {"limit", "offset"}}
    total = int(await db.scalar(text(f"SELECT COUNT(*) {base} WHERE {clause}"), count_params) or 0)
    return WorkspaceMeetingRequestPage(
        items=[_meeting_request_from_row(row) for row in rows.mappings().all()],
        page=page,
        page_size=page_size,
        total=total,
        has_more=_page(total, page, page_size),
    )


_LEAD_SELECT = """SELECT l.id, l.name, l.phone, l.wechat, l.source, l.intention_level, l.status,
    l.next_follow_at, l.created_at, l.updated_at, l.remark, l.matchmaker_id, l.converted_user_id,
    COALESCE(p.display_name, u.nickname) AS matchmaker_name,
    lf.last_follow_up_at
    FROM customer_lead l
    LEFT JOIN matchmaker_workspace_profile p ON p.user_id = l.matchmaker_id AND p.status = 1
    LEFT JOIN users u ON u.id = l.matchmaker_id
    LEFT JOIN (SELECT lead_id, MAX(created_at) AS last_follow_up_at
        FROM customer_lead_follow_up GROUP BY lead_id) lf ON lf.lead_id = l.id"""


def _lead_from_row(row: object) -> WorkspaceLead:
    values = dict(row)  # type: ignore[arg-type]
    return WorkspaceLead(**values)


async def _require_scoped_lead(db: AsyncSession, actor: WorkspaceActor, lead_id: int) -> WorkspaceLead:
    params: dict[str, object] = {"lead_id": lead_id}
    scope = _scope_predicate(actor, "l", params)
    row = (
        await db.execute(text(f"{_LEAD_SELECT} WHERE l.id = :lead_id AND {scope}"), params)
    ).mappings().first()
    if not row:
        raise HTTPException(404, detail="客源线索不存在或不在当前工作范围")
    return _lead_from_row(row)


async def list_workspace_leads(
    db: AsyncSession,
    actor: WorkspaceActor,
    page: int,
    page_size: int,
    status: str | None,
    search: str | None,
) -> WorkspaceLeadPage:
    params: dict[str, object] = {"limit": page_size, "offset": (page - 1) * page_size}
    where = [_scope_predicate(actor, "l", params)]
    if status:
        where.append("l.status = :status")
        params["status"] = status
    if search:
        where.append("(l.name LIKE CONCAT('%', :search, '%') OR l.phone LIKE CONCAT('%', :search, '%'))")
        params["search"] = search
    clause = " AND ".join(where)
    rows = await db.execute(
        text(f"{_LEAD_SELECT} WHERE {clause} ORDER BY l.id DESC LIMIT :limit OFFSET :offset"),
        params,
    )
    count_params = {key: value for key, value in params.items() if key not in {"limit", "offset"}}
    total = int(
        await db.scalar(text(f"SELECT COUNT(*) FROM customer_lead l WHERE {clause}"), count_params)
        or 0
    )
    return WorkspaceLeadPage(
        items=[_lead_from_row(row) for row in rows.mappings().all()],
        page=page,
        page_size=page_size,
        total=total,
        has_more=_page(total, page, page_size),
    )


async def create_workspace_lead(
    db: AsyncSession, actor: WorkspaceActor, request: WorkspaceLeadCreate
) -> WorkspaceLead:
    phone = normalize_contact(request.phone)
    wechat = normalize_contact(request.wechat)
    await ensure_contact_available(db, phone, wechat)
    try:
        result = await db.execute(
            text(
                """INSERT INTO customer_lead
                (name, phone, wechat, source, intention_level, remark, matchmaker_id, organization_id, created_by)
                VALUES (:name, :phone, :wechat, :source, :intention_level, :remark, :matchmaker_id, :organization_id, :created_by)"""
            ),
            {
                **request.model_dump(),
                "phone": phone,
                "wechat": wechat,
                "matchmaker_id": actor.user_id,
                "organization_id": actor.organization_id,
                "created_by": actor.user_id,
            },
        )
        lead_id = int(result.lastrowid)
        await _audit(db, actor.user_id, "matchmaker_workspace.lead.create", "customer_lead", lead_id)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise_duplicate_contact()
    return await _require_scoped_lead(db, actor, lead_id)


async def update_workspace_lead(
    db: AsyncSession, actor: WorkspaceActor, lead_id: int, request: WorkspaceLeadUpdate
) -> WorkspaceLead:
    current = await _require_scoped_lead(db, actor, lead_id)
    values = request.model_dump(exclude_unset=True)
    target_status = values.get("status") or current.status
    # 目标状态仍为有效线索时，联系方式不得与其它有效线索重复；改为 LOST/CLOSED 后生成列会置空。
    if target_status not in {"LOST", "CLOSED"}:
        target_phone = normalize_contact(values["phone"]) if "phone" in values else normalize_contact(current.phone)
        target_wechat = normalize_contact(values["wechat"]) if "wechat" in values else normalize_contact(current.wechat)
        await ensure_contact_available(db, target_phone, target_wechat, exclude_lead_id=lead_id)
    assignments = ", ".join(f"{name} = :{name}" for name in values)
    try:
        await db.execute(
            text(f"UPDATE customer_lead SET {assignments}, updated_at = UTC_TIMESTAMP() WHERE id = :lead_id"),
            {**values, "lead_id": lead_id},
        )
        await _audit(db, actor.user_id, "matchmaker_workspace.lead.update", "customer_lead", lead_id)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise_duplicate_contact()
    return await _require_scoped_lead(db, actor, lead_id)


async def add_workspace_lead_follow_up(
    db: AsyncSession,
    actor: WorkspaceActor,
    lead_id: int,
    request: WorkspaceLeadFollowUpCreate,
) -> WorkspaceLeadFollowUp:
    await _require_scoped_lead(db, actor, lead_id)
    result = await db.execute(
        text(
            """INSERT INTO customer_lead_follow_up
            (lead_id, method, content, intention_level, next_follow_at, created_by)
            VALUES (:lead_id, :method, :content, :intention_level, :next_follow_at, :created_by)"""
        ),
        {**request.model_dump(), "lead_id": lead_id, "created_by": actor.user_id},
    )
    follow_up_id = int(result.lastrowid)
    await db.execute(
        text(
            """UPDATE customer_lead SET
            status = CASE WHEN status = 'NEW' THEN 'CONTACTED' ELSE status END,
            next_follow_at = :next_follow_at, updated_at = UTC_TIMESTAMP() WHERE id = :lead_id"""
        ),
        {"lead_id": lead_id, "next_follow_at": request.next_follow_at},
    )
    await _audit(db, actor.user_id, "matchmaker_workspace.lead.follow_up", "customer_lead_follow_up", follow_up_id)
    await db.commit()
    row = (
        await db.execute(
            text(
                """SELECT id, lead_id, method, content, intention_level, next_follow_at, created_at
                FROM customer_lead_follow_up WHERE id = :follow_up_id"""
            ),
            {"follow_up_id": follow_up_id},
        )
    ).mappings().one()
    return WorkspaceLeadFollowUp(**dict(row))


async def abandon_workspace_lead(
    db: AsyncSession, actor: WorkspaceActor, lead_id: int, request: WorkspaceAbandonRequest
) -> WorkspaceLead:
    lead = await _require_scoped_lead(db, actor, lead_id)
    if lead.status in {"CONVERTED", "CLOSED", "LOST"}:
        raise HTTPException(409, detail="当前状态的客源不能弃海")
    result = await db.execute(
        text("SELECT id FROM customer_lead_abandonment WHERE lead_id = :lead_id AND restored_at IS NULL"),
        {"lead_id": lead_id},
    )
    if result.scalar():
        raise HTTPException(409, detail="该客源已在弃海池")
    await db.execute(
        text(
            """INSERT INTO customer_lead_abandonment (lead_id, reason, abandoned_by)
            VALUES (:lead_id, :reason, :actor_id)"""
        ),
        {"lead_id": lead_id, "reason": request.reason, "actor_id": actor.user_id},
    )
    await db.execute(
        text(
            """UPDATE customer_lead SET status = 'LOST', matchmaker_id = NULL,
            next_follow_at = NULL, updated_at = UTC_TIMESTAMP() WHERE id = :lead_id"""
        ),
        {"lead_id": lead_id},
    )
    await _audit(db, actor.user_id, "matchmaker_workspace.lead.abandon", "customer_lead", lead_id, request.reason)
    await db.commit()
    # 弃海后普通红娘不再拥有该客源，返回弃海后的稳定副本。
    return WorkspaceLead(**{**lead.model_dump(), "status": "LOST", "next_follow_at": None, "matchmaker_id": None, "matchmaker_name": None})


async def restore_workspace_lead(
    db: AsyncSession, actor: WorkspaceActor, lead_id: int, request: WorkspaceAbandonRequest
) -> WorkspaceLead:
    params: dict[str, object] = {"lead_id": lead_id}
    scope = _scope_predicate(actor, "l", params)
    if actor.scope == "SELF":
        params["actor_id"] = actor.user_id
        scope = f"({scope} OR a.abandoned_by = :actor_id)"
    active = await db.scalar(
        text(
            f"""SELECT a.id FROM customer_lead_abandonment a
            JOIN customer_lead l ON l.id = a.lead_id
            WHERE a.lead_id = :lead_id AND a.restored_at IS NULL AND {scope}
            ORDER BY a.id DESC LIMIT 1"""
        ),
        params,
    )
    if not active:
        raise HTTPException(404, detail="弃海记录不存在或不在当前工作范围")
    # 恢复后线索回到有效状态，先确认联系方式没有被其它有效线索占用。
    lead_row = (
        await db.execute(text("SELECT phone, wechat FROM customer_lead WHERE id = :lead_id"), {"lead_id": lead_id})
    ).mappings().one()
    await ensure_contact_available(
        db,
        normalize_contact(lead_row["phone"]),
        normalize_contact(lead_row["wechat"]),
        exclude_lead_id=lead_id,
        detail="该客源的联系方式已被其他有效线索占用，无法恢复",
    )
    await db.execute(
        text(
            """UPDATE customer_lead_abandonment SET restored_by = :actor_id,
            restored_at = UTC_TIMESTAMP(), restore_reason = :reason WHERE id = :abandonment_id"""
        ),
        {"actor_id": actor.user_id, "reason": request.reason, "abandonment_id": active},
    )
    await db.execute(
        text(
            """UPDATE customer_lead SET status = 'NEW', matchmaker_id = :matchmaker_id,
            organization_id = :organization_id, updated_at = UTC_TIMESTAMP() WHERE id = :lead_id"""
        ),
        {
            "lead_id": lead_id,
            "matchmaker_id": actor.user_id,
            "organization_id": actor.organization_id,
        },
    )
    await _audit(db, actor.user_id, "matchmaker_workspace.lead.restore", "customer_lead", lead_id, request.reason)
    await db.commit()
    return await _require_scoped_lead(db, actor, lead_id)


async def list_workspace_lead_follow_ups(
    db: AsyncSession,
    actor: WorkspaceActor,
    lead_id: int,
    page: int,
    page_size: int,
) -> WorkspaceLeadFollowUpPage:
    await _require_scoped_lead(db, actor, lead_id)
    params: dict[str, object] = {"lead_id": lead_id, "limit": page_size, "offset": (page - 1) * page_size}
    rows = await db.execute(
        text(
            """SELECT id, lead_id, method, content, intention_level, next_follow_at, created_at
            FROM customer_lead_follow_up WHERE lead_id = :lead_id
            ORDER BY id DESC LIMIT :limit OFFSET :offset"""
        ),
        params,
    )
    total = int(
        await db.scalar(
            text("SELECT COUNT(*) FROM customer_lead_follow_up WHERE lead_id = :lead_id"),
            {"lead_id": lead_id},
        )
        or 0
    )
    return WorkspaceLeadFollowUpPage(
        items=[WorkspaceLeadFollowUp(**dict(row)) for row in rows.mappings().all()],
        page=page,
        page_size=page_size,
        total=total,
        has_more=_page(total, page, page_size),
    )


async def list_workspace_colleagues(db: AsyncSession, actor: WorkspaceActor) -> list[WorkspaceLeadFollower]:
    """同组织可分派跟进的服务红娘；普通红娘仅能看到自己。"""
    if actor.scope != "ORGANIZATION" or actor.organization_id is None:
        return [WorkspaceLeadFollower(user_id=actor.user_id, display_name=actor.display_name, is_self=True)]
    params: dict[str, object] = {"organization_id": actor.organization_id}
    rows = await db.execute(
        text(
            """SELECT p.user_id, COALESCE(p.display_name, u.nickname, '服务红娘') AS display_name
            FROM matchmaker_workspace_profile p
            JOIN users u ON u.id = p.user_id
            WHERE p.organization_id = :organization_id AND p.status = 1
            UNION
            SELECT m.matchmaker_id, COALESCE(p2.display_name, u2.nickname, '服务红娘') AS display_name
            FROM customer_lead m
            LEFT JOIN matchmaker_workspace_profile p2 ON p2.user_id = m.matchmaker_id AND p2.status = 1
            LEFT JOIN users u2 ON u2.id = m.matchmaker_id
            WHERE m.organization_id = :organization_id AND m.matchmaker_id IS NOT NULL AND m.status NOT IN ('LOST', 'CLOSED')
            ORDER BY user_id"""
        ),
        params,
    )
    followers: dict[int, WorkspaceLeadFollower] = {}
    for row in rows.mappings().all():
        user_id = int(row["user_id"])
        if user_id not in followers:
            followers[user_id] = WorkspaceLeadFollower(
                user_id=user_id, display_name=str(row["display_name"]), is_self=user_id == actor.user_id
            )
    if actor.user_id not in followers:
        followers[actor.user_id] = WorkspaceLeadFollower(
            user_id=actor.user_id, display_name=actor.display_name, is_self=True
        )
    return list(followers.values())


async def assign_workspace_lead(
    db: AsyncSession, actor: WorkspaceActor, lead_id: int, request: WorkspaceAssignRequest
) -> WorkspaceLead:
    lead = await _require_scoped_lead(db, actor, lead_id)
    if lead.status in {"CONVERTED", "CLOSED"}:
        raise HTTPException(409, detail="已入库或已结婚的客源不能改派")
    if request.matchmaker_id == lead.matchmaker_id:
        return lead
    target = await db.execute(
        text(
            """SELECT p.user_id, COALESCE(p.display_name, u.nickname, '服务红娘') AS display_name
            FROM matchmaker_workspace_profile p JOIN users u ON u.id = p.user_id
            WHERE p.user_id = :matchmaker_id AND p.status = 1
            AND (:organization_id IS NULL OR p.organization_id = :organization_id) LIMIT 1"""
        ),
        {"matchmaker_id": request.matchmaker_id, "organization_id": actor.organization_id},
    )
    row = target.mappings().first()
    if not row:
        raise HTTPException(404, detail="跟进红娘不存在或不在当前组织范围")
    await db.execute(
        text(
            """UPDATE customer_lead SET matchmaker_id = :matchmaker_id,
            organization_id = COALESCE(organization_id, :organization_id), updated_at = UTC_TIMESTAMP()
            WHERE id = :lead_id"""
        ),
        {
            "matchmaker_id": request.matchmaker_id,
            "organization_id": actor.organization_id,
            "lead_id": lead_id,
        },
    )
    await _audit(
        db,
        actor.user_id,
        "matchmaker_workspace.lead.assign",
        "customer_lead",
        lead_id,
        f"分派给 {row['display_name']}",
    )
    await db.commit()
    return await _require_scoped_lead(db, actor, lead_id)


async def convert_workspace_lead(
    db: AsyncSession, actor: WorkspaceActor, lead_id: int, request: WorkspaceLeadConvertRequest
) -> WorkspaceLeadConvertResult:
    """一键入库：线索生成正式账号（无微信 openid，绑定线索手机号）并进入公开资料待审。"""
    lead = await _require_scoped_lead(db, actor, lead_id)
    if lead.converted_user_id:
        member = await review_workspace_member(
            db, actor, lead.converted_user_id, WorkspaceMemberReviewUpdate(status="PENDING")
        )
        return WorkspaceLeadConvertResult(
            lead=await _require_scoped_lead(db, actor, lead_id),
            user_id=lead.converted_user_id,
            review_status=member.review_status,
        )
    if lead.status in {"CONVERTED", "CLOSED", "LOST"}:
        raise HTTPException(409, detail="当前状态的客源不能入库")
    if not lead.phone:
        raise HTTPException(409, detail="线索未登记手机号，无法生成账号，请先补充电话")
    review_status = "PENDING"
    async with db.begin_nested():
        result = await db.execute(
            text(
                """INSERT INTO users
                (openid, phone, phone_verified_at, nickname, gender, status, data_complete_rate, register_ip)
                VALUES (:openid, :phone, UTC_TIMESTAMP(), :nickname, :gender, 1, 0, '127.0.0.1')"""
            ),
            {
                "openid": f"lead-convert-{lead_id}",
                "phone": lead.phone,
                "nickname": lead.name,
                "gender": request.gender,
            },
        )
        user_id = int(result.lastrowid)
        await db.execute(
            text(
                """INSERT INTO matchmaker_member_review (user_id, status, reviewed_by)
                VALUES (:user_id, 'PENDING', NULL)
                ON DUPLICATE KEY UPDATE updated_at = UTC_TIMESTAMP()"""
            ),
            {"user_id": user_id},
        )
        await db.execute(
            text(
                """INSERT INTO resource_assignment
                (user_id, organization_id, matchmaker_id, source, status, assigned_by)
                VALUES (:user_id, :organization_id, :matchmaker_id, 'lead_convert', 1, :assigned_by)"""
            ),
            {
                "user_id": user_id,
                "organization_id": actor.organization_id,
                "matchmaker_id": lead.matchmaker_id or actor.user_id,
                "assigned_by": actor.user_id,
            },
        )
        await db.execute(
            text(
                """UPDATE customer_lead SET status = 'CONVERTED', converted_user_id = :user_id,
                updated_at = UTC_TIMESTAMP() WHERE id = :lead_id"""
            ),
            {"user_id": user_id, "lead_id": lead_id},
        )
    await _audit(
        db,
        actor.user_id,
        "matchmaker_workspace.lead.convert",
        "customer_lead",
        lead_id,
        f"一键入库生成用户 {user_id}",
    )
    await db.commit()
    return WorkspaceLeadConvertResult(
        lead=await _require_scoped_lead(db, actor, lead_id), user_id=user_id, review_status=review_status
    )


async def _require_scoped_member(db: AsyncSession, actor: WorkspaceActor, member_id: int) -> None:
    params: dict[str, object] = {"member_id": member_id}
    scope = _member_scope_predicate(actor, params)
    exists = await db.scalar(
        text(f"SELECT 1 FROM users u WHERE u.id = :member_id AND {scope} LIMIT 1"), params
    )
    if not exists:
        raise HTTPException(404, detail="会员不存在或不在当前工作范围")


async def list_workspace_members(
    db: AsyncSession,
    actor: WorkspaceActor,
    page: int,
    page_size: int,
    review_status: str | None,
    search: str | None,
) -> WorkspaceMemberPage:
    params: dict[str, object] = {"limit": page_size, "offset": (page - 1) * page_size}
    where = [_member_scope_predicate(actor, params)]
    if review_status:
        where.append("COALESCE(mr.status, 'PENDING') = :review_status")
        params["review_status"] = review_status
    if search:
        where.append(
            "(u.nickname LIKE CONCAT('%', :search, '%') OR CAST(u.id AS CHAR) LIKE CONCAT('%', :search, '%')"
            " OR REPLACE(COALESCE(u.phone, ''), '-', '') LIKE CONCAT('%', REPLACE(:search, '-', ''), '%'))"
        )
        params["search"] = search
    clause = " AND ".join(where)
    base = """FROM users u
        LEFT JOIN user_profile p ON p.user_id = u.id
        LEFT JOIN user_auth ua ON ua.user_id = u.id
        LEFT JOIN user_profile_completion pc ON pc.user_id = u.id
        LEFT JOIN matchmaker_member_review mr ON mr.user_id = u.id
        LEFT JOIN (SELECT user_id, MAX(next_follow_at) AS next_follow_at FROM member_follow_up GROUP BY user_id) mf ON mf.user_id = u.id
        LEFT JOIN (SELECT user_id, MAX(id) AS max_assignment_id FROM resource_assignment WHERE status = 1 GROUP BY user_id) la ON la.user_id = u.id
        LEFT JOIN resource_assignment ra ON ra.id = la.max_assignment_id
        LEFT JOIN matchmaker_workspace_profile wp ON wp.user_id = ra.matchmaker_id AND wp.status = 1
        LEFT JOIN users mu ON mu.id = ra.matchmaker_id"""
    rows = await db.execute(
        text(
            f"""SELECT u.id AS user_id, COALESCE(u.nickname, '未命名会员') AS nickname, u.avatar,
            u.gender, u.birthday, p.hometown, p.residence, ua.education, ua.job,
            u.is_married, p.height, COALESCE(ua.realname_status, 0) AS realname_status,
            ra.matchmaker_id AS matchmaker_id,
            COALESCE(wp.display_name, mu.nickname) AS matchmaker_name,
            COALESCE(pc.score, 0) AS profile_completion_score,
            COALESCE(mr.status, 'PENDING') AS review_status, mr.reason AS review_reason,
            mr.reviewed_at, mf.next_follow_at, u.created_at {base}
            WHERE {clause} ORDER BY u.id DESC LIMIT :limit OFFSET :offset"""
        ),
        params,
    )
    count_params = {key: value for key, value in params.items() if key not in {"limit", "offset"}}
    total = int(await db.scalar(text(f"SELECT COUNT(*) {base} WHERE {clause}"), count_params) or 0)
    return WorkspaceMemberPage(
        items=[WorkspaceMember(**dict(row)) for row in rows.mappings().all()],
        page=page,
        page_size=page_size,
        total=total,
        has_more=_page(total, page, page_size),
    )


async def review_workspace_member(
    db: AsyncSession,
    actor: WorkspaceActor,
    member_id: int,
    request: WorkspaceMemberReviewUpdate,
) -> WorkspaceMember:
    await _require_scoped_member(db, actor, member_id)
    await db.execute(
        text(
            """INSERT INTO matchmaker_member_review
            (user_id, status, reason, reviewed_by, reviewed_at)
            VALUES (:user_id, :status, :reason, :reviewed_by, UTC_TIMESTAMP())
            ON DUPLICATE KEY UPDATE status = VALUES(status), reason = VALUES(reason),
            reviewed_by = VALUES(reviewed_by), reviewed_at = UTC_TIMESTAMP(), updated_at = UTC_TIMESTAMP()"""
        ),
        {
            "user_id": member_id,
            "status": request.status,
            "reason": request.reason if request.status == "REJECTED" else None,
            "reviewed_by": actor.user_id,
        },
    )
    await _audit(
        db,
        actor.user_id,
        "matchmaker_workspace.member.public_profile_review",
        "matchmaker_member_review",
        member_id,
        request.reason,
    )
    await db.commit()
    page = await list_workspace_members(db, actor, 1, 1, None, str(member_id))
    for member in page.items:
        if member.user_id == member_id:
            return member
    raise HTTPException(404, detail="会员不存在或不在当前工作范围")


async def create_member_follow_up(
    db: AsyncSession,
    actor: WorkspaceActor,
    member_id: int,
    request: WorkspaceMemberFollowUpCreate,
) -> WorkspaceMemberFollowUp:
    await _require_scoped_member(db, actor, member_id)
    result = await db.execute(
        text(
            """INSERT INTO member_follow_up (user_id, method, content, next_follow_at, created_by)
            VALUES (:user_id, :method, :content, :next_follow_at, :created_by)"""
        ),
        {**request.model_dump(), "user_id": member_id, "created_by": actor.user_id},
    )
    follow_up_id = int(result.lastrowid)
    await _audit(
        db,
        actor.user_id,
        "matchmaker_workspace.member.follow_up.create",
        "member_follow_up",
        follow_up_id,
    )
    await db.commit()
    row = (
        await db.execute(
            text(
                """SELECT id, user_id, method, content, next_follow_at, created_at
                FROM member_follow_up WHERE id = :follow_up_id"""
            ),
            {"follow_up_id": follow_up_id},
        )
    ).mappings().one()
    return WorkspaceMemberFollowUp(**dict(row))


async def list_member_follow_ups(
    db: AsyncSession, actor: WorkspaceActor, member_id: int, page: int, page_size: int
) -> WorkspaceMemberFollowUpPage:
    await _require_scoped_member(db, actor, member_id)
    params = {"member_id": member_id, "limit": page_size, "offset": (page - 1) * page_size}
    rows = await db.execute(
        text(
            """SELECT id, user_id, method, content, next_follow_at, created_at
            FROM member_follow_up WHERE user_id = :member_id
            ORDER BY id DESC LIMIT :limit OFFSET :offset"""
        ),
        params,
    )
    total = int(
        await db.scalar(text("SELECT COUNT(*) FROM member_follow_up WHERE user_id = :member_id"), {"member_id": member_id})
        or 0
    )
    return WorkspaceMemberFollowUpPage(
        items=[WorkspaceMemberFollowUp(**dict(row)) for row in rows.mappings().all()],
        page=page,
        page_size=page_size,
        total=total,
        has_more=_page(total, page, page_size),
    )


async def get_member_contact(
    db: AsyncSession, actor: WorkspaceActor, member_id: int
) -> WorkspaceMemberContact:
    await _require_scoped_member(db, actor, member_id)
    phone = await db.scalar(text("SELECT phone FROM users WHERE id = :member_id"), {"member_id": member_id})
    await _audit(db, actor.user_id, "matchmaker_workspace.member.contact.read", "user", member_id)
    await db.commit()
    return WorkspaceMemberContact(user_id=member_id, phone=str(phone) if phone else None)


async def list_match_candidates(
    db: AsyncSession,
    actor: WorkspaceActor,
    member_id: int,
    page: int,
    page_size: int,
) -> WorkspaceMatchCandidatePage:
    await _require_scoped_member(db, actor, member_id)
    gender = await db.scalar(text("SELECT gender FROM users WHERE id = :member_id"), {"member_id": member_id})
    params: dict[str, object] = {"member_id": member_id, "limit": page_size, "offset": (page - 1) * page_size}
    scope = _member_scope_predicate(actor, params)
    gender_clause = "" if gender not in (1, 2) else "AND u.gender <> :gender"
    if gender in (1, 2):
        params["gender"] = gender
    base = """FROM users u LEFT JOIN user_profile p ON p.user_id = u.id
        LEFT JOIN matchmaker_member_review mr ON mr.user_id = u.id
        LEFT JOIN (SELECT user_id, MAX(id) AS max_assignment_id FROM resource_assignment WHERE status = 1 GROUP BY user_id) ca ON ca.user_id = u.id
        LEFT JOIN resource_assignment cra ON cra.id = ca.max_assignment_id
        LEFT JOIN matchmaker_workspace_profile cwp ON cwp.user_id = cra.matchmaker_id AND cwp.status = 1
        LEFT JOIN users cmu ON cmu.id = cra.matchmaker_id"""
    where = f"u.id <> :member_id AND u.status = 1 AND {scope} AND COALESCE(mr.status, 'PENDING') = 'PASSED' {gender_clause}"
    rows = await db.execute(
        text(
            f"""SELECT u.id AS user_id, COALESCE(u.nickname, '未命名会员') AS nickname,
            u.avatar, u.gender, u.birthday, p.residence,
            COALESCE(cwp.display_name, cmu.nickname) AS matchmaker_name {base} WHERE {where}
            ORDER BY u.id DESC LIMIT :limit OFFSET :offset"""
        ),
        params,
    )
    count_params = {key: value for key, value in params.items() if key not in {"limit", "offset"}}
    total = int(await db.scalar(text(f"SELECT COUNT(*) {base} WHERE {where}"), count_params) or 0)
    pref_row = (
        await db.execute(
            text(
                """SELECT age_min, age_max, height_min, height_max, income_min, education_min
            FROM user_partner_preference WHERE user_id = :member_id LIMIT 1"""
            ),
            {"member_id": member_id},
        )
    ).mappings().first()
    preference = None
    if pref_row:
        preference = MemberPartnerPreference(
            age_min=pref_row["age_min"],
            age_max=pref_row["age_max"],
            height_min=pref_row["height_min"],
            height_max=pref_row["height_max"],
            income_min=float(pref_row["income_min"]) if pref_row["income_min"] is not None else None,
            education_min=pref_row["education_min"],
        )
    return WorkspaceMatchCandidatePage(
        items=[WorkspaceMatchCandidate(**dict(row)) for row in rows.mappings().all()],
        page=page,
        page_size=page_size,
        total=total,
        has_more=_page(total, page, page_size),
        preference=preference,
    )


async def list_match_history(
    db: AsyncSession,
    actor: WorkspaceActor,
    member_id: int,
    page: int,
    page_size: int,
) -> WorkspaceMatchHistoryPage:
    await _require_scoped_member(db, actor, member_id)
    params = {"member_id": member_id, "limit": page_size, "offset": (page - 1) * page_size}
    rows = await db.execute(
        text(
            """SELECT ma.id,
            CASE WHEN ma.from_user_id = :member_id THEN ma.to_user_id ELSE ma.from_user_id END AS counterpart_user_id,
            COALESCE(u.nickname, '未命名会员') AS counterpart_nickname,
            ma.status,
            CASE WHEN ma.from_user_id = :member_id THEN 'OUTGOING' ELSE 'INCOMING' END AS direction,
            ma.created_at, ma.responded_at
            FROM match_apply ma JOIN users u ON u.id = CASE WHEN ma.from_user_id = :member_id THEN ma.to_user_id ELSE ma.from_user_id END
            WHERE ma.from_user_id = :member_id OR ma.to_user_id = :member_id
            ORDER BY ma.id DESC LIMIT :limit OFFSET :offset"""
        ),
        params,
    )
    total = int(
        await db.scalar(
            text("SELECT COUNT(*) FROM match_apply WHERE from_user_id = :member_id OR to_user_id = :member_id"),
            {"member_id": member_id},
        )
        or 0
    )
    return WorkspaceMatchHistoryPage(
        items=[WorkspaceMatchHistoryItem(**dict(row)) for row in rows.mappings().all()],
        page=page,
        page_size=page_size,
        total=total,
        has_more=_page(total, page, page_size),
    )


async def get_workspace_member_note(
    db: AsyncSession, actor: WorkspaceActor, member_id: int
) -> WorkspaceMemberNote:
    """红娘说：读取会员的公开评价（工作台范围内）。"""
    await _require_scoped_member(db, actor, member_id)
    row = (
        await db.execute(
            text("SELECT note, updated_at FROM matchmaker_admin_member_note WHERE user_id = :member_id"),
            {"member_id": member_id},
        )
    ).mappings().first()
    return WorkspaceMemberNote(
        user_id=member_id,
        note=str(row["note"]) if row and row["note"] is not None else "",
        updated_at=row["updated_at"] if row else None,
    )


async def update_workspace_member_note(
    db: AsyncSession, actor: WorkspaceActor, member_id: int, request: WorkspaceMemberNoteUpdate
) -> WorkspaceMemberNote:
    """红娘说：保存会员的公开评价，留审计痕迹。"""
    await _require_scoped_member(db, actor, member_id)
    note = request.note.strip()
    await db.execute(
        text(
            """INSERT INTO matchmaker_admin_member_note (user_id, note, updated_by)
            VALUES (:member_id, :note, :actor_id)
            ON DUPLICATE KEY UPDATE note = VALUES(note), updated_by = VALUES(updated_by)"""
        ),
        {"member_id": member_id, "note": note, "actor_id": actor.user_id},
    )
    await _audit(db, actor.user_id, "matchmaker_workspace.member.note", "user", member_id, note[:120])
    await db.commit()
    return await get_workspace_member_note(db, actor, member_id)


async def list_member_matchmakers(
    db: AsyncSession, actor: WorkspaceActor, member_id: int
) -> list[WorkspaceLeadFollower]:
    """修改红娘：返回当前工作范围内可选的服务红娘（含当前跟进人标记）。"""
    await _require_scoped_member(db, actor, member_id)
    colleagues = await list_workspace_colleagues(db, actor)
    current_id = await db.scalar(
        text(
            """SELECT ra.matchmaker_id FROM resource_assignment ra
            WHERE ra.user_id = :member_id AND ra.status = 1 AND ra.matchmaker_id IS NOT NULL
            ORDER BY ra.id DESC LIMIT 1"""
        ),
        {"member_id": member_id},
    )
    result: list[WorkspaceLeadFollower] = []
    seen: set[int] = set()
    for item in colleagues:
        if item.user_id in seen:
            continue
        seen.add(item.user_id)
        item.is_self = item.user_id == actor.user_id or item.is_self
        result.append(item)
    for row in (
        await db.execute(
            text(
                """SELECT DISTINCT ra.matchmaker_id, COALESCE(wp.display_name, u.nickname, '服务红娘') AS display_name
            FROM resource_assignment ra
            LEFT JOIN matchmaker_workspace_profile wp ON wp.user_id = ra.matchmaker_id AND wp.status = 1
            LEFT JOIN users u ON u.id = ra.matchmaker_id
            WHERE ra.status = 1 AND ra.matchmaker_id IS NOT NULL"""
            )
        )
    ).mappings().all():
        user_id = int(row["matchmaker_id"])
        if user_id not in seen:
            seen.add(user_id)
            result.append(WorkspaceLeadFollower(user_id=user_id, display_name=str(row["display_name"]), is_self=False))
    if current_id is not None and int(current_id) not in seen:
        row = (
            await db.execute(
                text("SELECT COALESCE(nickname, '服务红娘') AS display_name FROM users WHERE id = :id"),
                {"id": int(current_id)},
            )
        ).mappings().first()
        result.append(
            WorkspaceLeadFollower(
                user_id=int(current_id),
                display_name=str(row["display_name"]) if row else "服务红娘",
                is_self=False,
            )
        )
    return result


async def assign_workspace_member_matchmaker(
    db: AsyncSession, actor: WorkspaceActor, member_id: int, request: WorkspaceMemberMatchmakerUpdate
) -> WorkspaceMember:
    """修改红娘：换绑会员的跟进服务红娘；置 null 表示改为待分派并结束现有归属。"""
    await _require_scoped_member(db, actor, member_id)
    target_id = request.matchmaker_id
    if target_id is not None:
        valid = await db.scalar(
            text(
                """SELECT 1 FROM user_role
                WHERE user_id = :id AND role_code = 'service_matchmaker' AND status = 1 LIMIT 1"""
            ),
            {"id": target_id},
        )
        if not valid:
            raise HTTPException(422, detail="指定用户不是有效的服务红娘")
    await db.execute(
        text(
            """UPDATE resource_assignment SET status = 2, ended_at = UTC_TIMESTAMP(), end_reason = 'reassigned'
            WHERE user_id = :member_id AND status = 1"""
        ),
        {"member_id": member_id},
    )
    if target_id is not None:
        await db.execute(
            text(
                """INSERT INTO resource_assignment
                (user_id, organization_id, matchmaker_id, source, status, assigned_by)
                VALUES (:member_id, :organization_id, :matchmaker_id, 'manual', 1, :assigned_by)"""
            ),
            {
                "member_id": member_id,
                "organization_id": actor.organization_id,
                "matchmaker_id": target_id,
                "assigned_by": actor.user_id,
            },
        )
    target_name = "待分派"
    if target_id is not None:
        name_row = (
            await db.execute(
                text("SELECT COALESCE(nickname, '服务红娘') AS display_name FROM users WHERE id = :id"),
                {"id": target_id},
            )
        ).mappings().first()
        target_name = str(name_row["display_name"]) if name_row else f"#{target_id}"
    await _audit(
        db,
        actor.user_id,
        "matchmaker_workspace.member.assign",
        "user",
        member_id,
        f"跟进红娘改为 {target_name}",
    )
    await db.commit()
    page = await list_workspace_members(db, actor, 1, 1000, None, None)
    for item in page.items:
        if item.user_id == member_id:
            return item
    raise HTTPException(404, detail="会员查询失败")


async def _require_role_actor(db: AsyncSession, current: CurrentUser, role_code: str) -> None:
    if not await _has_active_role(db, current.id, role_code):
        raise HTTPException(403, detail="当前账号没有所需业务身份")


async def _latest_team_invite(db: AsyncSession, team_id: int) -> PromotionCode | None:
    row = (
        await db.execute(
            text(
                """SELECT code, created_at FROM promotion_touch
                WHERE partner_team_id = :team_id AND promoter_id IS NULL
                ORDER BY id DESC LIMIT 1"""
            ),
            {"team_id": team_id},
        )
    ).mappings().first()
    return PromotionCode(**dict(row)) if row else None


async def _create_unique_promotion_touch(
    db: AsyncSession, *, promoter_id: int | None, team_id: int | None
) -> PromotionCode:
    for _ in range(5):
        code = token_urlsafe(12).replace("-", "A").replace("_", "B")
        duplicate = await db.scalar(text("SELECT 1 FROM promotion_touch WHERE code = :code"), {"code": code})
        if duplicate:
            continue
        result = await db.execute(
            text(
                """INSERT INTO promotion_touch (code, promoter_id, partner_team_id)
                VALUES (:code, :promoter_id, :team_id)"""
            ),
            {"code": code, "promoter_id": promoter_id, "team_id": team_id},
        )
        row = (
            await db.execute(
                text("SELECT code, created_at FROM promotion_touch WHERE id = :touch_id"),
                {"touch_id": int(result.lastrowid)},
            )
        ).mappings().one()
        return PromotionCode(**dict(row))
    raise HTTPException(503, detail="生成专属码失败，请稍后重试")


# 合伙三级与推广四级阈值：与客户端展示文案一致的静态口径，仅依赖可审计的人数/业绩数据。
_PARTNER_LEVELS: list[tuple[str, int, int]] = [
    ("初级合伙", 0, 0),
    ("中级合伙", 10_000, 100),
    ("战略合伙", 30_000, 500),
]
_PROMOTER_LEVELS: list[tuple[str, int]] = [
    ("初级", 0),
    ("推广大师", 51),
    ("推广大使", 100),
    ("推广天使", 500),
]


def _partner_level_info(effective_member_count: int) -> PartnerLevelInfo:
    """按有效会员数与团队业绩双阈值判定级别；业绩需账务结算支撑，当前以人数为准。"""
    current_name, next_name = _PARTNER_LEVELS[0][0], None
    performance_gap: int | None = None
    member_gap: int | None = None
    for index, (name, perf_target, member_target) in enumerate(_PARTNER_LEVELS):
        if effective_member_count >= member_target:
            current_name = name
            next_name = _PARTNER_LEVELS[index + 1][0] if index + 1 < len(_PARTNER_LEVELS) else None
            if next_name is not None:
                next_target = _PARTNER_LEVELS[index + 1]
                member_gap = max(0, next_target[2] - effective_member_count)
                performance_gap = max(0, next_target[1])
            break
    return PartnerLevelInfo(
        level=current_name,
        team_performance=0,
        effective_member_count=effective_member_count,
        next_level=next_name,
        performance_gap=float(performance_gap) if next_name is not None else None,
        member_gap=member_gap,
    )


def _promoter_level(effective_member_count: int) -> tuple[str, str | None, int | None]:
    current_name, next_name, member_gap = _PROMOTER_LEVELS[0][0], None, None
    for index, (name, threshold) in enumerate(_PROMOTER_LEVELS):
        if effective_member_count >= threshold:
            current_name = name
            next_name = _PROMOTER_LEVELS[index + 1][0] if index + 1 < len(_PROMOTER_LEVELS) else None
            if next_name is not None:
                member_gap = max(0, _PROMOTER_LEVELS[index + 1][1] - effective_member_count)
            break
    return current_name, next_name, member_gap


async def get_partner_center(db: AsyncSession, current: CurrentUser) -> PartnerCenterSnapshot:
    await _require_role_actor(db, current, "partner")
    team = (
        await db.execute(
            text("SELECT id, name FROM partner_team WHERE owner_user_id = :user_id AND status = 1"),
            {"user_id": current.id},
        )
    ).mappings().first()
    user_row = (
        await db.execute(
            text("SELECT COALESCE(nickname, '合伙人') AS nickname, phone, wechat_bound_at, created_at FROM users WHERE id = :user_id"),
            {"user_id": current.id},
        )
    ).mappings().first()
    user_name = str(user_row["nickname"]) if user_row else "合伙人"
    invite = await _latest_team_invite(db, int(team["id"])) if team else None
    team_members: list[PartnerTeamMember] = []
    effective_members: list[PartnerEffectiveMember] = []
    registration_rewards: list[PromoterRewardItem] = []
    if team:
        member_rows = await db.execute(
            text(
                """SELECT pm.promoter_id AS user_id, COALESCE(u.nickname, '推广红娘') AS nickname, pm.joined_at,
                (SELECT COUNT(*) FROM promotion_attribution pa
                    LEFT JOIN matchmaker_member_review mr ON mr.user_id = pa.user_id
                    WHERE pa.promoter_id = pm.promoter_id AND pa.status = 1
                    AND COALESCE(mr.status, 'PENDING') = 'PASSED') AS effective_count
                FROM partner_membership pm JOIN users u ON u.id = pm.promoter_id
                WHERE pm.team_id = :team_id AND pm.status = 1 ORDER BY pm.id DESC"""
            ),
            {"team_id": team["id"]},
        )
        team_members = [PartnerTeamMember(**dict(row)) for row in member_rows.mappings().all()]
        effective_rows = await db.execute(
            text(
                """SELECT pa.user_id, COALESCE(u.nickname, '未命名会员') AS nickname, u.gender, u.avatar,
                u.created_at AS register_at, COALESCE(pu.nickname, '推广红娘') AS promoter_name
                FROM promotion_attribution pa
                JOIN partner_membership pm ON pm.promoter_id = pa.promoter_id AND pm.team_id = :team_id AND pm.status = 1
                JOIN users u ON u.id = pa.user_id
                JOIN users pu ON pu.id = pa.promoter_id
                LEFT JOIN matchmaker_member_review mr ON mr.user_id = pa.user_id
                WHERE pa.status = 1 AND COALESCE(mr.status, 'PENDING') = 'PASSED'
                ORDER BY pa.id DESC"""
            ),
            {"team_id": team["id"]},
        )
        effective_members = [PartnerEffectiveMember(**dict(row)) for row in effective_rows.mappings().all()]
        # 注册奖励与推广红娘中心同口径：审核通过的有效会员按 1 元/人展示，仅作展示口径，不构成资金账务。
        for member in effective_members:
            registration_rewards.append(
                PromoterRewardItem(
                    user_id=member.user_id,
                    nickname=member.nickname,
                    gender=member.gender,
                    avatar=member.avatar,
                    register_at=member.register_at,
                    review_status="PASSED",
                    reward_amount=1,
                )
            )
    level_info = _partner_level_info(len(effective_members))
    return PartnerCenterSnapshot(
        profile=PartnerCenterProfile(
            nickname=user_name,
            team_name=str(team["name"]) if team else None,
            invite_code=invite.code if invite else None,
            phone=str(user_row["phone"]) if user_row and user_row["phone"] else None,
            wechat_bound=bool(user_row["wechat_bound_at"]) if user_row else False,
            register_at=user_row["created_at"] if user_row else None,
        ),
        team_members=team_members,
        effective_members=effective_members,
        registration_rewards=registration_rewards,
        level=level_info,
    )


async def update_partner_team(
    db: AsyncSession, current: CurrentUser, request: PartnerTeamUpdate
) -> PartnerCenterSnapshot:
    await _require_role_actor(db, current, "partner")
    team = await db.scalar(text("SELECT id FROM partner_team WHERE owner_user_id = :user_id FOR UPDATE"), {"user_id": current.id})
    if team:
        await db.execute(text("UPDATE partner_team SET name = :name, updated_at = UTC_TIMESTAMP() WHERE id = :team_id"), {"name": request.name, "team_id": team})
        team_id = int(team)
    else:
        result = await db.execute(
            text("INSERT INTO partner_team (owner_user_id, name, open_mode) VALUES (:user_id, :name, 'manual')"),
            {"user_id": current.id, "name": request.name},
        )
        team_id = int(result.lastrowid)
    await _audit(db, current.id, "matchmaker_workspace.partner.team.upsert", "partner_team", team_id)
    await db.commit()
    return await get_partner_center(db, current)


async def create_partner_invite(db: AsyncSession, current: CurrentUser) -> PromotionCode:
    await _require_role_actor(db, current, "partner")
    team_id = await db.scalar(text("SELECT id FROM partner_team WHERE owner_user_id = :user_id AND status = 1"), {"user_id": current.id})
    if not team_id:
        raise HTTPException(409, detail="请先创建合伙团队")
    invite = await _create_unique_promotion_touch(db, promoter_id=None, team_id=int(team_id))
    await _audit(db, current.id, "matchmaker_workspace.partner.invite.create", "promotion_touch", None)
    await db.commit()
    return invite


_JOIN_REQUEST_SELECT = """SELECT jr.id, jr.team_id, COALESCE(pt.name, '合伙团队') AS team_name,
    jr.promoter_id, COALESCE(u.nickname, '推广红娘') AS promoter_nickname, u.avatar AS promoter_avatar,
    jr.status, jr.created_at, jr.reviewed_at, jr.reject_reason
    FROM partner_join_request jr
    LEFT JOIN partner_team pt ON pt.id = jr.team_id
    JOIN users u ON u.id = jr.promoter_id"""


def _join_request_from_row(row: object) -> PartnerJoinRequestSummary:
    values = dict(row)  # type: ignore[arg-type]
    return PartnerJoinRequestSummary(
        id=int(values["id"]),
        team_id=int(values["team_id"]),
        team_name=str(values["team_name"]),
        promoter_id=int(values["promoter_id"]),
        promoter_nickname=str(values["promoter_nickname"]),
        promoter_avatar=str(values["promoter_avatar"]) if values.get("promoter_avatar") else None,
        status=str(values["status"]),
        created_at=values["created_at"],
        reviewed_at=values["reviewed_at"],
        reject_reason=values["reject_reason"],
    )


async def submit_partner_join_request(
    db: AsyncSession, current: CurrentUser, body: PartnerJoinRequestCreate
) -> PartnerJoinRequestSummary:
    """推广红娘凭团队邀请码提交入团申请；PENDING 存续期间不得重复申请。"""
    await _require_role_actor(db, current, "promoter")
    code = body.invite_code.strip()
    touch = (
        await db.execute(
            text(
                """SELECT id, partner_team_id FROM promotion_touch
                WHERE code = :code AND promoter_id IS NULL"""
            ),
            {"code": code},
        )
    ).mappings().first()
    if touch is None or touch["partner_team_id"] is None:
        raise HTTPException(404, detail="邀请码无效或不是团队邀请码")
    team_id = int(touch["partner_team_id"])
    team = (
        await db.execute(text("SELECT id, name, status FROM partner_team WHERE id = :id"), {"id": team_id})
    ).mappings().first()
    if team is None or team["status"] != 1:
        raise HTTPException(409, detail="合伙团队不存在或已关闭")
    active_membership = await db.execute(
        text("SELECT id FROM partner_membership WHERE promoter_id = :pid AND status = 1"),
        {"pid": current.id},
    )
    if active_membership.scalar():
        raise HTTPException(409, detail="您已加入团队，不能重复申请")
    duplicate = await db.execute(
        text(
            """SELECT id FROM partner_join_request
            WHERE promoter_id = :pid AND status = 'PENDING' FOR UPDATE"""
        ),
        {"pid": current.id},
    )
    if duplicate.scalar():
        raise HTTPException(409, detail="已有一份待确认的入团申请")
    result = await db.execute(
        text(
            """INSERT INTO partner_join_request (team_id, promoter_id, invite_code)
            VALUES (:team_id, :pid, :code)"""
        ),
        {"team_id": team_id, "pid": current.id, "code": code},
    )
    request_id = int(result.lastrowid)
    await _audit(db, current.id, "matchmaker_workspace.partner_join.submit", "partner_join_request", request_id, f"team={team_id}")
    await db.commit()
    row = (
        await db.execute(text(f"{_JOIN_REQUEST_SELECT} WHERE jr.id = :id"), {"id": request_id})
    ).mappings().one()
    return _join_request_from_row(row)


async def cancel_partner_join_request(db: AsyncSession, current: CurrentUser, request_id: int) -> None:
    """推广红娘撤回自己的待确认申请。"""
    row = (
        await db.execute(
            text("SELECT id, status FROM partner_join_request WHERE id = :id AND promoter_id = :pid FOR UPDATE"),
            {"id": request_id, "pid": current.id},
        )
    ).first()
    if row is None:
        raise HTTPException(404, detail="入团申请不存在")
    if row.status != "PENDING":
        raise HTTPException(409, detail="该申请已处理，不能撤回")
    await db.execute(
        text("UPDATE partner_join_request SET status = 'CANCELLED', reviewed_at = UTC_TIMESTAMP(), updated_at = UTC_TIMESTAMP() WHERE id = :id"),
        {"id": request_id},
    )
    await _audit(db, current.id, "matchmaker_workspace.partner_join.cancel", "partner_join_request", request_id, "推广红娘撤回申请")
    await db.commit()


async def list_partner_join_requests(
    db: AsyncSession, current: CurrentUser, status: str | None
) -> list[PartnerJoinRequestSummary]:
    """合伙人查看自己团队的入团申请。"""
    await _require_role_actor(db, current, "partner")
    team_id = await db.scalar(
        text("SELECT id FROM partner_team WHERE owner_user_id = :user_id AND status = 1"),
        {"user_id": current.id},
    )
    if not team_id:
        return []
    params: dict[str, object] = {"team_id": team_id}
    where = "WHERE jr.team_id = :team_id AND jr.status = :status" if status else "WHERE jr.team_id = :team_id"
    if status:
        params["status"] = status
    rows = await db.execute(
        text(f"{_JOIN_REQUEST_SELECT} {where} ORDER BY jr.id DESC LIMIT 200"),
        params,
    )
    return [_join_request_from_row(row) for row in rows.mappings().all()]


async def review_partner_join_request(
    db: AsyncSession, current: CurrentUser, request_id: int, body: PartnerJoinRequestReview
) -> PartnerJoinRequestSummary:
    """合伙人审批入团申请；通过时写入 partner_membership 并标记其余申请失效。"""
    await _require_role_actor(db, current, "partner")
    team_id = await db.scalar(
        text("SELECT id FROM partner_team WHERE owner_user_id = :user_id AND status = 1 FOR UPDATE"),
        {"user_id": current.id},
    )
    if not team_id:
        raise HTTPException(409, detail="请先创建合伙团队")
    row = (
        await db.execute(
            text("SELECT id, team_id, promoter_id, status FROM partner_join_request WHERE id = :id FOR UPDATE"),
            {"id": request_id},
        )
    ).mappings().first()
    if row is None or int(row["team_id"]) != int(team_id):
        raise HTTPException(404, detail="入团申请不存在或不在您的团队")
    if row["status"] != "PENDING":
        raise HTTPException(409, detail="该申请已处理")
    promoter_id = int(row["promoter_id"])
    if body.status == "APPROVED":
        active_membership = await db.execute(
            text("SELECT id FROM partner_membership WHERE promoter_id = :pid AND status = 1"),
            {"pid": promoter_id},
        )
        if active_membership.scalar():
            raise HTTPException(409, detail="该推广红娘已加入其他团队")
        await db.execute(
            text("INSERT INTO partner_membership (team_id, promoter_id) VALUES (:team_id, :pid)"),
            {"team_id": team_id, "pid": promoter_id},
        )
    await db.execute(
        text(
            """UPDATE partner_join_request SET status = :status, reject_reason = :reason,
            reviewed_by = :reviewer, reviewed_at = UTC_TIMESTAMP(), updated_at = UTC_TIMESTAMP()
            WHERE id = :id"""
        ),
        {
            "status": body.status,
            "reason": body.reason.strip() if body.reason else None,
            "reviewer": current.id,
            "id": request_id,
        },
    )
    await _audit(db, current.id, "matchmaker_workspace.partner_join.review", "partner_join_request", request_id, body.status)
    await db.commit()
    result = (
        await db.execute(text(f"{_JOIN_REQUEST_SELECT} WHERE jr.id = :id"), {"id": request_id})
    ).mappings().one()
    return _join_request_from_row(result)


async def create_team_qr_code(db: AsyncSession, current: CurrentUser) -> dict[str, str]:
    """生成团队邀请小程序码（getwxacodeunlimit）；微信能力未配置时返回 503 降级。"""
    await _require_role_actor(db, current, "partner")
    if not settings.wechat_app_id or not settings.wechat_app_secret:
        raise HTTPException(503, detail="微信小程序码服务未配置")
    team_id = await db.scalar(
        text("SELECT id FROM partner_team WHERE owner_user_id = :user_id AND status = 1"),
        {"user_id": current.id},
    )
    if not team_id:
        raise HTTPException(409, detail="请先创建合伙团队")
    invite = await _latest_team_invite(db, int(team_id))
    if invite is None:
        invite = await create_partner_invite(db, current)
    scene = f"team={int(team_id)}&code={invite.code}"
    async with httpx.AsyncClient(timeout=10) as client:
        token_response = await client.get(
            "https://api.weixin.qq.com/cgi-bin/token",
            params={
                "grant_type": "client_credential",
                "appid": settings.wechat_app_id,
                "secret": settings.wechat_app_secret,
            },
        )
        token_data = token_response.json()
        if token_data.get("errcode") or not token_data.get("access_token"):
            raise HTTPException(503, detail="微信访问令牌获取失败")
        qr_response = await client.post(
            f"https://api.weixin.qq.com/wxa/getwxacodeunlimit?access_token={token_data['access_token']}",
            json={"scene": scene, "page": settings.wechat_mini_program_page, "check_path": False, "env_version": "release"},
        )
    if "image" not in qr_response.headers.get("content-type", ""):
        raise HTTPException(503, detail="微信小程序码生成失败")
    await _audit(db, current.id, "matchmaker_workspace.partner.team_qrcode", "partner_team", int(team_id))
    await db.commit()
    return {"code": invite.code, "image": "data:image/png;base64," + base64.b64encode(qr_response.content).decode("ascii")}


async def _list_promoter_leads(db: AsyncSession, user_id: int) -> list[WorkspaceLead]:
    rows = await db.execute(
        text(
            """SELECT id, name, phone, wechat, source, intention_level, status,
            next_follow_at, created_at, updated_at FROM customer_lead
            WHERE created_by = :user_id ORDER BY id DESC"""
        ),
        {"user_id": user_id},
    )
    return [_lead_from_row(row) for row in rows.mappings().all()]


async def get_promoter_center(db: AsyncSession, current: CurrentUser) -> PromoterCenterSnapshot:
    await _require_role_actor(db, current, "promoter")
    user_row = (
        await db.execute(
            text("SELECT COALESCE(nickname, '推广红娘') AS nickname, phone, wechat_bound_at, created_at FROM users WHERE id = :user_id"),
            {"user_id": current.id},
        )
    ).mappings().first()
    promoter_name = str(user_row["nickname"]) if user_row else "推广红娘"
    code_row = (
        await db.execute(
            text(
                """SELECT code FROM promotion_touch WHERE promoter_id = :user_id
                ORDER BY id DESC LIMIT 1"""
            ),
            {"user_id": current.id},
        )
    ).mappings().first()
    rows = await db.execute(
        text(
            """SELECT pa.user_id, COALESCE(u.nickname, '未命名会员') AS nickname, u.gender,
            u.created_at AS register_at, COALESCE(mr.status, 'PENDING') AS review_status
            FROM promotion_attribution pa JOIN users u ON u.id = pa.user_id
            LEFT JOIN matchmaker_member_review mr ON mr.user_id = pa.user_id
            WHERE pa.promoter_id = :user_id AND pa.status = 1 ORDER BY pa.id DESC"""
        ),
        {"user_id": current.id},
    )
    members = [PromoterMember(**dict(row)) for row in rows.mappings().all()]
    # 注册奖励时间线：与成员同源；审核通过按 1 元/人计奖励，未通过/待审为 0。
    reward_rows = await db.execute(
        text(
            """SELECT pa.user_id, COALESCE(u.nickname, '未命名会员') AS nickname, u.gender, u.avatar,
            u.created_at AS register_at, COALESCE(mr.status, 'PENDING') AS review_status
            FROM promotion_attribution pa JOIN users u ON u.id = pa.user_id
            LEFT JOIN matchmaker_member_review mr ON mr.user_id = pa.user_id
            WHERE pa.promoter_id = :user_id AND pa.status = 1 ORDER BY u.created_at DESC, pa.id DESC"""
        ),
        {"user_id": current.id},
    )
    rewards = []
    for reward in reward_rows.mappings().all():
        review_status = str(reward["review_status"])
        rewards.append(PromoterRewardItem(
            user_id=int(reward["user_id"]),
            nickname=str(reward["nickname"]),
            gender=int(reward["gender"]) if reward["gender"] in {1, 2} else None,
            avatar=reward.get("avatar"),
            register_at=reward["register_at"],
            review_status=review_status,
            reward_amount=1 if review_status == "PASSED" else 0,
        ))
    effective_members = [member for member in members if member.review_status == "PASSED"]
    level_name, next_level_name, member_gap = _promoter_level(len(effective_members))
    membership = (
        await db.execute(
            text(
                """SELECT pm.status, pt.name AS team_name FROM partner_membership pm
                JOIN partner_team pt ON pt.id = pm.team_id
                WHERE pm.promoter_id = :user_id AND pm.status = 1 LIMIT 1"""
            ),
            {"user_id": current.id},
        )
    ).mappings().first()
    pending_join = (
        await db.execute(
            text(
                """SELECT jr.id, pt.name AS team_name FROM partner_join_request jr
                JOIN partner_team pt ON pt.id = jr.team_id
                WHERE jr.promoter_id = :user_id AND jr.status = 'PENDING' ORDER BY jr.id DESC LIMIT 1"""
            ),
            {"user_id": current.id},
        )
    ).mappings().first()
    if membership is not None:
        team_status = "ACTIVE"
        team_name = str(membership["team_name"])
        pending_request_id = None
    elif pending_join is not None:
        team_status = "PENDING"
        team_name = str(pending_join["team_name"])
        pending_request_id = int(pending_join["id"])
    else:
        team_status = "NONE"
        team_name = None
        pending_request_id = None
    return PromoterCenterSnapshot(
        profile=PromoterCenterProfile(
            nickname=promoter_name,
            promotion_code=str(code_row["code"]) if code_row else None,
            phone=str(user_row["phone"]) if user_row and user_row["phone"] else None,
            wechat_bound=bool(user_row["wechat_bound_at"]) if user_row else False,
            register_at=user_row["created_at"] if user_row else None,
            team_name=team_name,
            team_status=team_status,
            pending_request_id=pending_request_id,
            level=level_name,
            next_level=next_level_name,
            member_gap=member_gap,
        ),
        leads=await _list_promoter_leads(db, current.id),
        effective_members=effective_members,
        incomplete_members=[member for member in members if member.review_status != "PASSED"],
        rewards=rewards,
    )


async def create_promoter_lead(
    db: AsyncSession, current: CurrentUser, request: PromoterLeadCreate
) -> WorkspaceLead:
    await _require_role_actor(db, current, "promoter")
    phone = normalize_contact(request.phone)
    wechat = normalize_contact(request.wechat)
    await ensure_contact_available(db, phone, wechat)
    try:
        result = await db.execute(
            text(
                """INSERT INTO customer_lead
                (name, phone, wechat, source, intention_level, remark, created_by)
                VALUES (:name, :phone, :wechat, :source, 1, :remark, :created_by)"""
            ),
            {**request.model_dump(), "phone": phone, "wechat": wechat, "created_by": current.id},
        )
        lead_id = int(result.lastrowid)
        await _audit(db, current.id, "matchmaker_workspace.promoter.lead.create", "customer_lead", lead_id)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise_duplicate_contact()
    row = (
        await db.execute(
            text(
                """SELECT id, name, phone, wechat, source, intention_level, status,
                next_follow_at, created_at, updated_at FROM customer_lead WHERE id = :lead_id"""
            ),
            {"lead_id": lead_id},
        )
    ).mappings().one()
    return _lead_from_row(row)


async def create_promoter_code(db: AsyncSession, current: CurrentUser) -> PromotionCode:
    await _require_role_actor(db, current, "promoter")
    code = await _create_unique_promotion_touch(db, promoter_id=current.id, team_id=None)
    await _audit(db, current.id, "matchmaker_workspace.promoter.code.create", "promotion_touch", None)
    await db.commit()
    return code


NEWLINE = chr(10)


async def create_workspace_member(
    db: AsyncSession, current: CurrentUser, body: WorkspaceMemberEntryCreate
) -> WorkspaceMemberEntryResult:
    """服务红娘录入会员：建基础账号、挂待审记录并分派到录入人名下。"""
    await require_workspace(db, current)
    duplicate = await db.execute(text("SELECT id FROM users WHERE phone = :phone"), {"phone": body.phone})
    if duplicate.scalar():
        raise HTTPException(409, detail="手机号已注册")
    values = body.model_dump(exclude_none=True)
    remark = values.pop("remark", None)
    user_values = {key: values.pop(key) for key in ("phone", "nickname", "gender", "birthday", "is_married") if key in values}
    user_values["status"] = 1
    auth_values = {key: values.pop(key) for key in ("education", "job", "real_name") if key in values}
    if "tags" in values and values["tags"]:
        values["tags"] = json.dumps({"custom": [t for t in str(values["tags"]).split("|") if t]}, ensure_ascii=False)
    # drinking/religion/marriage_plan 等历史字段在部分库中不存在，随 remark 保留信息。
    fallback_remark_parts = []
    for key in ("drinking", "religion", "marriage_plan"):
        if key in values:
            value = values.pop(key)
            if value:
                fallback_remark_parts.append(f"{key}: {value}")
    if fallback_remark_parts:
        remark = ((remark + NEWLINE) if remark else "") + NEWLINE.join(fallback_remark_parts)
    if values:
        profile_columns = {
            str(row[0])
            for row in (
                await db.execute(text(
                    "SELECT COLUMN_NAME FROM information_schema.COLUMNS"
                    " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'user_profile'"
                ))
            ).all()
        }
        missing = set(values) - profile_columns
        if missing:
            raise HTTPException(503, detail="数据库缺少会员资料字段，请先重启服务完成数据库结构迁移")
    result = await db.execute(text(
        "INSERT INTO users (" + ", ".join(user_values) + ", created_at, updated_at)"
        " VALUES (" + ", ".join(":" + key for key in user_values) + ", UTC_TIMESTAMP(), UTC_TIMESTAMP())"
    ), user_values)
    member_id = int(result.lastrowid)
    if values:
        columns = ["user_id", *values]
        updates = ", ".join(f"{key} = VALUES({key})" for key in values)
        await db.execute(text(
            f"INSERT INTO user_profile ({', '.join(columns)})"
            f" VALUES ({', '.join(':' + key for key in columns)})"
            f" ON DUPLICATE KEY UPDATE {updates}"
        ), {"user_id": member_id, **values})
    if auth_values:
        await db.execute(text(
            f"INSERT INTO user_auth (user_id, {', '.join(auth_values)})"
            f" VALUES (:user_id, {', '.join(':' + key for key in auth_values)})"
            f" ON DUPLICATE KEY UPDATE {', '.join(f'{key} = VALUES({key})' for key in auth_values)}"
        ), {"user_id": member_id, **auth_values})
    await db.execute(text(
        "INSERT INTO matchmaker_member_review (user_id, status) VALUES (:user_id, 'PENDING')"
        " ON DUPLICATE KEY UPDATE status = 'PENDING', reviewed_at = NULL, reviewed_by = NULL"
    ), {"user_id": member_id})
    actor = await require_workspace(db, current)
    organization_id = actor.organization_id
    await db.execute(text(
        "INSERT INTO resource_assignment (user_id, organization_id, matchmaker_id, source, status, assigned_by)"
        " VALUES (:user_id, :organization_id, :matchmaker_id, 'entry', 1, :assigned_by)"
    ), {
        "user_id": member_id,
        "organization_id": organization_id,
        "matchmaker_id": current.id,
        "assigned_by": current.id,
    })
    if remark:
        await db.execute(text(
            "INSERT INTO matchmaker_admin_member_note (user_id, note, updated_by)"
            " VALUES (:user_id, :note, :updated_by)"
            " ON DUPLICATE KEY UPDATE note = VALUES(note), updated_by = VALUES(updated_by),"
            " updated_at = UTC_TIMESTAMP()"
        ), {"user_id": member_id, "note": remark, "updated_by": current.id})
    await _audit(db, current.id, "matchmaker_workspace.member.entry_create", "user", member_id)
    await db.commit()
    return WorkspaceMemberEntryResult(
        member_id=member_id, review_status="PENDING", matchmaker_id=current.id
    )


async def update_workspace_meeting_status(
    db: AsyncSession, actor: WorkspaceActor, request_id: int, status: str
) -> None:
    params: dict[str, object] = {"id": request_id}
    clause = _scope_predicate(actor, "mr", params)
    row = await db.execute(text(f"SELECT id FROM meeting_request mr WHERE mr.id = :id AND {clause}"), params)
    if not row.scalar():
        raise HTTPException(404, detail="约见申请不存在或不在服务范围")
    await db.execute(
        text("UPDATE meeting_request SET status = :status, updated_at = UTC_TIMESTAMP() WHERE id = :id"),
        {"id": request_id, "status": status},
    )
    await _audit(db, actor.user_id, "matchmaker_workspace.meeting.status", "meeting_request", request_id, status)
    await db.commit()


async def delete_workspace_meeting_request(
    db: AsyncSession, actor: WorkspaceActor, request_id: int
) -> None:
    params: dict[str, object] = {"id": request_id}
    clause = _scope_predicate(actor, "mr", params)
    row = await db.execute(text(f"SELECT id FROM meeting_request mr WHERE mr.id = :id AND {clause}"), params)
    if not row.scalar():
        raise HTTPException(404, detail="约见申请不存在或不在服务范围")
    await db.execute(text("DELETE FROM meeting_record WHERE request_id = :id"), {"id": request_id})
    await db.execute(text("DELETE FROM meeting_request WHERE id = :id"), {"id": request_id})
    await _audit(db, actor.user_id, "matchmaker_workspace.meeting.delete", "meeting_request", request_id, "删除约见申请记录")
    await db.commit()


async def schedule_workspace_meeting(
    db: AsyncSession, actor: WorkspaceActor, request_id: int, body: WorkspaceMeetingScheduleCreate
) -> WorkspaceMeetingRecordSummary:
    """为双方接受的约见申请创建约见安排；同一申请同时只允许一份进行中的安排。"""
    params: dict[str, object] = {"id": request_id}
    clause = _scope_predicate(actor, "mr", params)
    request_row = await db.execute(
        text(f"SELECT id, status FROM meeting_request mr WHERE mr.id = :id AND {clause}"), params
    )
    current = request_row.first()
    if current is None:
        raise HTTPException(404, detail="约见申请不存在或不在服务范围")
    if current.status != "ACCEPTED":
        raise HTTPException(409, detail="只有双方接受的约见申请才能安排约见")
    active_count = int(
        await db.scalar(
            text(
                """SELECT COUNT(*) FROM meeting_record WHERE request_id = :id
                AND status IN ('SCHEDULED', 'REMINDED', 'CHECKED_IN')"""
            ),
            {"id": request_id},
        )
        or 0
    )
    if active_count > 0:
        raise HTTPException(409, detail="该申请已有进行中的约见安排")
    insert_result = await db.execute(
        text(
            """INSERT INTO meeting_record
            (request_id, organizer_id, organization_id, scheduled_at, location)
            VALUES (:request_id, :organizer_id, :organization_id, :scheduled_at, :location)"""
        ),
        {
            "request_id": request_id,
            "organizer_id": actor.user_id,
            "organization_id": actor.organization_id,
            "scheduled_at": body.scheduled_at,
            "location": body.location.strip(),
        },
    )
    meeting_id = int(insert_result.lastrowid)
    await _audit(db, actor.user_id, "matchmaker_workspace.meeting.schedule", "meeting_record", meeting_id, "工作台安排约见")
    await db.commit()
    record = (
        await db.execute(
            text("SELECT id, status, scheduled_at, location, cancel_reason FROM meeting_record WHERE id = :id"),
            {"id": meeting_id},
        )
    ).mappings().one()
    return WorkspaceMeetingRecordSummary(
        id=int(record["id"]),
        status=str(record["status"]),
        scheduled_at=record["scheduled_at"],
        location=str(record["location"]),
        cancel_reason=record["cancel_reason"],
    )


async def update_workspace_meeting_record_status(
    db: AsyncSession, actor: WorkspaceActor, meeting_id: int, body: WorkspaceMeetingRecordStatusUpdate
) -> None:
    """流转工作范围内的约见安排状态；已取消的约见不可恢复。"""
    params: dict[str, object] = {"id": meeting_id}
    clause = _scope_predicate(actor, "mr", params)
    row = await db.execute(
        text(
            f"""SELECT m.id, m.status FROM meeting_record m
            JOIN meeting_request mr ON mr.id = m.request_id
            WHERE m.id = :id AND {clause}"""
        ),
        params,
    )
    current = row.first()
    if current is None:
        raise HTTPException(404, detail="约见安排不存在或不在服务范围")
    if current.status == "CANCELLED":
        raise HTTPException(409, detail="已取消的约见不能恢复")
    cancel_reason = body.cancel_reason.strip() if body.cancel_reason else None
    await db.execute(
        text(
            "UPDATE meeting_record SET status = :status, cancel_reason = :reason, updated_at = UTC_TIMESTAMP() WHERE id = :id"
        ),
        {"id": meeting_id, "status": body.status, "reason": cancel_reason},
    )
    await _audit(db, actor.user_id, "matchmaker_workspace.meeting.record_status", "meeting_record", meeting_id, body.status)
    await db.commit()


async def update_workspace_introduction_status(
    db: AsyncSession, actor: WorkspaceActor, introduction_id: int, status: str, failure_reason: str | None
) -> None:
    params: dict[str, object] = {"id": introduction_id}
    clause = _scope_predicate(actor, "mi", params)
    row = await db.execute(text(f"SELECT id, status FROM matchmaker_introduction mi WHERE mi.id = :id AND {clause}"), params)
    current = row.first()
    if current is None:
        raise HTTPException(404, detail="牵线记录不存在或不在服务范围")
    reason_value = failure_reason.strip() if failure_reason else None
    if status == "FAILED":
        await db.execute(text(
            "UPDATE matchmaker_introduction SET status = :status, failure_reason = :reason, updated_at = UTC_TIMESTAMP() WHERE id = :id"
        ), {"id": introduction_id, "status": status, "reason": reason_value})
    else:
        await db.execute(text(
            "UPDATE matchmaker_introduction SET status = :status, updated_at = UTC_TIMESTAMP() WHERE id = :id"
        ), {"id": introduction_id, "status": status})
    await _audit(db, actor.user_id, "matchmaker_workspace.introduction.status", "matchmaker_introduction", introduction_id, status)
    await db.commit()


async def create_workspace_introduction(
    db: AsyncSession, actor: WorkspaceActor, body: WorkspaceIntroductionCreate
) -> WorkspaceIntroductionCreated:
    """添加牵线记录：双方须在工作范围内，时间/结果按业务口径落库。"""
    member_params: dict[str, object] = {"a": body.from_user_id, "b": body.to_user_id}
    member_clause = _member_scope_predicate(actor, member_params)
    found = await db.execute(
        text(f"SELECT id FROM users u WHERE u.id IN (:a, :b) AND {member_clause}"),
        member_params,
    )
    if len(found.all()) != 2:
        raise HTTPException(404, detail="牵线双方必须都是服务范围内的会员")
    note = f"workbench-entry:{actor.user_id}:{body.from_user_id}-{body.to_user_id}:{body.applied_at or 'now'}"
    duplicate = await db.execute(
        text("SELECT id FROM matchmaker_introduction WHERE matchmaker_id = :mid AND note = :note LIMIT 1"),
        {"mid": actor.user_id, "note": note},
    )
    if duplicate.scalar():
        raise HTTPException(409, detail="相同的牵线记录已存在")
    applied = body.applied_at or date.today()
    result = await db.execute(text(
        "INSERT INTO matchmaker_introduction"
        " (from_user_id, to_user_id, matchmaker_id, organization_id, status, failure_reason, note, created_at, updated_at)"
        " VALUES (:f, :t, :m, :org, :status, :reason, :note, :applied, UTC_TIMESTAMP())"
    ), {
        "f": body.from_user_id, "t": body.to_user_id, "m": actor.user_id,
        "org": actor.organization_id, "status": body.result,
        "reason": (body.failure_reason or "").strip() or None if body.result == "FAILED" else None,
        "note": note, "applied": applied,
    })
    introduction_id = int(result.lastrowid)
    await _audit(db, actor.user_id, "matchmaker_workspace.introduction.create", "matchmaker_introduction", introduction_id, body.result)
    await db.commit()
    return WorkspaceIntroductionCreated(id=introduction_id, status=body.result)

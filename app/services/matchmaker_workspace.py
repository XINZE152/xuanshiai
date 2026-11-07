"""Scoped mobile workspace services for service matchmakers, partners and promoters."""

from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import base64
import json
from secrets import token_urlsafe

import aiofiles
import httpx
from app.services.meeting_ownership import applicant_assignment, meeting_owner_sql
from fastapi import HTTPException, UploadFile
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any

from app.api.dependencies import CurrentUser
from app.core.config import settings
from app.core.security import lead_convert_password_fields
from app.services.auth import ensure_default_user_role
from app.services.customer_lead_contact import (
    ensure_contact_available,
    normalize_contact,
    raise_duplicate_contact,
)
from app.services.profile import (
    EDUCATION_LEVEL_LABELS,
    _image_outputs,
    _read_limited,
    education_level_from_label,
    recalculate_completion,
)
from app.services.regions import region_codes_from_text, region_display
from app.schemas.matchmaker_workspace import (
    MatchmakerWorkspaceAccess,
    MatchmakerHomepage,
    MatchmakerHomepageCase,
    MatchmakerHomepageUpdate,
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
    WorkspaceCommissionEntry,
    WorkspaceCommissionEntryPage,
    WorkspaceOfflineIncome,
    WorkspaceOfflineIncomePage,
    WorkspaceGoodNewsCreated,
    WorkspaceGoodNewsCreate,
    WorkspaceGoodNewsItem,
    WorkspaceGoodNewsPage,
    WorkspacePennantItem,
    WorkspacePennantPage,
    WorkspaceLead,
    WorkspaceLevel,
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
    WorkspaceIntroductionContactViewed,
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
    WorkspaceMemberPickupResult,
    WorkspaceMemberReviewUpdate,
    WorkspaceMeetingRecordSummary,
    WorkspaceMeetingRequest,
    WorkspaceMeetingRequestPage,
    WorkspaceMeetingScheduleCreate,
    WorkspaceMeetingDirectCreate,
    WorkspaceMeetingStatusUpdate,
    WorkspaceMeetingRecordStatusUpdate,
    WorkspaceMetric,
)


@dataclass(frozen=True)
class WorkspaceActor:
    user_id: int
    display_name: str
    level: str
    organization_id: int | None
    # 与红娘主页头像同一列（users.avatar），欢迎卡不能另搞一套头像口径。
    avatar: str | None = None

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
    return await workspace_access_for_user(db, current.id)


async def workspace_access_for_user(
    db: AsyncSession, user_id: int
) -> MatchmakerWorkspaceAccess:
    """按用户 ID 判定工作台准入；后台预览前台要按被查看的红娘取，不能依赖登录态。"""
    if not await _has_active_role(db, user_id, "service_matchmaker"):
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
            {"user_id": user_id},
        )
    ).mappings().first()
    # SUPER 且不挂门店 = 总店超级红娘（平台范围），不再是「未绑定组织」的坏状态。
    return MatchmakerWorkspaceAccess(
        can_access=True,
        role_status="active",
        scope="ORGANIZATION" if row and row["level"] == "SUPER" else "SELF",
        message="服务红娘工作台已开通",
    )


async def require_workspace(db: AsyncSession, current: CurrentUser) -> WorkspaceActor:
    return await require_workspace_for_user(db, current.id)


async def require_workspace_for_user(db: AsyncSession, user_id: int) -> WorkspaceActor:
    access = await workspace_access_for_user(db, user_id)
    if not access.can_access:
        raise HTTPException(403, detail=access.message)
    row = (
        await db.execute(
            text(
                """SELECT COALESCE(p.display_name, u.nickname, '服务红娘') AS display_name,
                    COALESCE(p.level, 'NORMAL') AS level, p.organization_id, u.avatar
                FROM users u
                LEFT JOIN matchmaker_workspace_profile p ON p.user_id = u.id AND p.status = 1
                WHERE u.id = :user_id"""
            ),
            {"user_id": user_id},
        )
    ).mappings().one()
    values = dict(row)
    return WorkspaceActor(
        user_id=user_id,
        display_name=str(values["display_name"]),
        level=str(values["level"]),
        organization_id=int(values["organization_id"]) if values["organization_id"] else None,
        avatar=str(values["avatar"]) if values.get("avatar") else None,
    )


def require_review_authority(actor: WorkspaceActor) -> WorkspaceActor:
    """公开资料审核只对总店（超级）红娘开放。

    普通红娘仍可代会员编辑资料、重新提交待审，但不能判定通过或驳回；
    范围过滤（_member_scope_predicate）在这道闸门之后仍然生效，总店红娘之间不互相越范围。
    """
    if actor.level != "SUPER":
        raise HTTPException(403, detail="仅总店红娘可审核会员公开资料")
    return actor


def _scope_predicate(actor: WorkspaceActor, alias: str, params: dict[str, object]) -> str:
    if actor.scope == "SELF":
        params["scope_matchmaker_id"] = actor.user_id
        return f"{alias}.matchmaker_id = :scope_matchmaker_id"
    if actor.organization_id is None:
        # 总店超级红娘：不挂门店，范围是平台全部，与后台须知「总店-超级红娘＝查看平台全部客源」一致。
        return "1 = 1"
    params["scope_organization_id"] = actor.organization_id
    return f"{alias}.organization_id = :scope_organization_id"


def _meeting_scope_predicate(actor: WorkspaceActor, alias: str, params: dict[str, object]) -> str:
    if actor.scope == "SELF":
        params["scope_matchmaker_id"] = actor.user_id
        return meeting_owner_sql(alias) + " = :scope_matchmaker_id"
    if actor.organization_id is None:
        return "1 = 1"
    params["scope_organization_id"] = actor.organization_id
    return meeting_owner_sql(alias, "organization_id") + " = :scope_organization_id"


def _meeting_follow_up_sql(alias: str = "m") -> str:
    return f"""(SELECT f.content FROM business_audit_log log
        JOIN member_follow_up f ON f.id = CAST(JSON_UNQUOTE(JSON_EXTRACT(log.after_json, '$.follow_up_id')) AS UNSIGNED)
        WHERE log.resource_type = 'meeting_record' AND log.resource_id = {alias}.id
        AND log.action = 'matchmaker_workspace.meeting.completed' ORDER BY log.id DESC LIMIT 1)"""


def _applied_matchmaker_exists(user_id_sql: str) -> str:
    """后台「红娘管理」的建档口径（SELECT_STAFF）：申请生效 + 档案未软删。

    小程序可选红娘名单必须过同一道闸门，否则 2026-09-06 那批只有 `user_role` 与工作台档案、
    后台从没建过档的种子号会一直挂在「修改红娘」名单里。
    """
    return (
        f"EXISTS (SELECT 1 FROM user_matchmaker_apply ma "
        f"LEFT JOIN matchmaker_profile mp ON mp.user_id = ma.user_id "
        f"WHERE ma.user_id = {user_id_sql} AND ma.application_type = 'service_matchmaker' "
        f"AND ma.status = 1 AND mp.deleted_at IS NULL)"
    )


def _member_scope_predicate(actor: WorkspaceActor, params: dict[str, object]) -> str:
    if actor.scope == "SELF":
        params["scope_matchmaker_id"] = actor.user_id
        return "EXISTS (SELECT 1 FROM resource_assignment ra WHERE ra.user_id = u.id AND ra.status = 1 AND ra.matchmaker_id = :scope_matchmaker_id)"
    if actor.organization_id is None:
        # 总店超级红娘：全部「已归属」会员，仍要求有生效归属行，否则资料待审会把全站注册用户都算进来。
        return "EXISTS (SELECT 1 FROM resource_assignment ra WHERE ra.user_id = u.id AND ra.status = 1)"
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
        avatar=actor.avatar,
    )
    return await get_workspace_profile(db, updated)


async def sync_workspace_profile(
    db: AsyncSession, user_id: int, *, overwrite: bool, level: WorkspaceLevel | None = None
) -> None:
    """把后台侧的服务红娘身份补齐进工作台档案，消除「后台红娘管理有、小程序修改红娘没有」。

    名称取 users.nickname，门店取 organization_member 的 store_matchmaker 有效绑定。
    overwrite=False 用于申请审核通过：已有档案行是红娘在工作台自设的，不覆盖。
    level 只在后台建档/编辑显式传参时写入（对应后台「总店-超级红娘 / 总店-普通红娘」下拉），
    不传时保持原值——申请审核通过不会把红娘升降级。
    """
    row = (
        await db.execute(
            text(
                """SELECT COALESCE(u.nickname, '服务红娘') AS display_name,
                (SELECT om.organization_id FROM organization_member om
                WHERE om.user_id = u.id AND om.role_code = 'store_matchmaker' AND om.status = 1
                ORDER BY om.id DESC LIMIT 1) AS organization_id
                FROM users u WHERE u.id = :user_id"""
            ),
            {"user_id": user_id},
        )
    ).mappings().first()
    if row is None:
        return
    updates = ["status = 1", "updated_at = UTC_TIMESTAMP()"]
    if level is not None:
        updates.append("level = VALUES(level)")
    if overwrite:
        updates = [
            "display_name = VALUES(display_name)",
            "organization_id = VALUES(organization_id)",
            *updates,
        ]
    await db.execute(
        text(
            f"""INSERT INTO matchmaker_workspace_profile
            (user_id, display_name, level, organization_id, status)
            VALUES (:user_id, :display_name, COALESCE(:level, 'NORMAL'), :organization_id, 1)
            ON DUPLICATE KEY UPDATE {", ".join(updates)}"""
        ),
        {
            "user_id": user_id,
            "display_name": str(row["display_name"])[:64],
            "level": level,
            "organization_id": int(row["organization_id"]) if row["organization_id"] is not None else None,
        },
    )


def _homepage_details(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


def _homepage_specialties(raw: Any) -> list[str]:
    items = raw if isinstance(raw, list) else []
    return [str(item).strip() for item in items if str(item).strip()][:5]


def _homepage_cases(raw: Any) -> list[MatchmakerHomepageCase]:
    items = raw if isinstance(raw, list) else []
    result: list[MatchmakerHomepageCase] = []
    for entry in items[:3]:
        if not isinstance(entry, dict):
            continue
        description = str(entry.get("description") or "").strip()
        if not description:
            continue
        images = entry.get("images") if isinstance(entry.get("images"), list) else []
        result.append(
            MatchmakerHomepageCase(
                description=description,
                images=[str(image).strip() for image in images if str(image).strip()][:3],
            )
        )
    return result


async def get_workspace_homepage(
    db: AsyncSession, actor: WorkspaceActor
) -> MatchmakerHomepage:
    """主页编辑读取入驻申请表：公开红娘主页与后台红娘编辑都读同一份资料。"""
    row = (
        await db.execute(
            text(
                """SELECT app.id AS application_id, app.user_id, u.nickname, u.avatar,
                    app.slogan, app.intro, app.application_details
                FROM user_matchmaker_apply app
                JOIN users u ON u.id = app.user_id
                WHERE app.user_id = :user_id AND app.application_type = 'service_matchmaker'"""
            ),
            {"user_id": actor.user_id},
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(404, detail="未找到红娘入驻档案，暂不能编辑主页")
    details = _homepage_details(row["application_details"])
    return MatchmakerHomepage(
        user_id=int(row["user_id"]),
        nickname=str(row["nickname"] or "服务红娘"),
        avatar=row["avatar"],
        slogan=row["slogan"],
        specialties=_homepage_specialties(details.get("specialties")),
        introduction=str(row["intro"] or ""),
        success_cases=_homepage_cases(details.get("success_cases")),
    )


async def update_workspace_homepage(
    db: AsyncSession,
    actor: WorkspaceActor,
    request: MatchmakerHomepageUpdate,
) -> MatchmakerHomepage:
    row = (
        await db.execute(
            text(
                """SELECT app.id AS application_id, app.application_details
                FROM user_matchmaker_apply app
                WHERE app.user_id = :user_id AND app.application_type = 'service_matchmaker'
                FOR UPDATE"""
            ),
            {"user_id": actor.user_id},
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(404, detail="未找到红娘入驻档案，暂不能编辑主页")

    values = request.model_dump(exclude_unset=True)
    if "nickname" in values:
        await db.execute(
            text("UPDATE users SET nickname = :value WHERE id = :id"),
            {"value": values["nickname"], "id": actor.user_id},
        )
        await db.execute(
            text("UPDATE matchmaker_workspace_profile SET display_name = :value WHERE user_id = :id"),
            {"value": values["nickname"], "id": actor.user_id},
        )
    if "slogan" in values:
        await db.execute(
            text("UPDATE user_matchmaker_apply SET slogan = :value WHERE id = :id"),
            {"value": values["slogan"], "id": int(row["application_id"])},
        )
    if "introduction" in values:
        await db.execute(
            text("UPDATE user_matchmaker_apply SET intro = :value WHERE id = :id"),
            {"value": values["introduction"], "id": int(row["application_id"])},
        )
    if "specialties" in values or "success_cases" in values:
        # application_details 还带着微信、期望价格等审核资料，只覆盖主页字段。
        details = _homepage_details(row["application_details"])
        if "specialties" in values:
            details["specialties"] = values["specialties"] or []
        if "success_cases" in values:
            details["success_cases"] = values["success_cases"]
        await db.execute(
            text("UPDATE user_matchmaker_apply SET application_details = :value WHERE id = :id"),
            {"value": json.dumps(details, ensure_ascii=False), "id": int(row["application_id"])},
        )

    await _audit(
        db,
        actor.user_id,
        "matchmaker_workspace.homepage.update",
        "user_matchmaker_apply",
        int(row["application_id"]),
    )
    await db.commit()
    return await get_workspace_homepage(db, actor)


def workspace_role_name(actor: WorkspaceActor) -> str:
    """工作台身份徽标：与后台「红娘管理」的角色下拉同一口径。

    ``SUPER`` 不挂门店 = 总店超级红娘（平台范围）；``SUPER`` 挂门店只在本店范围内生效；
    其余仍按普通服务红娘展示，和 ``_scope_predicate`` 给出的数据范围一一对应。
    """
    if actor.level != "SUPER":
        return "服务红娘"
    return "门店超级红娘" if actor.organization_id is not None else "总店超级红娘"


def workspace_identity(actor: WorkspaceActor) -> MatchmakerWorkspaceIdentity:
    """身份徽标单独成接口：我的账号页只要名称与角色，不必跑一遍首页的八个统计。"""
    return MatchmakerWorkspaceIdentity(
        greeting="你好",
        name=actor.display_name,
        avatar=actor.avatar,
        role_name=workspace_role_name(actor),
    )


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
    meeting_scope = _meeting_scope_predicate(actor, "mr", meeting_params)
    # 约见申请入口对应全部；待见面包含已接受但尚未排期，以及尚未完成跟进的约会。
    meeting_request_count = int(
        await db.scalar(
            text(
                f"""SELECT COUNT(*) FROM meeting_request mr WHERE {meeting_scope}
                """
            ),
            meeting_params,
        )
        or 0
    )
    pending_meeting_count = int(
        await db.scalar(
            text(
                f"""SELECT COUNT(*) FROM meeting_request mr
                LEFT JOIN meeting_record m ON m.id = (SELECT MAX(mm.id) FROM meeting_record mm WHERE mm.request_id = mr.id)
                WHERE {meeting_scope} AND mr.status = 'ACCEPTED'
                AND (m.id IS NULL OR m.status IN ('SCHEDULED', 'REMINDED', 'CHECKED_IN')
                    OR (m.status = 'COMPLETED' AND {_meeting_follow_up_sql()} IS NULL))"""
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
    # 线下业绩与后台「工作汇报 / 组织报表」同口径：本人作为销售红娘的线下 VIP 已收款订单。
    offline_income = await db.scalar(
        text(
            """SELECT COALESCE(SUM(amount), 0) FROM payment_order
            WHERE matchmaker_id = :user_id AND status = 1 AND product_type = 'offline_vip'"""
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
            value=_money_text(offline_income),
            action="财务明细",
            value_prefix="￥",
        ),
        WorkspaceMetric(key="pending_introduction", label="待我牵线", value=str(intro_count), action="牵线记录"),
        WorkspaceMetric(key="introduction_succeeded", label="牵线成功", value=str(intro_success_count), action="牵线记录"),
        WorkspaceMetric(key="meeting_requests", label="约见申请", value=str(meeting_request_count), action="约见申请"),
        WorkspaceMetric(key="pending_meeting", label="待见面", value=str(pending_meeting_count), action="约见申请"),
    ]
    return MatchmakerWorkspaceDashboard(
        identity=workspace_identity(actor),
        store_metrics=metrics,
    )


def _money_text(value: object) -> str:
    return f"{Decimal(str(value if value is not None else 0)):.2f}"


async def list_workspace_commission_entries(
    db: AsyncSession, actor: WorkspaceActor, page: int, page_size: int
) -> WorkspaceCommissionEntryPage:
    """本人线上分成明细：会员名与分成事件由订单、规则回带，口径与后台「总店红娘-分成明细」一致。"""
    params: dict[str, object] = {
        "user_id": actor.user_id,
        "limit": page_size,
        "offset": (page - 1) * page_size,
    }
    clause = "ce.beneficiary_type = 'service_matchmaker' AND ce.beneficiary_id = :user_id"
    base = """FROM commission_entry ce
        LEFT JOIN payment_order po ON po.id = ce.order_id
        LEFT JOIN users c ON c.id = po.user_id
        LEFT JOIN commission_rule rule ON rule.id = ce.rule_id"""
    rows = await db.execute(
        text(
            f"""SELECT ce.id, ce.created_at, ce.status, ce.base_amount, ce.amount,
            po.order_no, po.user_id AS consumer_id, po.product_name,
            c.nickname AS consumer_name, c.avatar AS consumer_avatar,
            COALESCE(rule.name, po.product_name) AS event_name
            {base} WHERE {clause} ORDER BY ce.created_at DESC, ce.id DESC
            LIMIT :limit OFFSET :offset"""
        ),
        params,
    )
    count_params = {key: value for key, value in params.items() if key not in {"limit", "offset"}}
    total = int(await db.scalar(text(f"SELECT COUNT(*) {base} WHERE {clause}"), count_params) or 0)
    # 冲正行不进合计：与工作台概览 online_commission 同一算法，避免两处金额对不上。
    total_amount = await db.scalar(
        text(f"SELECT COALESCE(SUM(ce.amount), 0) {base} WHERE {clause} AND ce.status <> 'REVERSED'"),
        count_params,
    )
    items: list[WorkspaceCommissionEntry] = []
    for row in rows.mappings().all():
        consumer_id = int(row["consumer_id"] or 0)
        consumer_name = row["consumer_name"] or (f"会员#{consumer_id}" if consumer_id > 0 else "未知会员")
        event_name = row["event_name"] or "服务分成"
        items.append(
            WorkspaceCommissionEntry(
                id=int(row["id"]),
                created_at=row["created_at"],
                consumer_id=consumer_id,
                consumer_name=str(consumer_name),
                consumer_avatar=str(row["consumer_avatar"]) if row["consumer_avatar"] else None,
                event_name=str(event_name),
                order_no=str(row["order_no"]) if row["order_no"] else None,
                base_amount=_money_text(row["base_amount"]),
                amount=_money_text(row["amount"]),
                status=str(row["status"]),
            )
        )
    return WorkspaceCommissionEntryPage(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        has_more=_page(total, page, page_size),
        total_amount=_money_text(total_amount),
    )


async def list_workspace_offline_income(
    db: AsyncSession, actor: WorkspaceActor, page: int, page_size: int
) -> WorkspaceOfflineIncomePage:
    """本人线下业绩明细：线下 VIP 已收款订单，口径与后台工作汇报的 offline_income 一致。"""
    params: dict[str, object] = {
        "user_id": actor.user_id,
        "limit": page_size,
        "offset": (page - 1) * page_size,
    }
    clause = "po.matchmaker_id = :user_id AND po.status = 1 AND po.product_type = 'offline_vip'"
    base = """FROM payment_order po
        LEFT JOIN users c ON c.id = po.user_id"""
    rows = await db.execute(
        text(
            f"""SELECT po.id, COALESCE(po.pay_time, po.created_at) AS paid_at, po.order_no,
            po.product_name, po.amount, po.user_id AS consumer_id,
            c.nickname AS consumer_name, c.avatar AS consumer_avatar
            {base} WHERE {clause} ORDER BY paid_at DESC, po.id DESC
            LIMIT :limit OFFSET :offset"""
        ),
        params,
    )
    count_params = {key: value for key, value in params.items() if key not in {"limit", "offset"}}
    total = int(await db.scalar(text(f"SELECT COUNT(*) {base} WHERE {clause}"), count_params) or 0)
    # 只统计已收款订单，与概览 offline_performance 同一算法。
    total_amount = await db.scalar(
        text(f"SELECT COALESCE(SUM(po.amount), 0) {base} WHERE {clause}"), count_params
    )
    items: list[WorkspaceOfflineIncome] = []
    for row in rows.mappings().all():
        consumer_id = int(row["consumer_id"] or 0)
        consumer_name = row["consumer_name"] or (f"会员#{consumer_id}" if consumer_id > 0 else "未知会员")
        item_name = row["product_name"] or "线下VIP服务"
        items.append(
            WorkspaceOfflineIncome(
                id=int(row["id"]),
                paid_at=row["paid_at"],
                consumer_id=consumer_id,
                consumer_name=str(consumer_name),
                consumer_avatar=str(row["consumer_avatar"]) if row["consumer_avatar"] else None,
                item_name=str(item_name),
                order_no=str(row["order_no"]) if row["order_no"] else None,
                amount=_money_text(row["amount"]),
            )
        )
    return WorkspaceOfflineIncomePage(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        has_more=_page(total, page, page_size),
        total_amount=_money_text(total_amount),
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
        application_response={1: "ACCEPTED", 2: "REJECTED"}.get(values.get("application_status"), "UNANSWERED"),
        response_reason=values.get("failure_reason") if values.get("application_status") == 2 and values["status"] == "PENDING" else None,
        from_phone_viewed=bool(values.get("from_phone_viewed")),
        to_phone_viewed=bool(values.get("to_phone_viewed")),
        from_user=WorkspaceIntroductionPerson(
            user_id=int(values["from_user_id"]),
            nickname=str(values["from_nickname"] or "未命名会员"),
            avatar=str(values["from_avatar"]) if values.get("from_avatar") else None,
            gender=int(values["from_gender"]) if values.get("from_gender") in {1, 2} else None,
            is_vip=bool(values.get("from_is_vip")),
        ),
        to_user=WorkspaceIntroductionPerson(
            user_id=int(values["to_user_id"]),
            nickname=str(values["to_nickname"] or "未命名会员"),
            avatar=str(values["to_avatar"]) if values.get("to_avatar") else None,
            gender=int(values["to_gender"]) if values.get("to_gender") in {1, 2} else None,
            is_vip=bool(values.get("to_is_vip")),
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
    params["viewer_id"] = actor.user_id
    base = """FROM matchmaker_introduction mi
        JOIN users fu ON fu.id = mi.from_user_id
        JOIN users tu ON tu.id = mi.to_user_id
        LEFT JOIN match_apply ma ON ma.id = CASE
            WHEN mi.note LIKE 'discovery-apply:%' THEN CAST(SUBSTRING(mi.note, 17) AS UNSIGNED)
            WHEN mi.note LIKE 'admin-entry:%' THEN CAST(SUBSTRING(mi.note, 13) AS UNSIGNED)
            ELSE NULL END
        LEFT JOIN (SELECT resource_id,
            MAX(CASE WHEN action = 'matchmaker_workspace.introduction.phone.from' AND actor_user_id = :viewer_id THEN id END) AS from_view_id,
            MAX(CASE WHEN action = 'matchmaker_workspace.introduction.phone.to' AND actor_user_id = :viewer_id THEN id END) AS to_view_id,
            MAX(CASE WHEN action = 'matchmaker_workspace.introduction.response.rejected' THEN id ELSE 0 END) AS reset_view_id
            FROM business_audit_log WHERE resource_type = 'matchmaker_introduction'
            AND (actor_user_id = :viewer_id OR action = 'matchmaker_workspace.introduction.response.rejected')
            AND action IN ('matchmaker_workspace.introduction.phone.from', 'matchmaker_workspace.introduction.phone.to', 'matchmaker_workspace.introduction.response.rejected')
            GROUP BY resource_id) pv ON pv.resource_id = mi.id"""
    rows = await db.execute(
        text(
            f"""SELECT mi.id, mi.status, mi.failure_reason, mi.note, mi.created_at, mi.updated_at, ma.status AS application_status,
            pv.from_view_id > COALESCE(pv.reset_view_id, 0) AS from_phone_viewed,
            pv.to_view_id > COALESCE(pv.reset_view_id, 0) AS to_phone_viewed,
            fu.id AS from_user_id, fu.nickname AS from_nickname, fu.avatar AS from_avatar, fu.gender AS from_gender,
            EXISTS (SELECT 1 FROM user_membership mf WHERE mf.user_id = fu.id AND mf.status = 1
                    AND (mf.start_at IS NULL OR mf.start_at <= UTC_TIMESTAMP())
                    AND (mf.end_at IS NULL OR mf.end_at > UTC_TIMESTAMP())) AS from_is_vip,
            tu.id AS to_user_id, tu.nickname AS to_nickname, tu.avatar AS to_avatar, tu.gender AS to_gender,
            EXISTS (SELECT 1 FROM user_membership mt WHERE mt.user_id = tu.id AND mt.status = 1
                    AND (mt.start_at IS NULL OR mt.start_at <= UTC_TIMESTAMP())
                    AND (mt.end_at IS NULL OR mt.end_at > UTC_TIMESTAMP())) AS to_is_vip
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
            follow_up_content=values.get("follow_up_content"),
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
            is_vip=bool(values.get("applicant_is_vip")),
        ),
        target=WorkspaceIntroductionPerson(
            user_id=int(values["target_user_id"]),
            nickname=str(values["target_nickname"] or "未命名会员"),
            avatar=str(values["target_avatar"]) if values.get("target_avatar") else None,
            gender=int(values["target_gender"]) if values.get("target_gender") in {1, 2} else None,
            is_vip=bool(values.get("target_is_vip")),
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
    where = [_meeting_scope_predicate(actor, "mr", params)]
    if search:
        where.append(
            "(au.nickname LIKE CONCAT('%', :search, '%') OR tu.nickname LIKE CONCAT('%', :search, '%') "
            "OR CAST(mr.id AS CHAR) LIKE CONCAT('%', :search, '%'))"
        )
        params["search"] = search
    clause = " AND ".join(where)
    base = f"""FROM meeting_request mr
        JOIN users au ON au.id = mr.user_id
        JOIN users tu ON tu.id = mr.target_user_id
        LEFT JOIN users mu ON mu.id = {meeting_owner_sql("mr")}
        LEFT JOIN matchmaker_workspace_profile mwp ON mwp.user_id = {meeting_owner_sql("mr")} AND mwp.status = 1
        LEFT JOIN (
            SELECT request_id, MAX(id) AS meeting_id FROM meeting_record GROUP BY request_id
        ) latest ON latest.request_id = mr.id
        LEFT JOIN meeting_record m ON m.id = latest.meeting_id"""
    rows = await db.execute(
        text(
            f"""SELECT mr.id, mr.status, mr.note, mr.created_at, mr.updated_at,
            au.id AS applicant_user_id, au.nickname AS applicant_nickname, au.avatar AS applicant_avatar, au.gender AS applicant_gender,
            EXISTS (SELECT 1 FROM user_membership ma WHERE ma.user_id = au.id AND ma.status = 1
                    AND (ma.start_at IS NULL OR ma.start_at <= UTC_TIMESTAMP())
                    AND (ma.end_at IS NULL OR ma.end_at > UTC_TIMESTAMP())) AS applicant_is_vip,
            tu.id AS target_user_id, tu.nickname AS target_nickname, tu.avatar AS target_avatar, tu.gender AS target_gender,
            EXISTS (SELECT 1 FROM user_membership mt WHERE mt.user_id = tu.id AND mt.status = 1
                    AND (mt.start_at IS NULL OR mt.start_at <= UTC_TIMESTAMP())
                    AND (mt.end_at IS NULL OR mt.end_at > UTC_TIMESTAMP())) AS target_is_vip,
            mu.nickname AS matchmaker_nickname, mwp.display_name AS matchmaker_display_name,
            m.id AS meeting_id, m.status AS meeting_status, m.scheduled_at, m.location, m.cancel_reason,
            {_meeting_follow_up_sql()} AS follow_up_content
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


_LEAD_SELECT = """SELECT l.id, l.name, l.real_name, l.phone, l.wechat, l.source, l.intention_level, l.status,
    l.next_follow_at, l.created_at, l.updated_at, l.remark, l.matchmaker_id, l.converted_user_id,
    l.photo, l.gender, l.birthday,
    COALESCE(p.display_name, u.nickname) AS matchmaker_name,
    COALESCE(cp.display_name, cu.nickname) AS creator_name,
    lf.last_follow_up_at,
    ab.reason AS abandon_reason,
    ab.abandoned_at AS abandoned_at
    FROM customer_lead l
    LEFT JOIN matchmaker_workspace_profile p ON p.user_id = l.matchmaker_id AND p.status = 1
    LEFT JOIN users u ON u.id = l.matchmaker_id
    LEFT JOIN matchmaker_workspace_profile cp ON cp.user_id = l.created_by AND cp.status = 1
    LEFT JOIN users cu ON cu.id = l.created_by
    LEFT JOIN (SELECT lead_id, MAX(created_at) AS last_follow_up_at
        FROM customer_lead_follow_up GROUP BY lead_id) lf ON lf.lead_id = l.id
    LEFT JOIN (SELECT lead_id, MAX(id) AS active_abandonment_id
        FROM customer_lead_abandonment WHERE restored_at IS NULL GROUP BY lead_id) aa ON aa.lead_id = l.id
    LEFT JOIN customer_lead_abandonment ab ON ab.id = aa.active_abandonment_id"""


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
    db: AsyncSession, actor: WorkspaceActor, page: int, page_size: int,
    status: str | None, search: str | None, **filters: object,
) -> WorkspaceLeadPage:
    # 复用后台筛选口径，同时保留工作台的数据范围和弃海可见规则。
    from app.services.customer_lead_admin import list_leads

    scope_params: dict[str, object] = {}
    if actor.scope == "SELF":
        scope_params.update(scope_matchmaker_id=actor.user_id, abandoned_by=actor.user_id)
        scope = ("(customer_lead.matchmaker_id = :scope_matchmaker_id OR EXISTS ("
                 "SELECT 1 FROM customer_lead_abandonment a WHERE a.lead_id = customer_lead.id "
                 "AND a.restored_at IS NULL AND a.abandoned_by = :abandoned_by))")
    else:
        scope = _scope_predicate(actor, "customer_lead", scope_params)
    result = await list_leads(
        db, page, page_size, status, filters.pop("source", None),
        filters.pop("matchmaker_id", None), search,
        scope=scope, scope_params=scope_params, **filters,
    )
    ids = [item.id for item in result.items]
    if not ids:
        return WorkspaceLeadPage(items=[], page=page, page_size=page_size,
                                 total=result.total, has_more=result.has_more)
    params: dict[str, object] = {"lead_id_" + str(i): value for i, value in enumerate(ids)}
    placeholders = ", ".join(":" + key for key in params)
    params.update(scope_params)
    scoped_where = scope.replace("customer_lead.", "l.")
    rows = (await db.execute(text(f"{_LEAD_SELECT} WHERE l.id IN ({placeholders}) AND {scoped_where}"), params)).mappings().all()
    by_id = {int(row["id"]): _lead_from_row(row) for row in rows}
    return WorkspaceLeadPage(items=[by_id[id] for id in ids if id in by_id], page=page,
                             page_size=page_size, total=result.total, has_more=result.has_more)


async def upload_lead_photo(
    db: AsyncSession, actor: WorkspaceActor, file: UploadFile
) -> dict[str, Any]:
    """客源线索照片上传：写入 storage/uploads/leads/，返回可直接存入 photo 字段的 URL。"""
    return await upload_workspace_photo(db, actor, file, "leads")


async def upload_workspace_photo(
    db: AsyncSession, actor: WorkspaceActor, file: UploadFile, subdir: str
) -> dict[str, Any]:
    """工作台图片上传：按业务子目录写入 storage/uploads/<subdir>/，返回可入库的 URL 对。"""
    data = await _read_limited(file, 5 * 1024 * 1024)
    image_data, thumbnail_data = await asyncio.to_thread(_image_outputs, data)
    directory = Path(settings.upload_dir) / subdir
    directory.mkdir(parents=True, exist_ok=True)
    name = uuid.uuid4().hex
    image_path = directory / f"photo-{name}.webp"
    thumbnail_path = directory / f"photo-{name}-thumb.webp"
    await asyncio.gather(
        _write_lead_photo(image_path, image_data),
        _write_lead_photo(thumbnail_path, thumbnail_data),
    )
    return {
        "url": f"/storage/uploads/{subdir}/{image_path.name}",
        "thumbnail_url": f"/storage/uploads/{subdir}/{thumbnail_path.name}",
        "size": len(image_data),
    }


async def _write_lead_photo(path: Path, data: bytes) -> None:
    async with aiofiles.open(path, "wb") as fh:
        await fh.write(data)


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
                (name, real_name, phone, wechat, source, intention_level, remark, photo, gender, birthday,
                 matchmaker_id, organization_id, created_by)
                VALUES (:name, :real_name, :phone, :wechat, :source, :intention_level, :remark, :photo, :gender, :birthday,
                 :matchmaker_id, :organization_id, :created_by)"""
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
    # 捞取即换人：谁捞回归谁，created_by 一并改写成捞取人；原登记人仍留在弃海流水 abandoned_by 与审计日志里。
    await db.execute(
        text(
            """UPDATE customer_lead SET status = 'NEW', matchmaker_id = :matchmaker_id,
            created_by = :actor_id, organization_id = :organization_id,
            updated_at = UTC_TIMESTAMP() WHERE id = :lead_id"""
        ),
        {
            "lead_id": lead_id,
            "matchmaker_id": actor.user_id,
            "actor_id": actor.user_id,
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
    """同组织可分派跟进的服务红娘；普通红娘仅能看到自己；总店超级红娘（不挂门店）为平台全部红娘。"""
    if actor.scope != "ORGANIZATION":
        return [WorkspaceLeadFollower(user_id=actor.user_id, display_name=actor.display_name, is_self=True)]
    params: dict[str, object] = {}
    if actor.organization_id is None:
        profile_filter, lead_filter = "", ""
    else:
        params["organization_id"] = actor.organization_id
        profile_filter = " AND p.organization_id = :organization_id"
        lead_filter = " AND m.organization_id = :organization_id"
    rows = await db.execute(
        text(
            f"""SELECT p.user_id, COALESCE(p.display_name, u.nickname, '服务红娘') AS display_name
            FROM matchmaker_workspace_profile p
            JOIN users u ON u.id = p.user_id
            WHERE p.status = 1 AND {_applied_matchmaker_exists('p.user_id')}{profile_filter}
            UNION
            SELECT m.matchmaker_id, COALESCE(p2.display_name, u2.nickname, '服务红娘') AS display_name
            FROM customer_lead m
            LEFT JOIN matchmaker_workspace_profile p2 ON p2.user_id = m.matchmaker_id AND p2.status = 1
            LEFT JOIN users u2 ON u2.id = m.matchmaker_id
            WHERE m.matchmaker_id IS NOT NULL AND m.status NOT IN ('LOST', 'CLOSED')
            AND {_applied_matchmaker_exists('m.matchmaker_id')}{lead_filter}
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
            f"""SELECT p.user_id, COALESCE(p.display_name, u.nickname, '服务红娘') AS display_name
            FROM matchmaker_workspace_profile p JOIN users u ON u.id = p.user_id
            WHERE p.user_id = :matchmaker_id AND p.status = 1
            AND (:organization_id IS NULL OR p.organization_id = :organization_id)
            AND {_applied_matchmaker_exists('p.user_id')} LIMIT 1"""
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
        user_columns = ["openid", "phone", "phone_verified_at", "nickname", "gender",
                        "status", "data_complete_rate", "register_ip"]
        user_values = [":openid", ":phone", "UTC_TIMESTAMP()", ":nickname", ":gender", "1", "0", "'127.0.0.1'"]
        user_payload: dict[str, Any] = {
            "openid": f"lead-convert-{lead_id}",
            "phone": lead.phone,
            "nickname": lead.name,
            "gender": request.gender,
        }
        password_fields = lead_convert_password_fields()
        for key, value in password_fields.items():
            user_columns.append(key)
            user_values.append(':' + key)
            user_payload[key] = value
        # 入库账号标「线索入库」（与后台建档/自助注册区分）；users.source 未迁移时静默不写。
        if await _users_has_source(db):
            user_columns.append("source")
            user_values.append(":source")
            user_payload["source"] = SOURCE_FROM_LEAD
        result = await db.execute(
            text(
                f"INSERT INTO users ({', '.join(user_columns)}) VALUES ({', '.join(user_values)})"
            ),
            user_payload,
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


def _append_workspace_member_filters(where: list[str], params: dict[str, object], actor: WorkspaceActor, raw: str) -> None:
    """Apply validated member choices before LIMIT, alongside the existing scope predicate."""
    try:
        values = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, detail="筛选条件格式不正确") from exc
    if not isinstance(values, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in values.items()):
        raise HTTPException(422, detail="筛选条件格式不正确")
    exact = {
        "occupation": ("COALESCE(NULLIF(p.occupation, ''), ua.job)", ["私企员工", "央企/国企", "事业单位/公务员", "外企员工", "自由职业", "创业者/个体", "教师", "医生", "律师", "金融从业", "互联网/IT", "制造业", "服务业", "在校学生"]),
        "housing": ("p.house", ["已购房", "未购房", "与父母同住"]),
        "vehicle": ("p.car", ["已购车", "未购车"]),
        "smoking": ("p.smoking", ["不吸烟", "偶尔吸烟", "经常吸烟"]),
        "drinking": ("p.drinking", ["不饮酒", "社交场合饮酒", "经常饮酒"]),
        "ethnicity": ("p.ethnicity", ["汉族", "回族", "满族", "蒙古族", "壮族", "维吾尔族", "藏族", "苗族", "彝族", "其他"]),
        "religion": ("p.religion", ["无宗教信仰", "佛教", "基督教", "伊斯兰教", "道教", "其他"]),
        "mbti": ("p.mbti", ["INTJ", "INTP", "ENTJ", "ENTP", "INFJ", "INFP", "ENFJ", "ENFP", "ISTJ", "ISFJ", "ESTJ", "ESFJ", "ISTP", "ISFP", "ESTP", "ESFP"]),
    }
    choices = {
        "gender": {"male": "u.gender = 1", "female": "u.gender = 2"},
        "marital": {"1": "u.is_married = 1", "2": "u.is_married IN (2,4,5,6,7)", "3": "u.is_married = 3"},
        "ageRange": {"under20": "TIMESTAMPDIFF(YEAR,u.birthday,CURDATE()) < 20", "20-25": "TIMESTAMPDIFF(YEAR,u.birthday,CURDATE()) BETWEEN 20 AND 25", "26-30": "TIMESTAMPDIFF(YEAR,u.birthday,CURDATE()) BETWEEN 26 AND 30", "31-35": "TIMESTAMPDIFF(YEAR,u.birthday,CURDATE()) BETWEEN 31 AND 35", "36-40": "TIMESTAMPDIFF(YEAR,u.birthday,CURDATE()) BETWEEN 36 AND 40", "over40": "TIMESTAMPDIFF(YEAR,u.birthday,CURDATE()) > 40"},
        "heightRange": {"under160": "p.height < 160", "160-169": "p.height BETWEEN 160 AND 169", "170-179": "p.height BETWEEN 170 AND 179", "over180": "p.height >= 180"},
        "income": {"under10": "p.income < 100000", "10-20": "p.income >= 100000 AND p.income < 200000", "30-50": "p.income >= 300000 AND p.income < 500000", "50-100": "p.income >= 500000 AND p.income < 1000000", "over100": "p.income >= 1000000"},
    }
    allowed = set(exact) | set(choices) | {"education", "hometown", "residence", "household", "weight", "mine"}
    if any(key not in allowed or len(value) > 128 for key, value in values.items()):
        raise HTTPException(422, detail="不支持的筛选条件")
    for key, value in values.items():
        if not value:
            continue
        if key in {"ageRange", "heightRange", "weight"} and value.startswith("range:"):
            match = re.fullmatch(r"range:(\d*):(\d*)", value)
            limits = {"ageRange": (18, 120), "heightRange": (140, 220), "weight": (30, 150)}
            if not match or not (match.group(1) or match.group(2)):
                raise HTTPException(422, detail=f"无效的筛选范围：{key}")
            lower = int(match.group(1)) if match.group(1) else None
            upper = int(match.group(2)) if match.group(2) else None
            minimum, maximum = limits[key]
            if (lower is not None and not minimum <= lower <= maximum) or (upper is not None and not minimum <= upper <= maximum) or (lower is not None and upper is not None and lower > upper):
                raise HTTPException(422, detail=f"无效的筛选范围：{key}")
            column = {"ageRange": "TIMESTAMPDIFF(YEAR,u.birthday,CURDATE())", "heightRange": "p.height", "weight": "p.weight"}[key]
            if lower is not None:
                where.append(f"{column} >= :filter_{key}_min")
                params[f"filter_{key}_min"] = lower
            if upper is not None:
                where.append(f"{column} <= :filter_{key}_max")
                params[f"filter_{key}_max"] = upper
        elif key in exact:
            column, allowed_values = exact[key]
            if value not in allowed_values:
                raise HTTPException(422, detail=f"无效的筛选条件：{key}")
            where.append(f"{column} = :filter_{key}")
            params[f"filter_{key}"] = value
        elif key in choices:
            if key in {"ageRange", "heightRange"} and value.isdigit() and ((key == "ageRange" and 18 <= int(value) <= 120) or (key == "heightRange" and 140 <= int(value) <= 220)):
                column = "TIMESTAMPDIFF(YEAR,u.birthday,CURDATE())" if key == "ageRange" else "p.height"
                where.append(f"{column} = :filter_{key}")
                params[f"filter_{key}"] = int(value)
            elif value not in choices[key]:
                raise HTTPException(422, detail=f"无效的筛选条件：{key}")
            else:
                where.append(f"({choices[key][value]})")
        elif key == "mine":
            if value != "1":
                raise HTTPException(422, detail="无效的筛选条件：mine")
            where.append("ra.matchmaker_id = :filter_mine")
            params["filter_mine"] = actor.user_id
        elif key == "weight":
            weight_ranges = {
                "under40": "p.weight < 40", "40-49": "p.weight >= 40 AND p.weight < 50",
                "50-59": "p.weight >= 50 AND p.weight < 60", "60-69": "p.weight >= 60 AND p.weight < 70",
                "70-79": "p.weight >= 70 AND p.weight < 80", "over80": "p.weight >= 80",
            }
            if value in weight_ranges:
                where.append(f"({weight_ranges[value]})")
            elif value.isdigit() and 40 <= int(value) <= 120:
                # 兼容仍发送单值的旧客户端。
                where.append("p.weight = :filter_weight")
                params["filter_weight"] = int(value)
            else:
                raise HTTPException(422, detail="无效的体重")
        elif key == "education":
            labels = {"highschool": ["高中及以下"], "college": ["大专"], "bachelor": ["本科"], "master": ["硕士", "博士"], "doctor": ["博士"], "masters": ["硕士"]}
            if value not in labels:
                raise HTTPException(422, detail="无效的学历")
            education_sql = "CASE WHEN COALESCE(ua.education_verified,0) = 1 AND NULLIF(ua.education,'') IS NOT NULL THEN ua.education WHEN p.education_level = 1 THEN '高中及以下' WHEN p.education_level = 2 THEN '大专' WHEN p.education_level = 3 THEN '本科' WHEN p.education_level = 4 THEN '硕士' WHEN p.education_level = 5 THEN '博士' ELSE ua.education END"
            tokens = []
            for index, label in enumerate(labels[value]):
                param = f"filter_education_{index}"
                tokens.append(f":{param}")
                params[param] = label
            where.append(f"({education_sql}) IN ({', '.join(tokens)})")
        else:
            from app.services.regions import list_cities, list_districts, list_provinces
            parts = value.split()
            province = next((p for p in list_provinces().items if p.name == parts[0]), None)
            if province is None:
                raise HTTPException(422, detail=f"无效的地区：{value}")
            conditions = [f"p.{key}_province_code LIKE :filter_{key}_province"]
            params[f"filter_{key}_province"] = f"{province.code[:2]}%"
            if len(parts) > 1:
                cities = list_cities(province.code).items
                city = next((c for c in cities if c.name == parts[1]), None)
                if city is None and len(cities) == 1 and parts[1] == province.name:
                    city = cities[0]
                if city is None:
                    raise HTTPException(422, detail=f"无效的地区：{value}")
                conditions.append(f"p.{key}_city_code LIKE :filter_{key}_city")
                params[f"filter_{key}_city"] = f"{city.code[:4]}%"
                if len(parts) > 2:
                    district = next((d for d in list_districts(city.code).items if d.name == parts[2]), None)
                    if district is None:
                        raise HTTPException(422, detail=f"无效的地区：{value}")
                    conditions.append(f"p.{key}_district_code = :filter_{key}_district")
                    params[f"filter_{key}_district"] = district.code
            if len(parts) > 3:
                raise HTTPException(422, detail=f"无效的地区：{value}")
            legacy = []
            for index, part in enumerate(dict.fromkeys(parts)):
                param = f"filter_{key}_text_{index}"
                legacy.append(f"p.{key} LIKE :{param}")
                params[param] = f"%{part}%"
            where.append(f"(({' AND '.join(conditions)}) OR ({' AND '.join(legacy)}))")


async def list_workspace_members(
    db: AsyncSession,
    actor: WorkspaceActor,
    page: int,
    page_size: int,
    review_status: str | None,
    search: str | None,
    filters: str | None = None,
) -> WorkspaceMemberPage:
    params: dict[str, object] = {"limit": page_size, "offset": (page - 1) * page_size}
    if review_status == "REJECTED" and actor.organization_id is not None:
        params["pool_organization_id"] = actor.organization_id
        where = ["EXISTS (SELECT 1 FROM resource_assignment pool_ra WHERE pool_ra.user_id = u.id AND pool_ra.status = 1 AND pool_ra.organization_id = :pool_organization_id AND pool_ra.id = (SELECT MAX(pool_latest.id) FROM resource_assignment pool_latest WHERE pool_latest.user_id = u.id AND pool_latest.status = 1))"]
    else:
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
    if filters:
        _append_workspace_member_filters(where, params, actor, filters)
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
            u.gender, u.birthday, p.hometown, p.residence,
            p.hometown_province_code, p.hometown_city_code, p.hometown_district_code,
            p.residence_province_code, p.residence_city_code, p.residence_district_code,
            ua.education AS auth_education,
            COALESCE(ua.education_verified, 0) AS auth_education_verified,
            p.education_level,
            COALESCE(NULLIF(p.occupation, ''), ua.job) AS job,
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
        items=[WorkspaceMember(**_workspace_member_row(dict(row))) for row in rows.mappings().all()],
        page=page,
        page_size=page_size,
        total=total,
        has_more=_page(total, page, page_size),
    )


def _workspace_member_row(row: dict[str, Any]) -> dict[str, Any]:
    """把 SQL 行整理成红娘卡片：地区由编码现拼，家乡/居住地为空时不再显示空白。

    待审要审的是会员自己填的资料，而自助填写只写 user_profile（含省市区编码）；
    user_auth 里只有红娘录入或认证材料留下的旧值，自助注册会员在那张表一行都没有。
    """
    row["hometown"] = row["hometown"] or region_display(
        row.pop("hometown_province_code"), row.pop("hometown_city_code"), row.pop("hometown_district_code")
    )
    row["residence"] = row["residence"] or region_display(
        row.pop("residence_province_code"), row.pop("residence_city_code"), row.pop("residence_district_code")
    )
    row["education"] = _member_education(row)
    for key in ("hometown_province_code", "hometown_city_code", "hometown_district_code",
                "residence_province_code", "residence_city_code", "residence_district_code",
                "education_level", "auth_education", "auth_education_verified"):
        row.pop(key, None)
    return row


def _member_education(row: dict[str, Any]) -> str | None:
    """卡片上的学历：认证结论优先，其次会员自己选的档位，最后才是历史录入的文本。

    学历认证通过后认证表里的文案就是核验过的事实，覆盖会员自填档位；未认证时
    自填档位才是待审的内容，而 ``user_auth.education`` 只有红娘代录的会员才有值。
    """
    auth_education = row.get("auth_education")
    if row.get("auth_education_verified") and auth_education:
        return str(auth_education)
    education_level = row.get("education_level")
    if education_level is not None:
        label = EDUCATION_LEVEL_LABELS.get(int(education_level))
        if label is not None:
            return label
    return str(auth_education) if auth_education else None


async def pickup_workspace_member(db: AsyncSession, actor: WorkspaceActor, member_id: int) -> WorkspaceMemberPickupResult:
    """Claim a rejected member within the actor's organization and return them to review."""
    params: dict[str, object] = {"member_id": member_id}
    if actor.organization_id is not None:
        scope = "ra.organization_id = :pool_org"
        params["pool_org"] = actor.organization_id
    else:
        scope = _member_scope_predicate(actor, params)
    row = (await db.execute(text(f"""SELECT u.id, mr.status, ra.matchmaker_id
        FROM users u JOIN matchmaker_member_review mr ON mr.user_id = u.id
        JOIN resource_assignment ra ON ra.user_id = u.id AND ra.status = 1
        WHERE u.id = :member_id AND {scope}
        AND ra.id = (SELECT MAX(latest.id) FROM resource_assignment latest WHERE latest.user_id = u.id AND latest.status = 1)
        ORDER BY ra.id DESC LIMIT 1 FOR UPDATE"""), params)).mappings().first()
    if row is None:
        raise HTTPException(404, detail="会员不存在或不在当前弃海范围")
    if row["status"] != "REJECTED":
        raise HTTPException(409, detail="该会员已被捞取或不在弃海状态")
    old_owner = row["matchmaker_id"]
    await db.execute(text("UPDATE resource_assignment SET status = 2, ended_at = UTC_TIMESTAMP(), end_reason = 'reassigned' WHERE user_id = :member_id AND status = 1"), {"member_id": member_id})
    await db.execute(text("""INSERT INTO resource_assignment
        (user_id, organization_id, matchmaker_id, source, status, assigned_by)
        VALUES (:member_id, :organization_id, :actor_id, 'manual', 1, :actor_id)"""),
        {"member_id": member_id, "organization_id": actor.organization_id, "actor_id": actor.user_id})
    await db.execute(text("UPDATE matchmaker_member_review SET status = 'PENDING', reason = NULL, reviewed_by = NULL, reviewed_at = NULL, updated_at = UTC_TIMESTAMP() WHERE user_id = :member_id"), {"member_id": member_id})
    await _audit(db, actor.user_id, "matchmaker_workspace.member.pickup", "user", member_id, f"弃海捞取；原服务红娘：{old_owner}")
    await db.commit()
    return WorkspaceMemberPickupResult(member_id=member_id, matchmaker_id=actor.user_id)


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
    """受审计读取会员联系方式。

    超级红娘可查看所有会员的联系方式；普通红娘仅能查看自己服务范围内的会员。
    每次读取都会写入服务审计日志。
    """
    is_super = actor.level == "SUPER"
    if not is_super:
        await _require_scoped_member(db, actor, member_id)
    row = (
        (
            await db.execute(
                text(
                    """SELECT u.id, u.nickname, u.phone,
                        (SELECT p.wechat FROM user_profile p WHERE p.user_id = u.id LIMIT 1) AS wechat,
                        (SELECT a.real_name FROM user_auth a WHERE a.user_id = u.id LIMIT 1) AS real_name
                    FROM users u
                    WHERE u.id = :member_id
                    LIMIT 1"""
                ),
                {"member_id": member_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(404, detail="会员不存在")
    await _audit(db, actor.user_id, "matchmaker_workspace.member.contact.read", "user", member_id)
    await db.commit()
    phone = row["phone"]
    wechat = row["wechat"]
    real_name = row["real_name"]
    nickname = row["nickname"]
    return WorkspaceMemberContact(
        user_id=member_id,
        nickname=str(nickname) if nickname else None,
        real_name=str(real_name) if real_name else None,
        phone=str(phone) if phone else None,
        wechat=str(wechat) if wechat else None,
        scope="ALL" if is_super else "SERVED",
    )


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
    """某个会员的牵线记录。

    数据源是 matchmaker_introduction：红娘在工作台手工添加的牵线、以及会员在小程序点「申请认识」
    自动同步进来的牵线（record_member_application_introduction）都落这一张表，
    所以这里的口径与工作台「牵线记录」中心、待牵线/牵线成功指标完全一致。
    资源权限仍只按会员归属判定（_require_scoped_member）：会员在本红娘服务范围内，
    就能看到 TA 名下的全部牵线记录，包括其他红娘为 TA 牵的线。
    """
    await _require_scoped_member(db, actor, member_id)
    params = {"member_id": member_id, "limit": page_size, "offset": (page - 1) * page_size}
    counterpart = "CASE WHEN mi.from_user_id = :member_id THEN mi.to_user_id ELSE mi.from_user_id END"
    rows = await db.execute(
        text(
            f"""SELECT mi.id,
            {counterpart} AS counterpart_user_id,
            COALESCE(u.nickname, '未命名会员') AS counterpart_nickname,
            mi.status,
            CASE WHEN mi.from_user_id = :member_id THEN 'OUTGOING' ELSE 'INCOMING' END AS direction,
            mi.created_at, mi.updated_at
            FROM matchmaker_introduction mi LEFT JOIN users u ON u.id = {counterpart}
            WHERE mi.from_user_id = :member_id OR mi.to_user_id = :member_id
            ORDER BY mi.created_at DESC, mi.id DESC LIMIT :limit OFFSET :offset"""
        ),
        params,
    )
    total = int(
        await db.scalar(
            text("SELECT COUNT(*) FROM matchmaker_introduction WHERE from_user_id = :member_id OR to_user_id = :member_id"),
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
            WHERE ra.status = 1 AND ra.matchmaker_id IS NOT NULL
            AND """
                + _applied_matchmaker_exists("ra.matchmaker_id")
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
                f"SELECT 1 FROM users u WHERE u.id = :id AND {_applied_matchmaker_exists('u.id')} LIMIT 1"
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

# users.source（会员登记来源）字典档位，见 migrations/filters/20261011_01 列注释。
SOURCE_FROM_LEAD = "线索入库"
SOURCE_MATCHMAKER_ENTRY = "红娘录入"


async def _users_has_source(db: AsyncSession) -> bool:
    """users.source 属 20261011 补列项：未迁移的环境探测不到该列时静默不写。"""
    return bool((await db.execute(text("""SELECT 1 FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users'
        AND COLUMN_NAME = 'source'"""))).first())


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
    # 择偶要求字段单独摘出，写入 user_partner_preference。
    pref_keys = (
        "age_min", "age_max", "height_min", "height_max", "education_min", "income_min",
        "occupation_requirement", "marriage_accept", "housing_requirement", "smoking_requirement", "wedding_plan",
    )
    pref_values = {key: values.pop(key) for key in pref_keys if key in values}
    user_values = {key: values.pop(key) for key in ("phone", "nickname", "gender", "birthday", "is_married") if key in values}
    user_values["status"] = 1
    # 红娘工作台录入的会员标「红娘录入」，与自助注册/后台添加/线索入库区分。
    if await _users_has_source(db):
        user_values["source"] = SOURCE_MATCHMAKER_ENTRY
    auth_values = {key: values.pop(key) for key in ("education", "job", "real_name") if key in values}
    # 代录的资料必须同时落到会员自助档案的规范列上：主页、发现页和资料完整度读的都是
    # user_profile 的 occupation / education_level / 省市区编码，只写 user_auth 或旧文本列
    # 会让红娘录入的会员在主页一律显示「未填写」。
    if auth_values.get("job"):
        values["occupation"] = str(auth_values["job"])
    entry_education_level = education_level_from_label(auth_values.get("education"))
    if entry_education_level is not None:
        values["education_level"] = entry_education_level
    for region_field in ("hometown", "residence", "household"):
        resolved_codes = region_codes_from_text(values.get(region_field))
        for suffix, code in zip(("province_code", "city_code", "district_code"), resolved_codes):
            if code is not None:
                values[f"{region_field}_{suffix}"] = code
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
    # 录入即办号：补默认普通用户角色，会员后续用这个手机号验证码登录可直接进账号。
    await ensure_default_user_role(db, member_id)
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
    if pref_values:
        await db.execute(text(
            f"INSERT INTO user_partner_preference (user_id, {', '.join(pref_values)})"
            f" VALUES (:user_id, {', '.join(':' + key for key in pref_values)})"
            f" ON DUPLICATE KEY UPDATE {', '.join(f'{key} = VALUES({key})' for key in pref_values)}"
        ), {"user_id": member_id, **pref_values})
    # 录入的资料当场折算完整度，红娘和后台看到的是真实进度而不是恒为 0。
    await recalculate_completion(db, member_id)
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
    db: AsyncSession, actor: WorkspaceActor, request_id: int, status: str,
    body: WorkspaceMeetingStatusUpdate | None = None,
) -> None:
    params: dict[str, object] = {"id": request_id}
    clause = _meeting_scope_predicate(actor, "mr", params)
    row = (await db.execute(text(f"SELECT id, user_id, status FROM meeting_request mr WHERE mr.id = :id AND {clause} FOR UPDATE"), params)).mappings().first()
    if row is None:
        raise HTTPException(404, detail="约见申请不存在或不在服务范围")
    if row["status"] == status:
        return
    if row["status"] not in {"SUBMITTED", "CONTACTED"} or status not in {"CONTACTED", "ACCEPTED", "DECLINED", "CLOSED"}:
        raise HTTPException(409, detail="申请结果不可回退，请在约会安排中记录后续情况")
    if status == "ACCEPTED" and not (body and body.contacts_confirmed and body.applicant_agreed and body.target_agreed):
        raise HTTPException(409, detail="请先联系双方并确认双方均同意约会")
    if status in {"DECLINED", "CLOSED"} and not (body and body.reason and body.reason.strip()):
        raise HTTPException(422, detail="约会失败必须填写原因")
    await db.execute(
        text("UPDATE meeting_request SET status = :status, updated_at = UTC_TIMESTAMP() WHERE id = :id"),
        {"id": request_id, "status": status},
    )
    if status in {"DECLINED", "CLOSED"}:
        # 用户申请消耗的次数仅退回一次；红娘手工添加已是 ACCEPTED，不走此分支。
        await db.execute(text("""UPDATE matchmaker_service_quota SET available_count = available_count + 1,
            used_count = GREATEST(used_count - 1, 0), refunded_count = refunded_count + 1 WHERE user_id = :user_id"""),
            {"user_id": row["user_id"]})
    await _audit(db, actor.user_id, "matchmaker_workspace.meeting.status", "meeting_request", request_id,
                 body.reason.strip() if body and body.reason else status)
    await db.commit()


async def delete_workspace_meeting_request(
    db: AsyncSession, actor: WorkspaceActor, request_id: int
) -> None:
    params: dict[str, object] = {"id": request_id}
    clause = _meeting_scope_predicate(actor, "mr", params)
    row = await db.execute(text(f"SELECT id FROM meeting_request mr WHERE mr.id = :id AND {clause}"), params)
    if not row.scalar():
        raise HTTPException(404, detail="约见申请不存在或不在服务范围")
    await db.execute(text("DELETE FROM meeting_record WHERE request_id = :id"), {"id": request_id})
    await db.execute(text("DELETE FROM meeting_request WHERE id = :id"), {"id": request_id})
    await _audit(db, actor.user_id, "matchmaker_workspace.meeting.delete", "meeting_request", request_id, "删除约见申请记录")
    await db.commit()


async def _ensure_meeting_users_can_interact(db: AsyncSession, applicant_id: int, target_id: int) -> None:
    # social -> discovery -> workspace，延迟导入避免模块初始化环。
    from app.services.social import ensure_users_can_interact
    await ensure_users_can_interact(db, applicant_id, target_id)


async def create_workspace_meeting(
    db: AsyncSession, actor: WorkspaceActor, body: WorkspaceMeetingDirectCreate,
) -> WorkspaceMeetingRecordSummary:
    """红娘已联系双方并获得同意后，为名下申请人直接安排约会。"""
    assignment = await applicant_assignment(db, body.applicant_user_id)
    if assignment.get("matchmaker_id") != actor.user_id:
        raise HTTPException(404, detail="申请人不属于当前红娘名下")
    await _require_scoped_member(db, actor, body.target_user_id)
    await _ensure_meeting_users_can_interact(db, body.applicant_user_id, body.target_user_id)
    # 锁申请人，串行化本红娘为同一申请人发起的重复添加。
    applicant = await db.execute(text("SELECT id FROM users WHERE id = :id AND status = 1 FOR UPDATE"), {"id": body.applicant_user_id})
    if applicant.first() is None:
        raise HTTPException(404, detail="申请人不存在或不可用")
    duplicate = await db.scalar(text("""SELECT id FROM meeting_request WHERE user_id = :applicant
        AND target_user_id = :target AND status IN ('SUBMITTED', 'CONTACTED', 'ACCEPTED')
        AND NOT EXISTS (SELECT 1 FROM meeting_record m WHERE m.request_id = meeting_request.id
            AND m.status IN ('COMPLETED', 'CANCELLED', 'NO_SHOW')) LIMIT 1"""),
        {"applicant": body.applicant_user_id, "target": body.target_user_id})
    if duplicate:
        raise HTTPException(409, detail="双方已有待处理或待见面的约会，请处理原记录")
    result = await db.execute(text("""INSERT INTO meeting_request
        (user_id, target_user_id, matchmaker_id, organization_id, status, note)
        VALUES (:applicant, :target, :owner, :organization, 'ACCEPTED', :note)"""), {
        "applicant": body.applicant_user_id, "target": body.target_user_id, "owner": actor.user_id,
        "organization": assignment.get("organization_id"), "note": "红娘已联系双方并确认双方均同意约会",
    })
    request_id = int(result.lastrowid)
    await _audit(db, actor.user_id, "matchmaker_workspace.meeting.both_agreed", "meeting_request", request_id,
                 "红娘确认已联系双方，申请人和被邀请方均同意约会")
    return await schedule_workspace_meeting(db, actor, request_id, body)


async def schedule_workspace_meeting(
    db: AsyncSession, actor: WorkspaceActor, request_id: int, body: WorkspaceMeetingScheduleCreate
) -> WorkspaceMeetingRecordSummary:
    """为双方接受的约见申请创建约见安排；同一申请同时只允许一份进行中的安排。"""
    params: dict[str, object] = {"id": request_id}
    clause = _meeting_scope_predicate(actor, "mr", params)
    request_row = await db.execute(
        text(f"SELECT id, status FROM meeting_request mr WHERE mr.id = :id AND {clause} FOR UPDATE"), params
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
                """
            ),
            {"id": request_id},
        )
        or 0
    )
    if active_count > 0:
        raise HTTPException(409, detail="该申请已有约会安排，不能重复添加")
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
    clause = _meeting_scope_predicate(actor, "mr", params)
    row = await db.execute(
        text(
            f"""SELECT m.id, m.status, m.scheduled_at, mr.user_id FROM meeting_record m
            JOIN meeting_request mr ON mr.id = m.request_id
            WHERE m.id = :id AND {clause} FOR UPDATE"""
        ),
        params,
    )
    current = row.mappings().first()
    if current is None:
        raise HTTPException(404, detail="约见安排不存在或不在服务范围")
    if current["status"] in {"CANCELLED", "NO_SHOW"}:
        raise HTTPException(409, detail="失败约会不能恢复或完成")
    if current["status"] == "COMPLETED" and body.status != "COMPLETED":
        raise HTTPException(409, detail="完成约会不能回退")
    if body.status == "COMPLETED":
        scheduled_at = current["scheduled_at"]
        now = datetime.now(scheduled_at.tzinfo) if scheduled_at.tzinfo else datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
        if scheduled_at > now:
            raise HTTPException(409, detail="约会时间尚未到，不能完成约会")
        existing = await db.scalar(text(f"SELECT {_meeting_follow_up_sql()} FROM meeting_record m WHERE m.id = :id"), params)
        if existing:
            return  # 同一完成操作重复提交不重复创建跟进。
        content = (body.follow_up_content or "").strip()
        if not content:
            raise HTTPException(422, detail="完成约会前必须填写跟进记录")
        follow_up = await db.execute(text("""INSERT INTO member_follow_up (user_id, method, content, created_by)
            VALUES (:user_id, 'OTHER', :content, :actor)"""), {
            "user_id": current["user_id"], "content": content, "actor": actor.user_id,
        })
        await db.execute(text("""INSERT INTO business_audit_log
            (actor_user_id, action, resource_type, resource_id, after_json)
            VALUES (:actor, 'matchmaker_workspace.meeting.completed', 'meeting_record', :id, :payload)"""), {
            "actor": actor.user_id, "id": meeting_id,
            "payload": json.dumps({"follow_up_id": int(follow_up.lastrowid)}),
        })
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
    db: AsyncSession, actor: WorkspaceActor, introduction_id: int, status: str, failure_reason: str | None,
    contact_confirmed: bool = False,
) -> None:
    params: dict[str, object] = {"id": introduction_id}
    clause = _scope_predicate(actor, "mi", params)
    row = await db.execute(text(f"SELECT id, status, created_at, updated_at FROM matchmaker_introduction mi WHERE mi.id = :id AND {clause} FOR UPDATE"), params)
    current = row.mappings().first()
    if current is None:
        raise HTTPException(404, detail="牵线记录不存在或不在服务范围")
    if status not in {"PENDING", "IN_PROGRESS", "SUCCEEDED", "FAILED"}:
        raise HTTPException(422, detail="无效的牵线状态")
    if current["status"] == status:
        return  # 重复确认不改写结果，也不重复审计。
    if current["status"] != "IN_PROGRESS" or status not in {"SUCCEEDED", "FAILED"}:
        raise HTTPException(409, detail="待牵线需先查看双方电话；仅牵线中可确认结果，已完成记录不可回退")
    if not contact_confirmed:
        raise HTTPException(409, detail="请先确认已联系双方，并根据被牵线方态度判断结果")
    if not all(await _introduction_phone_views(db, actor.user_id, introduction_id)):
        raise HTTPException(409, detail="请先在本条牵线记录中查看双方电话")
    reason_value = failure_reason.strip() if failure_reason else None
    if status == "FAILED" and not reason_value:
        raise HTTPException(422, detail="牵线失败必须填写失败原因")
    if status == "FAILED":
        await db.execute(text(
            "UPDATE matchmaker_introduction SET status = :status, failure_reason = :reason, updated_at = UTC_TIMESTAMP() WHERE id = :id"
        ), {"id": introduction_id, "status": status, "reason": reason_value})
    else:
        await db.execute(text(
            "UPDATE matchmaker_introduction SET status = :status, failure_reason = NULL, updated_at = UTC_TIMESTAMP() WHERE id = :id"
        ), {"id": introduction_id, "status": status})
    await _audit(db, actor.user_id, "matchmaker_workspace.introduction.contact_confirmed", "matchmaker_introduction", introduction_id, status)
    await _audit(db, actor.user_id, "matchmaker_workspace.introduction.status", "matchmaker_introduction", introduction_id, status)
    await db.commit()


async def _introduction_phone_views(db: AsyncSession, actor_id: int, introduction_id: int) -> tuple[bool, bool]:
    views = (await db.execute(text("""SELECT DISTINCT action FROM business_audit_log
        WHERE actor_user_id = :actor_id AND resource_type = 'matchmaker_introduction' AND resource_id = :id
        AND id > COALESCE((SELECT MAX(reset_log.id) FROM business_audit_log reset_log
            WHERE reset_log.resource_type = 'matchmaker_introduction' AND reset_log.resource_id = :id
            AND reset_log.action = 'matchmaker_workspace.introduction.response.rejected'), 0)
        AND action IN ('matchmaker_workspace.introduction.phone.from', 'matchmaker_workspace.introduction.phone.to')"""),
        {"actor_id": actor_id, "id": introduction_id})).scalars().all()
    return ("matchmaker_workspace.introduction.phone.from" in views, "matchmaker_workspace.introduction.phone.to" in views)


async def view_workspace_introduction_contact(
    db: AsyncSession, actor: WorkspaceActor, introduction_id: int, side: str, contacts_obtained: bool = False,
) -> WorkspaceIntroductionContactViewed:
    """逐侧读取电话；同一红娘查看双方电话并明确确认获得联系方式后才推进状态。"""
    if side not in {"from", "to"}:
        raise HTTPException(422, detail="无效的牵线方")
    params: dict[str, object] = {"id": introduction_id}
    clause = _scope_predicate(actor, "mi", params)
    row = (await db.execute(text(f"""SELECT mi.id, mi.status, mi.from_user_id, mi.to_user_id,
        mi.created_at, mi.updated_at FROM matchmaker_introduction mi
        WHERE mi.id = :id AND {clause} FOR UPDATE"""), params)).mappings().first()
    if row is None:
        raise HTTPException(404, detail="牵线记录不存在或不在服务范围")
    member_id = int(row["from_user_id"] if side == "from" else row["to_user_id"])
    member = (await db.execute(text("""SELECT u.id, u.nickname, u.phone,
        (SELECT a.real_name FROM user_auth a WHERE a.user_id = u.id LIMIT 1) AS real_name
        FROM users u WHERE u.id = :member_id AND u.status = 1"""), {"member_id": member_id})).mappings().first()
    if member is None:
        raise HTTPException(404, detail="会员不存在或不可联系")
    phone = str(member["phone"] or "").strip()
    if phone:
        await _audit(db, actor.user_id, "matchmaker_workspace.introduction.phone." + side, "matchmaker_introduction", introduction_id)
    else:
        await _audit(db, actor.user_id, "matchmaker_workspace.introduction.phone.empty", "matchmaker_introduction", introduction_id, side)
    from_viewed, to_viewed = await _introduction_phone_views(db, actor.user_id, introduction_id)
    status = str(row["status"])
    if contacts_obtained and not (from_viewed and to_viewed):
        raise HTTPException(409, detail="请先查看双方电话，再确认已获得双方联系方式")
    if status == "PENDING" and from_viewed and to_viewed and contacts_obtained:
        await _audit(db, actor.user_id, "matchmaker_workspace.introduction.contacts_obtained", "matchmaker_introduction", introduction_id)
        status = "IN_PROGRESS"
        await db.execute(text("""UPDATE matchmaker_introduction SET status = 'IN_PROGRESS',
            updated_at = UTC_TIMESTAMP() WHERE id = :id AND status = 'PENDING'"""), {"id": introduction_id})
        await _audit(db, actor.user_id, "matchmaker_workspace.introduction.status", "matchmaker_introduction", introduction_id, status)
    await db.commit()
    return WorkspaceIntroductionContactViewed(
        introduction_id=introduction_id, status=status, from_phone_viewed=from_viewed, to_phone_viewed=to_viewed,
        contact=WorkspaceMemberContact(user_id=member_id, nickname=member["nickname"], real_name=member["real_name"],
            phone=phone or None, wechat=None, scope="ALL" if actor.level == "SUPER" else "SERVED"),
    )


async def create_workspace_introduction(
    db: AsyncSession, actor: WorkspaceActor, body: WorkspaceIntroductionCreate
) -> WorkspaceIntroductionCreated:
    """添加待牵线记录：双方须在工作范围内，不允许录入终态绕过跟进。"""
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
        "reason": None,
        "note": note, "applied": applied,
    })
    introduction_id = int(result.lastrowid)
    await _audit(db, actor.user_id, "matchmaker_workspace.introduction.create", "matchmaker_introduction", introduction_id, body.result)
    await db.commit()
    return WorkspaceIntroductionCreated(id=introduction_id, status=body.result)


UNASSIGNED_MATCHMAKER_ID = 0
"""matchmaker_introduction.matchmaker_id 是 NOT NULL。申请人只归属到门店、还没指定具体红娘时用它占位：
这一行不属于任何具体红娘，靠 organization_id 让该门店的超级红娘看到（_scope_predicate 的 SUPER 分支）。"""


async def _application_follow_up_owner(db: AsyncSession, user_id: int) -> tuple[int, int | None] | None:
    """解析「谁来跟进这条申请牵线」：谁发起申请就挂谁的服务红娘，没有服务红娘就挂承接门店。

    返回 None 表示这条申请不该生成牵线记录：申请人本人是服务红娘（不是会员），
    或既没有生效归属、又没配置承接门店（MEMBER_AUTO_ASSIGN_ORGANIZATION_ID 留空），挂上去也没有人看得见。
    """
    staff = await db.scalar(text("""SELECT 1 FROM user_role WHERE user_id = :user_id
        AND role_code = 'service_matchmaker' AND status = 1 LIMIT 1"""), {"user_id": user_id})
    if staff:
        return None
    assignment = (
        await db.execute(
            text("""SELECT matchmaker_id, organization_id FROM resource_assignment
                WHERE user_id = :user_id AND status = 1 ORDER BY id DESC LIMIT 1"""),
            {"user_id": user_id},
        )
    ).mappings().first()
    matchmaker_id = (
        int(assignment["matchmaker_id"])
        if assignment is not None and assignment["matchmaker_id"] is not None
        else UNASSIGNED_MATCHMAKER_ID
    )
    organization_id = (
        int(assignment["organization_id"])
        if assignment is not None and assignment["organization_id"] is not None
        else settings.member_auto_assign_organization_id
    )
    if matchmaker_id == UNASSIGNED_MATCHMAKER_ID and organization_id is None:
        return None
    return matchmaker_id, organization_id


async def record_member_application_introduction(
    db: AsyncSession, application_id: int, from_user_id: int, to_user_id: int
) -> None:
    """会员在小程序点「申请认识」= 红娘牵线管理里的一条申请牵线，落库即为待牵线。

    与红娘手工「添加牵线记录」是同一张表、同一套状态；跟进人按申请人的生效归属解析。
    note 用 discovery-apply:{申请 id} 作幂等键，同一次申请只会有一行。
    调用方（create_application）持有事务，这里不 commit，牵线记录和申请一起成功或一起回滚。
    """
    owner = await _application_follow_up_owner(db, from_user_id)
    if owner is None:
        return
    matchmaker_id, organization_id = owner
    note = f"discovery-apply:{application_id}"
    if await db.scalar(
        text("SELECT 1 FROM matchmaker_introduction WHERE note = :note LIMIT 1"), {"note": note}
    ):
        return
    await db.execute(
        text("""INSERT INTO matchmaker_introduction
            (from_user_id, to_user_id, matchmaker_id, organization_id, status, note, created_at, updated_at)
            VALUES (:f, :t, :m, :org, 'PENDING', :note, UTC_TIMESTAMP(), UTC_TIMESTAMP())"""),
        {"f": from_user_id, "t": to_user_id, "m": matchmaker_id, "org": organization_id, "note": note},
    )


async def sync_member_application_introduction(
    db: AsyncSession, from_user_id: int, to_user_id: int, *, application_id: int,
    succeeded: bool, failure_reason: str | None = None
) -> None:
    """同意直接成功；拒绝回待牵线。只处理这一条认识申请，不能改动同一对会员的其他牵线。"""
    status = "SUCCEEDED" if succeeded else "PENDING"
    reason = None if succeeded else (failure_reason or "对方暂时婉拒了你的申请")[:500]
    allowed = "('PENDING', 'IN_PROGRESS', 'FAILED')" if succeeded else "('PENDING', 'IN_PROGRESS')"
    await db.execute(
        text(f"""UPDATE matchmaker_introduction
            SET status = :status, failure_reason = :reason, updated_at = UTC_TIMESTAMP()
            WHERE from_user_id = :f AND to_user_id = :t AND note IN (:note, :admin_note) AND status IN {allowed}"""),
        {"status": status, "reason": reason, "f": from_user_id, "t": to_user_id, "note": f"discovery-apply:{application_id}", "admin_note": f"admin-entry:{application_id}"},
    )
    if not succeeded:
        # 用审计ID分隔跟进轮次，避免秒级时间戳令拒绝前的查看记录误算为本轮查看。
        await db.execute(text("""INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id)
            SELECT :actor_id, 'matchmaker_workspace.introduction.response.rejected', 'matchmaker_introduction', id
            FROM matchmaker_introduction WHERE note IN (:note, :admin_note) AND from_user_id = :f AND to_user_id = :t AND status = 'PENDING'"""),
            {"actor_id": to_user_id, "note": f"discovery-apply:{application_id}", "admin_note": f"admin-entry:{application_id}", "f": from_user_id, "t": to_user_id})


ADMIN_MATCH_RECORD_NOTE_PREFIX = "admin-entry"
"""后台「牵线记录」同步到 matchmaker_introduction 的 note 前缀，`admin-entry:{match_apply id}` 即幂等键。"""

_ADMIN_STATUS_TO_INTRODUCTION: dict[int, str] = {
    0: "PENDING",
    1: "SUCCEEDED",
    2: "FAILED",
    3: "FAILED",
}


async def _match_record_follow_up_owner(
    db: AsyncSession, from_user_id: int, to_user_id: int
) -> tuple[int, int | None] | None:
    """后台牵线的跟进人：优先发起方的具体归属红娘，其次被牵方的，两边都只有门店公海时落发起方门店。

    后台新增只填两个会员 ID，「谁发起」并不成立，所以不能直接用 §5.6 的申请人口径；
    `_application_follow_up_owner` 在没有具体红娘时会返回 0 占位的门店公海行，
    这里要继续试另一方有没有真正的归属红娘，否则牵线记录会被吸进公海没人跟。
    """
    primary = await _application_follow_up_owner(db, from_user_id)
    if primary is not None and primary[0] != UNASSIGNED_MATCHMAKER_ID:
        return primary
    counterpart = await _application_follow_up_owner(db, to_user_id)
    if counterpart is not None and counterpart[0] != UNASSIGNED_MATCHMAKER_ID:
        return counterpart
    return primary or counterpart


async def sync_admin_match_record_introduction(
    db: AsyncSession,
    record_id: int,
    from_user_id: int,
    to_user_id: int,
    status: int,
    *,
    created_at: datetime | None = None,
    failure_reason: str | None = None,
) -> None:
    """后台「牵线记录」写 match_apply 时同步一条 matchmaker_introduction。

    口径与 §5.6 的会员申请认识一致：match_apply 保持后台原有读写不变，牵线记录以
    matchmaker_introduction 为唯一事实表，红娘在工作台和小程序资料待审里看到的是同一批记录。
    跟进人按双方会员的生效归属解析（见 _match_record_follow_up_owner），
    双方都没有生效归属、又没有配置承接门店时不生成记录——挂上去也没有任何红娘看得见。
    note 用 `admin-entry:{match_apply id}` 作幂等键，且按 note 而非 (matchmaker_id, note) 查重：
    会员改派后归属红娘会变，若按联合键会重复插一条。
    调用方（后台路由）持有事务，这里不 commit，两条写入一起成功或一起回滚。
    """
    introduction_status = _ADMIN_STATUS_TO_INTRODUCTION.get(status, "PENDING")
    reason = ((failure_reason or "")[:500] or None) if introduction_status == "FAILED" else None
    note = f"{ADMIN_MATCH_RECORD_NOTE_PREFIX}:{record_id}"
    existing_id = await db.scalar(
        text("SELECT id FROM matchmaker_introduction WHERE note = :note LIMIT 1"), {"note": note}
    )
    if existing_id is not None:
        await db.execute(
            text("""UPDATE matchmaker_introduction
                SET status = :status, failure_reason = :reason, updated_at = UTC_TIMESTAMP() WHERE id = :id"""),
            {"status": introduction_status, "reason": reason, "id": int(existing_id)},
        )
        return
    owner = await _match_record_follow_up_owner(db, from_user_id, to_user_id)
    if owner is None:
        return
    matchmaker_id, organization_id = owner
    await db.execute(
        text("""INSERT INTO matchmaker_introduction
            (from_user_id, to_user_id, matchmaker_id, organization_id, status, failure_reason, note, created_at, updated_at)
            VALUES (:f, :t, :m, :org, :status, :reason, :note, COALESCE(:created_at, UTC_TIMESTAMP()), UTC_TIMESTAMP())"""),
        {
            "f": from_user_id, "t": to_user_id, "m": matchmaker_id, "org": organization_id,
            "status": introduction_status, "reason": reason, "note": note, "created_at": created_at,
        },
    )


GOOD_NEWS_DOMAIN = "good_news"
GOOD_NEWS_PENNANT_DOMAIN = "good_news_pennant"
"""红娘喜讯与锦旗墙的数据源域：后台「运营工具-喜讯管理」写这两域，工作台只读同一批行。"""

_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")


def _content_extra(row: dict) -> dict:
    try:
        extra = json.loads(row.get("extra_json") or "{}")
    except (TypeError, ValueError):
        extra = {}
    return extra if isinstance(extra, dict) else {}


def _plain_text(html: str) -> str:
    """后台喜讯正文是富文本；小程序按纯文本渲染，所以剥标签并把 &nbsp; 还原成空格。"""
    return _HTML_TAG_PATTERN.sub("", html or "").replace("&nbsp;", " ").strip()


def _meet_duration_text(raw: object) -> str:
    """后台「相识时间」按月份数字录入（历史表单口径），纯数字时补单位，其它文本原样显示。"""
    value = str(raw or "").strip()
    return f"{value}个月" if value.isdigit() else value


async def _member_profiles(db: AsyncSession, user_ids: list[int]) -> dict[int, dict]:
    if not user_ids:
        return {}
    keys = {f"profile_u{index}": value for index, value in enumerate(user_ids)}
    placeholders = ", ".join(f":{key}" for key in keys)
    rows = (
        await db.execute(text(f"SELECT id, nickname, avatar FROM users WHERE id IN ({placeholders})"), keys)
    ).mappings().all()
    return {int(row["id"]): dict(row) for row in rows}


def _positive_id(raw: object) -> int | None:
    return raw if isinstance(raw, int) and raw > 0 else None


async def list_workbench_good_news(
    db: AsyncSession, actor: WorkspaceActor, stage: str, page: int, page_size: int, item_id: int | None = None,
) -> WorkspaceGoodNewsPage:
    """红娘工作台的「红娘喜讯」列表。

    数据源是后台「运营工具-喜讯管理」写入的 admin_content_item(domain=good_news)：
    后台录一条红娘这边就能看到，不另建表，公开开关沿用 extra.public。
    范围与其它中心一致——总店（SUPER）看全部，普通红娘只看 extra.matchmaker_id 是自己
    的那些；后台代录常常先不填归属红娘，这类无归属行只有总店看得见。
    可见性上红娘端「报喜」的行会先停在待审（status=2），所以 SELF 放宽到 status IN (1, 2)
    （归属条件保证她只会看到自己代报的），SUPER 也放行归属到她名下的待审行，
    否则总店账号报完喜在自己的工作区里看不到那条记录。
    双方昵称头像按 extra.from_user_id/to_user_id 回查 users，查不到（后台手填的自定义姓名）
    才退回 extra.feedback_name/partner_name。
    """
    conditions = ["tenant_id = 1", "domain = :domain"]
    params: dict[str, object] = {
        "domain": GOOD_NEWS_DOMAIN, "limit": page_size, "offset": (page - 1) * page_size,
    }
    if stage:
        conditions.append("JSON_UNQUOTE(JSON_EXTRACT(extra_json, '$.status')) = :stage")
        params["stage"] = stage
    if item_id is not None:
        conditions.append("id = :item_id")
        params["item_id"] = item_id
    own_matchmaker_id = str(actor.user_id)
    if actor.scope == "SELF":
        conditions.append("status IN (1, 2)")
        conditions.append("JSON_UNQUOTE(JSON_EXTRACT(extra_json, '$.matchmaker_id')) = :own_matchmaker_id")
        params["own_matchmaker_id"] = own_matchmaker_id
    else:
        # 待审行只认 extra.matchmaker_id，不用 created_by：那一列在后台写入时存的是管理员 ID，
        # 与 C 端 user_id 同号段，拿它放行会把后台标为不公开的别人的行漏出来。
        conditions.append("(status = 1 OR JSON_UNQUOTE(JSON_EXTRACT(extra_json, '$.matchmaker_id')) = :own_matchmaker_id)")
        params["own_matchmaker_id"] = own_matchmaker_id
    where = " AND ".join(conditions)
    total = int(await db.scalar(text(f"SELECT COUNT(*) FROM admin_content_item WHERE {where}"), params) or 0)
    rows = (
        await db.execute(text(f"""SELECT id, title, subtitle, image_url, status, extra_json, created_at
            FROM admin_content_item WHERE {where}
            ORDER BY sort DESC, created_at DESC, id DESC LIMIT :limit OFFSET :offset"""), params)
    ).mappings().all()
    parsed = [(dict(row), _content_extra(dict(row))) for row in rows]
    member_ids = sorted({
        value for _, extra in parsed for value in (_positive_id(extra.get("from_user_id")), _positive_id(extra.get("to_user_id"))) if value
    })
    profiles = await _member_profiles(db, member_ids)
    items: list[WorkspaceGoodNewsItem] = []
    for row, extra in parsed:
        from_id = _positive_id(extra.get("from_user_id"))
        to_id = _positive_id(extra.get("to_user_id"))
        left = profiles.get(from_id or 0) or {}
        right = profiles.get(to_id or 0) or {}
        raw_images = extra.get("images")
        images = [str(url).strip() for url in raw_images if str(url or "").strip()] if isinstance(raw_images, list) else []
        if not images and str(row.get("image_url") or "").strip():
            images = [str(row["image_url"]).strip()]
        items.append(WorkspaceGoodNewsItem(
            id=int(row["id"]),
            stage=str(extra.get("status") or row.get("title") or ""),
            from_user_id=from_id,
            to_user_id=to_id,
            from_name=str(left.get("nickname") or extra.get("feedback_name") or "未命名会员"),
            to_name=str(right.get("nickname") or extra.get("partner_name") or "未命名会员"),
            from_avatar=left.get("avatar"),
            to_avatar=right.get("avatar"),
            matchmaker_name=str(extra.get("matchmaker_name") or row.get("subtitle") or "服务红娘"),
            meet_duration=_meet_duration_text(extra.get("meet_duration")),
            images=images,
            content=_plain_text(str(extra.get("content_html") or "")),
            blessing=str(extra.get("blessing") or ""),
            blessing_count=int(extra.get("blessing_count") or 0),
            public=bool(extra.get("public", True)),
            pending_review=int(row.get("status") or 1) != 1,
            created_at=row.get("created_at"),
        ))
    return WorkspaceGoodNewsPage(
        items=items, page=page, page_size=page_size, total=total, has_more=_page(total, page, page_size)
    )


async def create_workbench_good_news(
    db: AsyncSession, actor: WorkspaceActor, request: WorkspaceGoodNewsCreate
) -> WorkspaceGoodNewsCreated:
    """红娘代会员报喜，写入后台喜讯内容域的待审行。

    与后台「运营工具-喜讯管理」共用 admin_content_item(domain=good_news)，不另建表：
    红娘端没有会员选择器，双方都只存姓名文本（extra.partner_name），读侧按文本回退；
    行落 status=2 待审，由后台喜讯管理放行（status=1）后才对其它红娘和总店可见，
    提交人自己在「红娘喜讯」里能立刻看到带「待审」标记的这条。
    """
    stage = request.stage.strip()
    partner_name = request.partner_name.strip()
    if not stage or not partner_name:
        raise HTTPException(422, detail="当前状态与对方称呼都要填写")
    if stage not in {"牵手成功", "恋爱生活", "已见父母", "已订婚", "已领证", "已办婚礼", "婚后生活"}:
        raise HTTPException(422, detail="请选择有效的喜讯阶段")
    if request.consent_confirmed is False:
        raise HTTPException(422, detail="请先确认已取得相关会员同意")
    if request.client_request_id:
        from app.services.workspace_good_news import find_duplicate_submission
        existing = await find_duplicate_submission(db, actor, GOOD_NEWS_DOMAIN, request.client_request_id)
        if existing is not None:
            return existing
    images = [url.strip() for url in request.images if url.strip()][:9]
    extra = {
        "status": stage,
        "partner_name": partner_name,
        "matchmaker_id": str(actor.user_id),
        "matchmaker_name": actor.display_name,
        "meet_duration": request.meet_duration.strip(),
        "content_html": request.content.strip(),
        "images": images,
        "blessing_count": 0,
        "source": "matchmaker_workspace",
        "feedback_name": request.feedback_name.strip(),
        "consent_confirmed": request.consent_confirmed is True,
        "public": request.consent_confirmed is True,
        "client_request_id": request.client_request_id,
    }
    result = await db.execute(
        text(
            "INSERT INTO admin_content_item "
            "(tenant_id, domain, title, subtitle, image_url, status, sort, extra_json, created_by) "
            "VALUES (1, :domain, :title, :subtitle, :image_url, 2, 0, :extra, :created_by)"
        ),
        {
            "domain": GOOD_NEWS_DOMAIN,
            "title": stage,
            "subtitle": actor.display_name,
            "image_url": images[0] if images else None,
            "extra": json.dumps(extra, ensure_ascii=False),
            "created_by": actor.user_id,
        },
    )
    await db.commit()
    return WorkspaceGoodNewsCreated(id=int(result.lastrowid or 0), pending_review=True)


async def list_workbench_good_news_pennants(
    db: AsyncSession, actor: WorkspaceActor, page: int, page_size: int, item_id: int | None = None,
) -> WorkspacePennantPage:
    """锦旗墙（domain=good_news_pennant）。

    新提交按 `extra.target_user_id` 绑定受赠红娘，改名不影响归属；
    无 ID 的后台历史记录仍按 `extra.target` 显示名兼容匹配。
    """
    conditions = ["tenant_id = 1", "domain = :domain", "status IN (1, 2)"]
    params: dict[str, object] = {
        "domain": GOOD_NEWS_PENNANT_DOMAIN, "limit": page_size, "offset": (page - 1) * page_size,
    }
    if actor.scope == "SELF":
        conditions.append("""(JSON_UNQUOTE(JSON_EXTRACT(extra_json, '$.target_user_id')) = :own_id
            OR (JSON_EXTRACT(extra_json, '$.target_user_id') IS NULL
                AND JSON_UNQUOTE(JSON_EXTRACT(extra_json, '$.target')) = :own_target))""")
        params["own_target"] = actor.display_name
        params["own_id"] = str(actor.user_id)
    else:
        conditions.append("(status = 1 OR JSON_UNQUOTE(JSON_EXTRACT(extra_json, '$.target_user_id')) = :own_id)")
        params["own_id"] = str(actor.user_id)
    if item_id is not None:
        conditions.append("id = :item_id")
        params["item_id"] = item_id
    where = " AND ".join(conditions)
    total = int(await db.scalar(text(f"SELECT COUNT(*) FROM admin_content_item WHERE {where}"), params) or 0)
    rows = (
        await db.execute(text(f"""SELECT id, title, subtitle, image_url, status, extra_json, created_at
            FROM admin_content_item WHERE {where}
            ORDER BY sort DESC, created_at DESC, id DESC LIMIT :limit OFFSET :offset"""), params)
    ).mappings().all()
    items = []
    for raw in rows:
        row = dict(raw)
        extra = _content_extra(row)
        items.append(WorkspacePennantItem(
            id=int(row["id"]),
            maker=str(extra.get("maker") or ""),
            giver=str(extra.get("giver") or row.get("title") or ""),
            target=str(extra.get("target") or row.get("subtitle") or ""),
            words=str(extra.get("words") or ""),
            image_url=row.get("image_url"),
            created_at=row.get("created_at"),
            left_words=str(extra.get("left_words") or ""),
            right_words=str(extra.get("right_words") or ""),
            date_text=str(extra.get("date_text") or ""),
            target_user_id=_positive_id(extra.get("target_user_id")),
            pending_review=int(row.get("status") or 1) != 1,
        ))
    return WorkspacePennantPage(
        items=items, page=page, page_size=page_size, total=total, has_more=_page(total, page, page_size)
    )

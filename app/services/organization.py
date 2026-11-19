"""组织、门店、资源归属和推广团队服务。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser
from app.core.config import settings
from app.schemas.organization import (
    PartnerJoinCreate,
    PartnerMembershipResponse,
    PartnerTeamCreate,
    PartnerTeamResponse,
    PromotionAttributionCreate,
    PromotionAttributionResponse,
    PromotionTouchCreate,
    PromotionTouchResponse,
    ResourceAssignmentCreate,
    ResourceAssignmentResponse,
    StoreCreate,
    StoreMemberCreate,
    StoreMemberResponse,
    StoreResponse,
)


def _dt(value: Any) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


async def _audit(db: AsyncSession, actor_id: int, action: str, resource_type: str, resource_id: int, reason: str | None = None) -> None:
    await db.execute(text("""INSERT INTO business_audit_log
        (actor_user_id, action, resource_type, resource_id, reason)
        VALUES (:actor_id, :action, :resource_type, :resource_id, :reason)"""), {
        "actor_id": actor_id, "action": action, "resource_type": resource_type,
        "resource_id": resource_id, "reason": reason,
    })


async def _require_role(db: AsyncSession, user_id: int, role_code: str) -> None:
    result = await db.execute(text("""SELECT 1 FROM user_role
        WHERE user_id = :user_id AND role_code = :role_code AND status = 1 LIMIT 1"""), {
        "user_id": user_id, "role_code": role_code,
    })
    if not result.scalar():
        raise HTTPException(403, detail="当前账号没有所需业务身份")


async def create_store(db: AsyncSession, admin: CurrentUser, request: StoreCreate) -> StoreResponse:
    duplicate = await db.execute(text("SELECT 1 FROM organization WHERE code = :code LIMIT 1"), {"code": request.code})
    if duplicate.scalar():
        raise HTTPException(409, detail="门店编码已存在")
    result = await db.execute(text("""INSERT INTO organization
        (org_type, code, name, display_name, region_code, auto_redirect, created_by)
        VALUES ('store', :code, :name, :display_name, :region_code, :auto_redirect, :created_by)"""), {
        "code": request.code, "name": request.name, "display_name": request.display_name,
        "region_code": request.region_code, "auto_redirect": int(request.auto_redirect), "created_by": admin.id,
    })
    store_id = int(result.lastrowid)
    await _audit(db, admin.id, "organization.create", "organization", store_id)
    await db.commit()
    return await get_store(db, store_id)


async def get_store(
    db: AsyncSession,
    store_id: int,
    *,
    scope: str | None = None,
    params: dict[str, object] | None = None,
) -> StoreResponse:
    """读取门店。

    ``scope`` / ``params`` 由调用方（后台路由）从 ``CurrentMatchmakerAdmin`` 的四档
    作用域谓词取得（D-10）。越权访问统一按 404「门店不存在」返回，不区分状态码。
    """
    clause = f" AND ({scope})" if scope else ""
    result = await db.execute(text(f"""SELECT id, code, name, display_name, region_code, status,
        auto_redirect, created_at FROM organization WHERE id = :id AND org_type = 'store'{clause}"""),
        {**(params or {}), "id": store_id})
    row = result.mappings().first()
    if not row:
        raise HTTPException(404, detail="门店不存在")
    return StoreResponse(**{**dict(row), "auto_redirect": bool(row["auto_redirect"])})


async def list_stores(
    db: AsyncSession,
    *,
    scope: str | None = None,
    params: dict[str, object] | None = None,
) -> list[StoreResponse]:
    """列出门店。传入 ``scope`` 时按四档数据范围过滤（D-10）。"""
    clause = f" AND ({scope})" if scope else ""
    result = await db.execute(text(f"""SELECT id, code, name, display_name, region_code, status,
        auto_redirect, created_at FROM organization WHERE org_type = 'store'{clause}
        ORDER BY id DESC"""), params or {})
    return [StoreResponse(**{**dict(row), "auto_redirect": bool(row["auto_redirect"])}) for row in result.mappings().all()]


async def add_store_member(db: AsyncSession, admin: CurrentUser, store_id: int, request: StoreMemberCreate) -> StoreMemberResponse:
    await get_store(db, store_id)
    await _require_role(db, request.user_id, "user")
    if request.role_code == "store_manager":
        existing = await db.execute(text("""SELECT id FROM organization_member
            WHERE organization_id = :org_id AND role_code = 'store_manager' AND status = 1 LIMIT 1 FOR UPDATE"""), {"org_id": store_id})
        if existing.scalar():
            raise HTTPException(409, detail="一个门店只能有一个有效店长")
    duplicate = await db.execute(text("""SELECT id FROM organization_member
        WHERE organization_id = :org_id AND user_id = :user_id AND role_code = :role AND status = 1 LIMIT 1"""), {
        "org_id": store_id, "user_id": request.user_id, "role": request.role_code,
    })
    if duplicate.scalar():
        raise HTTPException(409, detail="用户已是该门店成员")
    result = await db.execute(text("""INSERT INTO organization_member
        (organization_id, user_id, role_code, granted_by)
        VALUES (:org_id, :user_id, :role, :admin_id)"""), {
        "org_id": store_id, "user_id": request.user_id, "role": request.role_code, "admin_id": admin.id,
    })
    member_id = int(result.lastrowid)
    await _audit(db, admin.id, "organization.member.add", "organization_member", member_id)
    await db.commit()
    result = await db.execute(text("""SELECT id, organization_id, user_id, role_code, status,
        started_at, ended_at FROM organization_member WHERE id = :id"""), {"id": member_id})
    return StoreMemberResponse(**dict(result.mappings().one()))


async def assign_resource(db: AsyncSession, admin: CurrentUser, request: ResourceAssignmentCreate) -> ResourceAssignmentResponse:
    if request.matchmaker_id is None and request.organization_id is None:
        raise HTTPException(422, detail="至少指定门店或服务红娘")
    if request.matchmaker_id is not None:
        await _require_role(db, request.matchmaker_id, "service_matchmaker")
    if request.organization_id is not None:
        await get_store(db, request.organization_id)
    await db.execute(text("""UPDATE resource_assignment SET status = 2,
        ended_at = UTC_TIMESTAMP(), end_reason = 'reassigned'
        WHERE user_id = :user_id AND status = 1"""), {"user_id": request.user_id})
    result = await db.execute(text("""INSERT INTO resource_assignment
        (user_id, organization_id, matchmaker_id, source, assigned_by)
        VALUES (:user_id, :organization_id, :matchmaker_id, :source, :admin_id)"""), {
        "user_id": request.user_id, "organization_id": request.organization_id,
        "matchmaker_id": request.matchmaker_id, "source": request.source, "admin_id": admin.id,
    })
    assignment_id = int(result.lastrowid)
    await _audit(db, admin.id, "resource.assign", "resource_assignment", assignment_id)
    await db.commit()
    result = await db.execute(text("""SELECT id, user_id, organization_id, matchmaker_id,
        source, status, effective_at, ended_at FROM resource_assignment WHERE id = :id"""), {"id": assignment_id})
    return ResourceAssignmentResponse(**dict(result.mappings().one()))


async def auto_assign_new_member(db: AsyncSession, user_id: int, basic_profile_done: bool) -> bool:
    """自然注册的会员填完基础信息，就自动进公海门店并挂上资料待审。

    红娘工作台的可见范围由 resource_assignment 决定：没有生效归属的会员在任何红娘的待审列表里都不存在。
    这里补的是「让门店超级红娘看得到」这一步，不替会员指定具体红娘，指定仍走人工分派。
    """
    organization_id = settings.member_auto_assign_organization_id
    if organization_id is None or not basic_profile_done:
        return False
    # 服务红娘自己也在填资料，不该被当成新会员推进待审池。
    staff = await db.scalar(text("""SELECT 1 FROM user_role WHERE user_id = :user_id
        AND role_code = 'service_matchmaker' AND status = 1 LIMIT 1"""), {"user_id": user_id})
    if staff:
        return False
    # 已有生效归属（后台分派、线索入库、红娘录入）一律不动，不覆盖人工指定的人。
    assigned = await db.scalar(text("""SELECT 1 FROM resource_assignment
        WHERE user_id = :user_id AND status = 1 LIMIT 1"""), {"user_id": user_id})
    if assigned:
        return False
    store = await db.scalar(text("""SELECT 1 FROM organization WHERE id = :id
        AND org_type = 'store' AND status = 1 LIMIT 1"""), {"id": organization_id})
    if not store:
        return False
    result = await db.execute(text("""INSERT INTO resource_assignment
        (user_id, organization_id, matchmaker_id, source, status, assigned_by)
        VALUES (:user_id, :organization_id, NULL, 'self_registered', 1, NULL)"""), {
        "user_id": user_id, "organization_id": organization_id,
    })
    assignment_id = int(result.lastrowid)
    # INSERT IGNORE：红娘已经判过的 PASSED/REJECTED 不会因为会员事后补资料又被打回待审。
    await db.execute(text("""INSERT IGNORE INTO matchmaker_member_review (user_id, status, reviewed_by)
        VALUES (:user_id, 'PENDING', NULL)"""), {"user_id": user_id})
    await db.execute(text("""INSERT INTO business_audit_log
        (actor_user_id, action, resource_type, resource_id, reason)
        VALUES (NULL, 'member.auto_assign', 'resource_assignment', :assignment_id, :reason)"""), {
        "assignment_id": assignment_id,
        "reason": f"基础信息填写完成，自动分派门店 {organization_id}",
    })
    return True


async def create_touch(db: AsyncSession, current: CurrentUser, request: PromotionTouchCreate) -> PromotionTouchResponse:
    await _require_role(db, current.id, "promoter")
    if request.promoter_id != current.id:
        raise HTTPException(403, detail="只能创建自己的推广触点")
    duplicate = await db.execute(text("SELECT 1 FROM promotion_touch WHERE code = :code"), {"code": request.code})
    if duplicate.scalar():
        raise HTTPException(409, detail="推广码已存在")
    result = await db.execute(text("""INSERT INTO promotion_touch (code, promoter_id, partner_team_id)
        VALUES (:code, :promoter_id, :team_id)"""), {
        "code": request.code, "promoter_id": current.id, "team_id": request.partner_team_id,
    })
    touch_id = int(result.lastrowid)
    await db.commit()
    result = await db.execute(text("""SELECT id, code, promoter_id, partner_team_id,
        registered_user_id, created_at FROM promotion_touch WHERE id = :id"""), {"id": touch_id})
    return PromotionTouchResponse(**dict(result.mappings().one()))


async def attribute_promotion(db: AsyncSession, current: CurrentUser, request: PromotionAttributionCreate) -> PromotionAttributionResponse:
    existing = await db.execute(text("""SELECT id FROM promotion_attribution
        WHERE user_id = :user_id AND status = 1 LIMIT 1 FOR UPDATE"""), {"user_id": current.id})
    if existing.scalar():
        raise HTTPException(409, detail="当前用户已有有效推广归属")
    touch_result = await db.execute(text("""SELECT id, promoter_id, partner_team_id, registered_user_id
        FROM promotion_touch WHERE code = :code FOR UPDATE"""), {"code": request.code})
    touch = touch_result.mappings().first()
    if not touch:
        raise HTTPException(404, detail="推广码不存在")
    if touch["promoter_id"] is None:
        raise HTTPException(422, detail="该代码仅用于合伙团队邀请，不能用于会员推广归属")
    if int(touch["promoter_id"]) == current.id:
        raise HTTPException(422, detail="不能将自己归属到自己的推广码")
    await db.execute(text("UPDATE promotion_touch SET registered_user_id = :user_id WHERE id = :id"), {"user_id": current.id, "id": touch["id"]})
    result = await db.execute(text("""INSERT INTO promotion_attribution
        (user_id, promoter_id, touch_id) VALUES (:user_id, :promoter_id, :touch_id)"""), {
        "user_id": current.id, "promoter_id": touch["promoter_id"], "touch_id": touch["id"],
    })
    attribution_id = int(result.lastrowid)
    await db.commit()
    result = await db.execute(text("""SELECT id, user_id, promoter_id, touch_id,
        status, effective_at, ended_at FROM promotion_attribution WHERE id = :id"""), {"id": attribution_id})
    return PromotionAttributionResponse(**dict(result.mappings().one()))


async def create_partner_team(db: AsyncSession, admin: CurrentUser, request: PartnerTeamCreate) -> PartnerTeamResponse:
    await _require_role(db, request.owner_user_id, "partner")
    duplicate = await db.execute(text("SELECT 1 FROM partner_team WHERE owner_user_id = :user_id"), {"user_id": request.owner_user_id})
    if duplicate.scalar():
        raise HTTPException(409, detail="该用户已有合伙团队")
    result = await db.execute(text("""INSERT INTO partner_team (owner_user_id, name, open_mode)
        VALUES (:owner, :name, :open_mode)"""), {"owner": request.owner_user_id, "name": request.name, "open_mode": request.open_mode})
    team_id = int(result.lastrowid)
    await _audit(db, admin.id, "partner_team.create", "partner_team", team_id)
    await db.commit()
    result = await db.execute(text("""SELECT id, owner_user_id, name, status, open_mode, created_at
        FROM partner_team WHERE id = :id"""), {"id": team_id})
    return PartnerTeamResponse(**dict(result.mappings().one()))


async def join_partner_team(db: AsyncSession, current: CurrentUser, request: PartnerJoinCreate) -> PartnerMembershipResponse:
    await _require_role(db, current.id, "promoter")
    existing = await db.execute(text("""SELECT id FROM partner_membership
        WHERE promoter_id = :promoter_id AND status = 1 LIMIT 1 FOR UPDATE"""), {"promoter_id": current.id})
    if existing.scalar():
        raise HTTPException(409, detail="推广红娘只能加入一个有效团队")
    team = await db.execute(text("SELECT id FROM partner_team WHERE id = :id AND status = 1"), {"id": request.team_id})
    if not team.scalar():
        raise HTTPException(404, detail="合伙团队不存在或已关闭")
    result = await db.execute(text("""INSERT INTO partner_membership (team_id, promoter_id)
        VALUES (:team_id, :promoter_id)"""), {"team_id": request.team_id, "promoter_id": current.id})
    membership_id = int(result.lastrowid)
    await db.commit()
    result = await db.execute(text("""SELECT id, team_id, promoter_id, status, joined_at, left_at
        FROM partner_membership WHERE id = :id"""), {"id": membership_id})
    return PartnerMembershipResponse(**dict(result.mappings().one()))

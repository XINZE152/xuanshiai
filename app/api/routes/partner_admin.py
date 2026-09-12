"""Routes for 合伙红娘 → 合伙人管理 / 团队关系 / 分成明细 pages."""

from fastapi import APIRouter, Body, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    CurrentMatchmakerAdmin,
    get_current_matchmaker_admin,
)
from app.db.session import get_db
from app.schemas.partner_admin import (
    PartnerCommissionEntryCreate,
    PartnerCommissionEntryCreateResult,
    PartnerCommissionEntryOptions,
    PartnerCommissionEntryPage,
    PartnerRelationBind,
    PartnerRelationPage,
    PartnerRelationRemove,
    PartnerRelationResult,
    PartnerStaffCreate,
    PartnerStaffDetail,
    PartnerStaffPage,
    PartnerStaffUpdate,
    PartnerStatistics,
    PartnerTeamOption,
    PartnerUserCandidate,
)
from app.services import partner_admin as service

router = APIRouter(prefix="/admin/partners")
relation_router = APIRouter(prefix="/admin/partner-relations")

_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"


# ─── 合伙人管理 ────────────────────────────────────────────────
# 注意：静态段（statistics / user-candidates / commission-entries）必须声明在
# /{team_id} 之前，否则会被动态段吞掉。


@router.get("", response_model=PartnerStaffPage, summary="分页查询合伙人")
async def list_partners(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = Query(None, max_length=100),
    level_id: int | None = Query(None, ge=1, le=3),
    status: int | None = Query(None, ge=1, le=3),
    sort: str = Query("created_desc", max_length=32),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> PartnerStaffPage:
    current.require("matchmaker.read")
    return await service.list_partners(db, page, page_size, keyword, level_id, status, sort)


@router.post("", response_model=PartnerStaffDetail, status_code=201, summary="添加合伙人（绑定用户建团队）")
async def create_partner(
    body: PartnerStaffCreate = Body(...),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> PartnerStaffDetail:
    current.require("matchmaker.manage")
    return await service.create_partner(db, current.account.id, body)


@router.get("/statistics", response_model=PartnerStatistics, summary="合伙人统计卡")
async def partner_statistics(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> PartnerStatistics:
    current.require("matchmaker.read")
    return await service.partner_statistics(db)


@router.get("/user-candidates", response_model=list[PartnerUserCandidate], summary="搜索可绑定为合伙人的用户")
async def partner_user_candidates(
    keyword: str = Query(..., min_length=1, max_length=64),
    limit: int = Query(10, ge=1, le=50),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> list[PartnerUserCandidate]:
    current.require("matchmaker.read")
    return await service.search_user_candidates(db, keyword, limit)


@router.get("/team-options", response_model=list[PartnerTeamOption], summary="合伙团队下拉")
async def partner_team_options(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> list[PartnerTeamOption]:
    current.require("matchmaker.read")
    return await service.list_team_options(db)


@router.get(
    "/commission-entries",
    response_model=PartnerCommissionEntryPage,
    summary="分页查询合伙人分成明细",
)
async def partner_commission_entries(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    partner_id: int | None = Query(None, ge=1, description="合伙人（团队 owner）user id"),
    rule_id: int | None = Query(None, ge=1, description="分成事件"),
    start_date: str | None = Query(None, pattern=_DATE_PATTERN),
    end_date: str | None = Query(None, pattern=_DATE_PATTERN),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> PartnerCommissionEntryPage:
    current.require("matchmaker.read")
    return await service.list_commission_entries(db, page, page_size, partner_id, rule_id, start_date, end_date)


@router.get(
    "/commission-entries/options",
    response_model=PartnerCommissionEntryOptions,
    summary="分成明细筛选下拉",
)
async def partner_commission_entry_options(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> PartnerCommissionEntryOptions:
    current.require("matchmaker.read")
    return await service.commission_entry_options(db)


@router.post(
    "/commission-entries",
    response_model=PartnerCommissionEntryCreateResult,
    status_code=201,
    summary="录入一笔合伙人分成",
)
async def create_partner_commission_entry(
    body: PartnerCommissionEntryCreate = Body(...),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> PartnerCommissionEntryCreateResult:
    current.require("matchmaker.manage")
    return await service.create_commission_entry(db, current.account.id, body)


@router.get("/{team_id}", response_model=PartnerStaffDetail, summary="查询合伙人详情")
async def partner_detail(
    team_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> PartnerStaffDetail:
    current.require("matchmaker.read")
    return await service.get_partner(db, team_id)


@router.put("/{team_id}", response_model=PartnerStaffDetail, summary="编辑合伙人")
async def partner_update(
    team_id: int = Path(..., ge=1),
    body: PartnerStaffUpdate = Body(...),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> PartnerStaffDetail:
    current.require("matchmaker.manage")
    return await service.update_partner(db, team_id, body, current.account.id)


@router.delete("/{team_id}", summary="关闭合伙人（移出团队成员并撤销角色）")
async def partner_delete(
    team_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    current.require("matchmaker.manage")
    return await service.delete_partner(db, team_id, current.account.id)


# ─── 团队关系 ──────────────────────────────────────────────────


@relation_router.get("", response_model=PartnerRelationPage, summary="分页查询合伙人团队关系")
async def list_relations(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    team_id: int | None = Query(None, ge=1),
    keyword: str | None = Query(None, max_length=64),
    status: int | None = Query(None, ge=1, le=3),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> PartnerRelationPage:
    current.require("matchmaker.read")
    return await service.list_relations(db, page, page_size, team_id, keyword, status)


@relation_router.post("", response_model=PartnerRelationResult, status_code=201, summary="人工绑定团队关系")
async def bind_relation(
    body: PartnerRelationBind = Body(...),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> PartnerRelationResult:
    current.require("matchmaker.manage")
    return await service.bind_relation(db, body, current.account.id)


@relation_router.post("/{relation_id}/remove", response_model=PartnerRelationResult, summary="移出团队")
async def remove_relation(
    relation_id: int = Path(..., ge=1),
    body: PartnerRelationRemove = Body(...),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> PartnerRelationResult:
    current.require("matchmaker.manage")
    return await service.remove_relation(db, relation_id, body, current.account.id)

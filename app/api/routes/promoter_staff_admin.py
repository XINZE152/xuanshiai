"""Routes for the 推广红娘 management back-office page (总店红娘后台)."""

from typing import Literal

from fastapi import APIRouter, Body, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    CurrentMatchmakerAdmin,
    get_current_matchmaker_admin,
)
from app.db.session import get_db
from app.schemas.promoter_staff_admin import (
    PromoterCommissionEntryOptions,
    PromoterCommissionEntryPage,
    PromoterDeleteResponse,
    PromoterPlatformTokenResponse,
    PromoterPosterResponse,
    PromoterStaffCreate,
    PromoterStaffDetail,
    PromoterStaffPage,
    PromoterStaffUpdate,
    PromoterStatistics,
    PromoterStatusUpdate,
    PromoterTeamItem,
    PromoterTeamUpdate,
    PromoterUserCandidate,
)
from app.services import promoter_staff_admin as service

router = APIRouter(prefix="/admin/promoters")


@router.get("", response_model=PromoterStaffPage, summary="分页查询推广红娘")
async def list_promoters(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = Query(None, max_length=100, description="昵称/姓名/手机号关键字"),
    status: int | None = Query(None, ge=1, le=2, description="1 在职 2 离职，不传为全部"),
    commission_level_id: int | None = Query(None, ge=1, le=4, description="分成级别 1-4"),
    team_id: int | None = Query(None, ge=1, description="隶属合伙团队 ID"),
    visible: bool | None = Query(None, description="是否前台展示"),
    sort: Literal["joined_desc", "joined_asc", "member_desc", "member_asc"] = Query(
        "joined_desc", description="排序：加入时间升/降、名下会员数升/降"
    ),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> PromoterStaffPage:
    current.require("matchmaker.read")
    return await service.list_promoters(
        db, page, page_size, keyword, status, commission_level_id, team_id, visible, sort
    )


@router.get("/statistics", response_model=PromoterStatistics, summary="推广红娘统计卡")
async def promoter_statistics(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> PromoterStatistics:
    current.require("matchmaker.read")
    return await service.promoter_statistics(db)


@router.get("/user-candidates", response_model=list[PromoterUserCandidate], summary="搜索可绑定的普通用户")
async def promoter_user_candidates(
    keyword: str = Query(..., min_length=2, max_length=100),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> list[PromoterUserCandidate]:
    current.require("matchmaker.read")
    return await service.search_user_candidates(db, keyword.strip())


@router.get("/teams", response_model=list[PromoterTeamItem], summary="合伙团队下拉")
async def promoter_teams(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> list[PromoterTeamItem]:
    current.require("matchmaker.read")
    return await service.list_promoter_teams(db)


@router.get(
    "/commission-entries",
    response_model=PromoterCommissionEntryPage,
    summary="推广红娘分成流水",
)
async def promoter_commission_entries(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    promoter_id: int | None = Query(None, ge=1),
    rule_id: int | None = Query(None, ge=1),
    start_date: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end_date: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> PromoterCommissionEntryPage:
    current.require("matchmaker.read")
    return await service.list_promoter_commission_entries(
        db, page, page_size, promoter_id, rule_id, start_date, end_date
    )


@router.get(
    "/commission-entries/options",
    response_model=PromoterCommissionEntryOptions,
    summary="分成明细筛选下拉",
)
async def promoter_commission_entry_options(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> PromoterCommissionEntryOptions:
    current.require("matchmaker.read")
    return await service.promoter_commission_entry_options(db)


@router.post("", response_model=PromoterStaffDetail, status_code=201, summary="添加推广红娘")
async def create_promoter(
    body: PromoterStaffCreate = Body(...),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> PromoterStaffDetail:
    current.require("matchmaker.manage")
    return await service.create_promoter(db, current.account.id, body)


@router.get("/{user_id}", response_model=PromoterStaffDetail, summary="查询推广红娘详情")
async def get_promoter(
    user_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> PromoterStaffDetail:
    current.require("matchmaker.read")
    return await service.get_promoter(db, user_id)


@router.put("/{user_id}", response_model=PromoterStaffDetail, summary="编辑推广红娘")
async def update_promoter(
    user_id: int = Path(..., ge=1),
    body: PromoterStaffUpdate = Body(...),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> PromoterStaffDetail:
    current.require("matchmaker.manage")
    return await service.update_promoter(db, user_id, body)


@router.put("/{user_id}/team", response_model=PromoterStaffDetail, summary="变更推广红娘隶属团队")
async def update_promoter_team(
    user_id: int = Path(..., ge=1),
    body: PromoterTeamUpdate = Body(...),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> PromoterStaffDetail:
    current.require("matchmaker.manage")
    return await service.update_promoter_team(db, user_id, body, current.account.id)


@router.post("/{user_id}/poster", response_model=PromoterPosterResponse, summary="生成推广海报")
async def promoter_poster(
    user_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> PromoterPosterResponse:
    current.require("matchmaker.read")
    return await service.promoter_poster(db, user_id)


@router.post(
    "/{user_id}/platform-token",
    response_model=PromoterPlatformTokenResponse,
    summary="推广红娘平台免登",
)
async def promoter_platform_token(
    user_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> PromoterPlatformTokenResponse:
    current.require("matchmaker.read")
    return await service.promoter_platform_token(db, user_id)


@router.delete("/{user_id}", response_model=PromoterDeleteResponse, summary="删除推广红娘")
async def delete_promoter(
    user_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> PromoterDeleteResponse:
    current.require("matchmaker.manage")
    return await service.delete_promoter(db, user_id)


@router.patch("/{user_id}/status", response_model=PromoterStaffDetail, summary="推广红娘离职/复职")
async def update_promoter_status(
    user_id: int = Path(..., ge=1),
    body: PromoterStatusUpdate = Body(...),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> PromoterStaffDetail:
    current.require("matchmaker.manage")
    return await service.update_promoter(
        db, user_id, PromoterStaffUpdate(status=body.status, reason=body.reason)
    )

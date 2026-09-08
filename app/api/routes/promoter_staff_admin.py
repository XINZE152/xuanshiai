"""Routes for the 推广红娘 management back-office page (总店红娘后台)."""

from fastapi import APIRouter, Body, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    CurrentMatchmakerAdmin,
    get_current_matchmaker_admin,
)
from app.db.session import get_db
from app.schemas.promoter_staff_admin import (
    PromoterStaffCreate,
    PromoterStaffDetail,
    PromoterStaffPage,
    PromoterStaffUpdate,
    PromoterStatusUpdate,
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
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> PromoterStaffPage:
    current.require("matchmaker.read")
    return await service.list_promoters(db, page, page_size, keyword, status)


@router.get("/user-candidates", response_model=list[PromoterUserCandidate], summary="搜索可绑定的普通用户")
async def promoter_user_candidates(
    keyword: str = Query(..., min_length=2, max_length=100),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> list[PromoterUserCandidate]:
    current.require("matchmaker.read")
    return await service.search_user_candidates(db, keyword.strip())


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

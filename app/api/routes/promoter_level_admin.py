"""Routes for 推广红娘 → 分成配置 (4 固定级别) page."""

from fastapi import APIRouter, Body, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    CurrentMatchmakerAdmin,
    get_current_matchmaker_admin,
)
from app.db.session import get_db
from app.schemas.promoter_level_admin import PromoterLevelItem, PromoterLevelPage, PromoterLevelUpdate
from app.services import promoter_level_admin as service

router = APIRouter(prefix="/admin/promoter-levels")


@router.get("", response_model=PromoterLevelPage, summary="查询推广红娘 4 级分成配置")
async def list_promoter_levels(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> PromoterLevelPage:
    current.require("matchmaker.read")
    return await service.list_levels(db)


@router.get("/{level_id}", response_model=PromoterLevelItem, summary="查询单个推广红娘分成级别")
async def get_promoter_level(
    level_id: int = Path(..., ge=1, le=4),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> PromoterLevelItem:
    current.require("matchmaker.read")
    return await service.get_level(db, level_id)


@router.put("/{level_id}", response_model=PromoterLevelItem, summary="编辑推广红娘分成级别业务参数")
async def update_promoter_level(
    level_id: int = Path(..., ge=1, le=4),
    body: PromoterLevelUpdate = Body(...),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> PromoterLevelItem:
    current.require("matchmaker.manage")
    return await service.update_level(db, current.account.id, level_id, body)

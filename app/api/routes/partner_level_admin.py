"""Routes for 合伙红娘 → 分成配置 (3 固定级别) page."""

from fastapi import APIRouter, Body, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    CurrentMatchmakerAdmin,
    get_current_matchmaker_admin,
)
from app.db.session import get_db
from app.schemas.partner_level_admin import PartnerLevelItem, PartnerLevelPage, PartnerLevelUpdate
from app.services import partner_level_admin as service

router = APIRouter(prefix="/admin/partner-levels")


@router.get("", response_model=PartnerLevelPage, summary="查询合伙红娘 3 级分成配置")
async def list_partner_levels(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> PartnerLevelPage:
    current.require("matchmaker.read")
    return await service.list_levels(db)


@router.get("/{level_id}", response_model=PartnerLevelItem, summary="查询单个合伙分成级别")
async def get_partner_level(
    level_id: int = Path(..., ge=1, le=3),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> PartnerLevelItem:
    current.require("matchmaker.read")
    return await service.get_level(db, level_id)


@router.put("/{level_id}", response_model=PartnerLevelItem, summary="编辑合伙分成级别业务参数")
async def update_partner_level(
    level_id: int = Path(..., ge=1, le=3),
    body: PartnerLevelUpdate = Body(...),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> PartnerLevelItem:
    current.require("matchmaker.manage")
    return await service.update_level(db, current.account.id, level_id, body)

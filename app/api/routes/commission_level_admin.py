"""Routes serving the 红利分成分成级别 back-office page."""

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    CurrentMatchmakerAdmin,
    get_current_matchmaker_admin,
)
from app.db.session import get_db
from app.schemas.matchmaker_admin import CommissionLevel, CommissionLevelUpdate
from app.services.commission_level_admin import (
    get_level,
    list_levels,
    update_level,
)

router = APIRouter(prefix="/admin/commission-levels")


@router.get("", response_model=list[CommissionLevel], summary="查询服务红娘分成级别")
async def commission_levels(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> list[CommissionLevel]:
    return await list_levels(db)


@router.get("/{level_id}", response_model=CommissionLevel, summary="查询单个分成级别")
async def commission_level_detail(
    level_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> CommissionLevel:
    return await get_level(db, level_id)


@router.put("/{level_id}", response_model=CommissionLevel, summary="编辑服务红娘分成级别")
async def commission_level_upsert(
    level_id: int = Path(..., ge=1),
    body: CommissionLevelUpdate = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> CommissionLevel:
    return await update_level(db, current.account.id, level_id, body)
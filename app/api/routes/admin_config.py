"""Administration APIs for versioned platform and business configuration."""

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentMatchmakerAdmin, get_current_matchmaker_admin
from app.db.session import get_db
from app.schemas.admin_config import AdminConfigAuditPage, AdminConfigSnapshot, AdminConfigUpdate
from app.services.admin_config import get_config, list_audits, update_config

router = APIRouter(prefix="/admin/configs")


@router.get("/{namespace}", response_model=AdminConfigSnapshot, summary="查询配置域")
async def read_config(namespace: str = Path(..., min_length=2, max_length=64), current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> AdminConfigSnapshot:
    current.require("platform.config.read")
    return await get_config(db, namespace)


@router.patch("/{namespace}", response_model=AdminConfigSnapshot, summary="更新配置域")
async def write_config(namespace: str = Path(..., min_length=2, max_length=64), body: AdminConfigUpdate = ..., current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> AdminConfigSnapshot:
    current.require("platform.config.write")
    return await update_config(db, current.account.id, namespace, body)


@router.get("/{namespace}/audit-logs", response_model=AdminConfigAuditPage, summary="查询配置变更审计")
async def config_audits(namespace: str = Path(..., min_length=2, max_length=64), page: int = Query(1, ge=1, le=10000), page_size: int = Query(20, ge=1, le=100), current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> AdminConfigAuditPage:
    current.require("platform.config.read")
    return await list_audits(db, namespace, page, page_size)

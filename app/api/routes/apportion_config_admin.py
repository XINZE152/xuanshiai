"""总店红娘 -> 分派配置 后台 API。

URL 前缀：/admin/matchmaker/apportion-config
权限点：matchmaker.apportion.read / matchmaker.apportion.write
"""

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentMatchmakerAdmin, get_current_matchmaker_admin
from app.db.session import get_db
from app.schemas.matchmaker_admin import (
    ApportionAbandonUpdate,
    ApportionAssignUpdate,
    ApportionConfig,
    ApportionConfigAuditLogPage,
    ApportionConfigType,
    ApportionScope,
    ApportionToggleUpdate,
)
from app.services.apportion_config_admin import (
    get_config,
    list_audit_logs,
    list_configs,
    toggle_config,
    upsert_abandon,
    upsert_assign,
)


router = APIRouter(prefix="/admin/matchmaker/apportion-config")


@router.get("", response_model=list[ApportionConfig], summary="查询总店红娘分派配置（4 块）")
async def apportion_config_list(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> list[ApportionConfig]:
    return await list_configs(db)


@router.get(
    "/{scope}/{config_type}",
    response_model=ApportionConfig,
    summary="查询单块分派配置",
)
async def apportion_config_detail(
    scope: ApportionScope = Path(...),
    config_type: ApportionConfigType = Path(...),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> ApportionConfig:
    return await get_config(db, scope, config_type)


@router.put(
    "/{scope}/assign",
    response_model=ApportionConfig,
    summary="保存分配配置（assign 块）",
)
async def apportion_config_upsert_assign(
    scope: ApportionScope = Path(...),
    body: ApportionAssignUpdate = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> ApportionConfig:
    current.require("matchmaker.apportion.write")
    return await upsert_assign(db, current.account.id, scope, body)


@router.put(
    "/{scope}/abandon",
    response_model=ApportionConfig,
    summary="保存弃海配置（abandon 块）",
)
async def apportion_config_upsert_abandon(
    scope: ApportionScope = Path(...),
    body: ApportionAbandonUpdate = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> ApportionConfig:
    current.require("matchmaker.apportion.write")
    return await upsert_abandon(db, current.account.id, scope, body)


@router.patch(
    "/{scope}/{config_type}/toggle",
    response_model=ApportionConfig,
    summary="切换单块配置的启用/停用",
)
async def apportion_config_toggle(
    scope: ApportionScope = Path(...),
    config_type: ApportionConfigType = Path(...),
    body: ApportionToggleUpdate = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> ApportionConfig:
    current.require("matchmaker.apportion.write")
    return await toggle_config(db, current.account.id, scope, config_type, body)


@router.get(
    "/audit-log",
    response_model=ApportionConfigAuditLogPage,
    summary="查询分派配置审计日志",
)
async def apportion_config_audit_log(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> ApportionConfigAuditLogPage:
    return await list_audit_logs(db, page=page, page_size=page_size)
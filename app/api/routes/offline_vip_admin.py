"""线下VIP会员服务管理后台路由（M3-6）。

对应前端页面：会员CRM → 线下VIP（/love-user-vip-underline）。
"""

from fastapi import APIRouter, Body, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentMatchmakerAdmin, get_current_matchmaker_admin
from app.db.session import get_db
from app.schemas.offline_vip_admin import (
    OfflineVipCreate,
    OfflineVipItem,
    OfflineVipMeetLogPage,
    OfflineVipOptions,
    OfflineVipPage,
    OfflineVipStatistics,
    OfflineVipUpdate,
)
from app.services import offline_vip_admin as service

router = APIRouter(prefix="/admin/offline-vips")

# 服务进度合法取值（all 表示不筛选，不落库）
_PROGRESS_PATTERN = "^(matching|dating|deep|in_love|met_parents|paused|breakup|married)$"


@router.get("", response_model=OfflineVipPage, summary="分页查询线下VIP会员")
async def list_offline_vips(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    progress: str | None = Query(None, pattern=_PROGRESS_PATTERN, description="服务进度；不传为全部"),
    sales_matchmaker_id: int | None = Query(None, ge=1, description="销售红娘 users.id"),
    service_matchmaker_id: int | None = Query(None, ge=1, description="服务红娘 users.id"),
    promoter_id: int | None = Query(None, ge=1, description="推广红娘 users.id"),
    sign_start: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="签约日期起（含）"),
    sign_end: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="签约日期止（含）"),
    keyword: str | None = Query(None, max_length=64, description="会员昵称/手机/姓名/编号"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> OfflineVipPage:
    current.require("matchmaker.member.read")
    return await service.list_vips(
        db,
        page,
        page_size,
        progress,
        sales_matchmaker_id,
        service_matchmaker_id,
        promoter_id,
        sign_start,
        sign_end,
        keyword,
    )


@router.get("/statistics", response_model=OfflineVipStatistics, summary="线下VIP 统计卡")
async def offline_vip_statistics(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> OfflineVipStatistics:
    current.require("matchmaker.member.read")
    return await service.statistics(db)


@router.get("/options", response_model=OfflineVipOptions, summary="线下VIP 下拉选项")
async def offline_vip_options(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> OfflineVipOptions:
    current.require("matchmaker.member.read")
    return await service.options(db)


@router.post("", response_model=OfflineVipItem, status_code=201, summary="添加线下VIP会员")
async def create_offline_vip(
    body: OfflineVipCreate = Body(...),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> OfflineVipItem:
    current.require("matchmaker.member.manage")
    return await service.create_vip(db, current.account.id, body)


@router.get("/{vip_id}", response_model=OfflineVipItem, summary="查询线下VIP详情")
async def get_offline_vip(
    vip_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> OfflineVipItem:
    current.require("matchmaker.member.read")
    return await service.get_vip(db, vip_id)


@router.put("/{vip_id}", response_model=OfflineVipItem, summary="编辑线下VIP会员服务信息")
async def update_offline_vip(
    vip_id: int = Path(..., ge=1),
    body: OfflineVipUpdate = Body(...),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> OfflineVipItem:
    current.require("matchmaker.member.manage")
    return await service.update_vip(db, vip_id, current.account.id, body)


@router.get("/{vip_id}/meet-logs", response_model=OfflineVipMeetLogPage, summary="成功约见次数修改记录")
async def offline_vip_meet_logs(
    vip_id: int = Path(..., ge=1),
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> OfflineVipMeetLogPage:
    current.require("matchmaker.member.read")
    return await service.list_meet_logs(db, vip_id, page, page_size)

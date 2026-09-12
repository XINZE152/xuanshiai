"""会员服务-推广管理（推广服务订单）后台路由。"""

from fastapi import APIRouter, Body, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentMatchmakerAdmin, get_current_matchmaker_admin
from app.db.session import get_db
from app.schemas.promotion_order_admin import (
    PromotionOrder,
    PromotionOrderPage,
    PromotionOrderStatistics,
    PromotionOrderUpdate,
)
from app.services import promotion_order_admin as service

router = APIRouter(prefix="/admin/promotion-orders")


@router.get("", response_model=PromotionOrderPage, summary="查询推广服务订单")
async def order_list(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    pay_status: str | None = Query(None, pattern="^(unpaid|paid|refunded)$"),
    status: str | None = Query(None, pattern="^(pending|processing|done|cancelled)$"),
    search: str | None = Query(None, max_length=64, description="订单号/昵称/套餐名"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> PromotionOrderPage:
    current.require("matchmaker.service.read")
    return await service.list_orders(db, page, page_size, pay_status, status, search)


@router.get("/statistics", response_model=PromotionOrderStatistics, summary="推广服务订单统计")
async def order_stats(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> PromotionOrderStatistics:
    current.require("matchmaker.service.read")
    return await service.order_statistics(db)


@router.get("/{order_id}", response_model=PromotionOrder, summary="推广服务订单详情")
async def order_detail(
    order_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> PromotionOrder:
    current.require("matchmaker.service.read")
    return await service.get_order(db, order_id)


@router.patch("/{order_id}", response_model=PromotionOrder, summary="更新推广服务订单")
async def order_update(
    order_id: int = Path(..., ge=1),
    body: PromotionOrderUpdate = Body(...),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> PromotionOrder:
    current.require("matchmaker.service.manage")
    return await service.update_order(db, order_id, body, current.account.id)


@router.delete("/{order_id}", summary="删除推广服务订单")
async def order_delete(
    order_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    current.require("matchmaker.service.manage")
    deleted = await service.delete_order(db, order_id, current.account.id)
    return {"id": order_id, "deleted": deleted}

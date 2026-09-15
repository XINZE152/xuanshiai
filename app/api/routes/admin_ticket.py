"""平台工单反馈（system-feedback）后台路由。

前缀 ``/admin/tickets``；读走 ``matchmaker.system.read``（通用系统管理权限），
写走 ``matchmaker.system.manage``。

权限点集中：所有 ticket 接口走 system.* 权限点，与 system-setting-* 复用同一权限组。
"""

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    CurrentMatchmakerAdmin,
    get_current_matchmaker_admin,
)
from app.db.session import get_db
from app.schemas.admin_ticket import (
    AdminTicketCreate,
    AdminTicketPage,
    AdminTicketReply,
    AdminTicketStatistics,
    AdminTicketStatusUpdate,
)
from app.services import admin_ticket as service


router = APIRouter(prefix="/admin/tickets")


@router.get("", response_model=AdminTicketPage, summary="工单列表")
async def list_tickets(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    status: str = Query("all", pattern="^(all|待处理|处理中|已处理)$"),
    feedback_type: str = Query("all", pattern="^(all|BUG|咨询|建议)$"),
    keyword: str | None = Query(None, max_length=64),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminTicketPage:
    current.require("matchmaker.system.read")
    return await service.list_tickets(
        db,
        page=page,
        page_size=page_size,
        status=status,
        feedback_type=feedback_type,
        keyword=keyword,
    )


@router.get("/statistics", response_model=AdminTicketStatistics, summary="工单状态统计")
async def ticket_statistics(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminTicketStatistics:
    current.require("matchmaker.system.read")
    return await service.ticket_statistics(db)


@router.post("", summary="提交工单")
async def create_ticket(
    body: AdminTicketCreate,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
):
    current.require("matchmaker.system.manage")
    return await service.create_ticket(
        db,
        body=body,
        submitter_user_id=current.account.matchmaker_user_id or current.account.id,
        submitter_name=current.account.display_name,
    )


@router.post("/{ticket_id}/reply", summary="回复工单")
async def reply_ticket(
    ticket_id: int = Path(..., ge=1),
    body: AdminTicketReply = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
):
    current.require("matchmaker.system.manage")
    return await service.reply_ticket(
        db, ticket_id=ticket_id, admin_id=current.account.id, body=body
    )


@router.patch("/{ticket_id}/status", summary="流转工单状态（待处理→处理中）")
async def update_ticket_status(
    ticket_id: int = Path(..., ge=1),
    body: AdminTicketStatusUpdate = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
):
    current.require("matchmaker.system.manage")
    return await service.update_ticket_status(
        db, ticket_id=ticket_id, admin_id=current.account.id, new_status=body.status
    )

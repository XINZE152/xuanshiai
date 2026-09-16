"""Independent matchmaker back-office account administration routes."""

from datetime import datetime

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentMatchmakerAdmin, get_current_matchmaker_admin
from app.db.session import get_db
from app.schemas.admin_account_cancellation import (
    AdminAccountCancellationItem,
    AdminAccountCancellationPage,
    AdminAccountCancellationReview,
)
from app.schemas.matchmaker_admin_account import (
    MatchmakerAdminAccountCreate,
    MatchmakerAdminAccountItem,
    MatchmakerAdminAccountPage,
    MatchmakerAdminAccountStatusUpdate,
    MatchmakerAdminAccountUpdate,
    MatchmakerAdminAuditLogPage,
    MatchmakerAdminLoginLogPage,
    MatchmakerAdminPasswordReset,
    MatchmakerAdminSessionPage,
)
from app.services import admin_account_cancellation as cancellation_service
from app.services.matchmaker_admin_account import (
    create_account,
    get_account,
    list_accounts,
    list_audit_logs,
    list_login_logs,
    list_sessions,
    reset_password,
    revoke_all_sessions,
    update_account,
    update_account_status,
)

router = APIRouter(prefix="/admin/matchmaker")


@router.get("/accounts", response_model=MatchmakerAdminAccountPage)
async def accounts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    username: str | None = Query(None, max_length=64),
    display_name: str | None = Query(None, max_length=128),
    status: int | None = Query(None, ge=1, le=3),
    matchmaker_user_id: int | None = Query(None, ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MatchmakerAdminAccountPage:
    current.require("matchmaker.account.manage")
    return await list_accounts(db, page, page_size, username, display_name, status, matchmaker_user_id)


@router.post("/accounts", response_model=MatchmakerAdminAccountItem, status_code=201)
async def create_admin_account(
    body: MatchmakerAdminAccountCreate,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MatchmakerAdminAccountItem:
    current.require("matchmaker.account.manage")
    return await create_account(db, body, current.account.id)


@router.get("/accounts/login-logs", response_model=MatchmakerAdminLoginLogPage)
async def login_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    account_id: int | None = Query(None, ge=1),
    username: str | None = Query(None, min_length=1, max_length=64),
    from_time: datetime | None = Query(None, alias="from"),
    to_time: datetime | None = Query(None, alias="to"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MatchmakerAdminLoginLogPage:
    current.require("matchmaker.account.manage")
    return await list_login_logs(db, page, page_size, account_id, username, from_time, to_time)


@router.get("/audit-logs", response_model=MatchmakerAdminAuditLogPage, summary="后台通用审计日志（系统日志页）")
async def audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    action_prefix: str | None = Query(None, max_length=64, description="action 前缀过滤，如 admin_account.password"),
    actor_account_id: int | None = Query(None, ge=1),
    from_time: datetime | None = Query(None, alias="from"),
    to_time: datetime | None = Query(None, alias="to"),
    keyword: str | None = Query(None, min_length=1, max_length=64),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MatchmakerAdminAuditLogPage:
    current.require("matchmaker.account.manage")
    return await list_audit_logs(db, page, page_size, action_prefix, actor_account_id, from_time, to_time, keyword)


@router.get("/accounts/{account_id}", response_model=MatchmakerAdminAccountItem)
async def account_detail(
    account_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
):
    current.require("matchmaker.account.manage")
    return await get_account(db, account_id)


@router.patch("/accounts/{account_id}", response_model=MatchmakerAdminAccountItem)
async def edit_account(
    account_id: int,
    body: MatchmakerAdminAccountUpdate,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MatchmakerAdminAccountItem:
    current.require("matchmaker.account.manage")
    return await update_account(db, account_id, body, current.account.id)


@router.patch("/accounts/{account_id}/status", response_model=MatchmakerAdminAccountItem)
async def change_account_status(
    account_id: int,
    body: MatchmakerAdminAccountStatusUpdate,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MatchmakerAdminAccountItem:
    current.require("matchmaker.account.manage")
    return await update_account_status(db, account_id, body, current.account.id)


@router.post("/accounts/{account_id}/reset-password", response_model=MatchmakerAdminAccountItem)
async def reset_account_password(
    account_id: int,
    body: MatchmakerAdminPasswordReset,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MatchmakerAdminAccountItem:
    current.require("matchmaker.account.manage")
    return await reset_password(db, account_id, body, current.account.id)


@router.get("/accounts/{account_id}/sessions", response_model=MatchmakerAdminSessionPage)
async def account_sessions(
    account_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MatchmakerAdminSessionPage:
    current.require("matchmaker.account.manage")
    return await list_sessions(db, account_id, page, page_size)


@router.post("/accounts/{account_id}/sessions/revoke-all", status_code=204)
async def revoke_account_sessions(
    account_id: int,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    current.require("matchmaker.account.manage")
    await revoke_all_sessions(db, account_id, current.account.id)


@router.delete("/accounts/{account_id}", response_model=AdminAccountCancellationItem, summary="删除账号（提交注销申请）")
async def delete_account(
    account_id: int,
    request: Request,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminAccountCancellationItem:
    """前端「删除账号」确认弹窗走 DELETE；这里不物理删除，而是生成待审核的注销申请。"""
    current.require("matchmaker.account.manage")
    requested_ip = request.client.host if request.client else None
    return await cancellation_service.submit_cancellation(
        db, account_id=account_id, actor_id=current.account.id, requested_ip=requested_ip
    )


@router.get("/account-cancellations", response_model=AdminAccountCancellationPage, summary="注销申请列表")
async def list_account_cancellations(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str = Query("all", pattern="^(all|pending|approved|cancelled)$"),
    keyword: str | None = Query(None, max_length=64),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminAccountCancellationPage:
    current.require("matchmaker.account.manage")
    return await cancellation_service.list_cancellations(
        db, page=page, page_size=page_size, status=status, keyword=keyword
    )


@router.post(
    "/account-cancellations/{cancellation_id}/review",
    response_model=AdminAccountCancellationItem,
    summary="处理注销申请（确定注销 / 取消注销）",
)
async def review_account_cancellation(
    cancellation_id: int,
    body: AdminAccountCancellationReview,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminAccountCancellationItem:
    current.require("matchmaker.account.manage")
    return await cancellation_service.review_cancellation(
        db,
        cancellation_id=cancellation_id,
        admin_id=current.account.id,
        approve=body.approve,
        note=body.note,
    )

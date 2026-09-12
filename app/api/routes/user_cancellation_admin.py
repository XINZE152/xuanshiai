"""账号注销申请（reg-user-cancel）后台路由。

前缀 ``/admin/user-cancellations``；读走 ``matchmaker.member.read``，
写（批准/取消注销）走 ``matchmaker.member.manage``。
"""

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    CurrentMatchmakerAdmin,
    get_current_matchmaker_admin,
)
from app.db.session import get_db
from app.schemas.user_cancellation_admin import (
    UserCancellationItem,
    UserCancellationPage,
    UserCancellationReview,
    UserCancellationStatistics,
)
from app.services import user_cancellation_admin as service


router = APIRouter(prefix="/admin/user-cancellations")


@router.get("", response_model=UserCancellationPage, summary="注销申请列表")
async def list_cancellations(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    status: str = Query("all", pattern="^(all|pending|approved|cancelled)$"),
    keyword: str | None = Query(None, max_length=64),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> UserCancellationPage:
    current.require("matchmaker.member.read")
    return await service.list_cancellations(
        db, page=page, page_size=page_size, status=status, keyword=keyword
    )


@router.get("/statistics", response_model=UserCancellationStatistics, summary="注销申请统计")
async def cancellation_statistics(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> UserCancellationStatistics:
    current.require("matchmaker.member.read")
    return await service.cancellation_statistics(db)


@router.post("/{cancellation_id}/review", response_model=UserCancellationItem, summary="处理注销申请（批准/取消注销）")
async def review_cancellation(
    cancellation_id: int = Path(..., ge=1),
    body: UserCancellationReview = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
):
    current.require("matchmaker.member.manage")
    item = await service.review_cancellation(
        db,
        cancellation_id=cancellation_id,
        admin_id=current.account.id,
        approve=body.approve,
        note=body.note,
    )
    return item

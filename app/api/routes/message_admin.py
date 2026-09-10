"""Message administration routes."""

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentMatchmakerAdmin, CurrentUser, get_current_admin, get_current_matchmaker_admin
from app.db.session import get_db
from app.schemas.message_admin import (
    AdminAnnouncementCreate,
    AdminAnnouncementItem,
    AdminMessageItem,
    AdminMessageModerationRequest,
    AdminMessagePage,
)
from app.services.message_admin import (
    create_admin_announcement,
    list_admin_messages,
    moderate_admin_message,
)

router = APIRouter(prefix="/admin/messages")


@router.get("", response_model=AdminMessagePage, summary="\u5206\u9875\u67e5\u8be2\u804a\u5929\u6d88\u606f")
async def messages(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user_id: int | None = Query(None, ge=1),
    session_id: int | None = Query(None, ge=1),
    message_type: int | None = Query(None, ge=1, le=6),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminMessagePage:
    return await list_admin_messages(db, current, page, page_size, user_id, session_id, message_type)


@router.patch("/{message_id}/moderation", response_model=AdminMessageItem, summary="\u5904\u7f6e\u804a\u5929\u6d88\u606f")
async def moderate(
    message_id: int = Path(..., ge=1),
    body: AdminMessageModerationRequest = ...,
    current: CurrentUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminMessageItem:
    return await moderate_admin_message(db, current, message_id, body.action, body.reason)


@router.post("/announcements", response_model=AdminAnnouncementItem, status_code=201, summary="\u521b\u5efa\u540e\u53f0\u516c\u544a")
async def announcement(
    body: AdminAnnouncementCreate,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminAnnouncementItem:
    return await create_admin_announcement(db, current, body)

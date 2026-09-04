"""Authenticated AI-avatar profile and conversation endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas.ai_avatar import (
    AiAvatarClearResponse,
    AiAvatarConversationResponse,
    AiAvatarMessageRequest,
    AiAvatarProfileResponse,
    AiAvatarSendResponse,
)
from app.services.ai_avatar import (
    clear_ai_conversation,
    get_ai_conversation,
    get_public_ai_context,
    send_ai_message,
)

router = APIRouter(prefix="/ai-avatars")


@router.get("/{target_user_id}/profile", response_model=AiAvatarProfileResponse, summary="读取 AI 分身公开资料")
async def profile(
    target_user_id: Annotated[int, Path(ge=1)],
    current: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AiAvatarProfileResponse:
    context = await get_public_ai_context(db, current.id, current.realname_status, target_user_id)
    return context.profile


@router.get("/{target_user_id}/conversations", response_model=AiAvatarConversationResponse, summary="读取 AI 分身聊天记录")
async def conversation(
    target_user_id: Annotated[int, Path(ge=1)],
    current: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AiAvatarConversationResponse:
    return await get_ai_conversation(db, current.id, current.realname_status, target_user_id)


@router.post("/{target_user_id}/messages", response_model=AiAvatarSendResponse, summary="向 AI 分身发送问题")
async def send_message(
    body: AiAvatarMessageRequest,
    target_user_id: Annotated[int, Path(ge=1)],
    current: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AiAvatarSendResponse:
    return await send_ai_message(
        db, current.id, current.realname_status, target_user_id, body.content
    )


@router.delete("/{target_user_id}/conversations", response_model=AiAvatarClearResponse, summary="清空 AI 分身聊天记录")
async def clear_conversation(
    target_user_id: Annotated[int, Path(ge=1)],
    current: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AiAvatarClearResponse:
    return await clear_ai_conversation(db, current.id, current.realname_status, target_user_id)

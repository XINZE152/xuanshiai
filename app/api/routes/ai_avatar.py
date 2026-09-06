"""Authenticated AI-avatar profile and conversation endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas.ai_avatar import (
    AiAvatarClearResponse,
    AiAvatarConversationResponse,
    AiAvatarMessageRequest,
    AiAvatarOwnerAnswerCreateRequest,
    AiAvatarOwnerAnswerRequest,
    AiAvatarOwnerDashboardResponse,
    AiAvatarProfileResponse,
    AiAvatarSendResponse,
)
from app.services.ai_avatar import (
    add_owner_answer,
    answer_owner_question,
    clear_ai_conversation,
    delete_owner_answer,
    delete_owner_question,
    get_ai_conversation,
    get_owner_dashboard,
    get_public_ai_context,
    send_ai_message,
)

router = APIRouter(prefix="/ai-avatars")


@router.get("/me/dashboard", response_model=AiAvatarOwnerDashboardResponse, summary="读取我的 AI 分身问答")
async def owner_dashboard(
    current: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AiAvatarOwnerDashboardResponse:
    return await get_owner_dashboard(db, current.id)


@router.post("/me/questions", response_model=AiAvatarOwnerDashboardResponse, summary="新增我的 AI 分身问答")
async def create_owner_answer(
    body: AiAvatarOwnerAnswerCreateRequest,
    current: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AiAvatarOwnerDashboardResponse:
    return await add_owner_answer(db, current.id, body)


@router.post("/me/questions/{question_id}/answer", response_model=AiAvatarOwnerDashboardResponse, summary="回答 AI 分身待回答问题")
async def answer_owner_question_route(
    body: AiAvatarOwnerAnswerRequest,
    question_id: Annotated[int, Path(ge=1)],
    current: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AiAvatarOwnerDashboardResponse:
    return await answer_owner_question(db, current.id, question_id, body)


@router.delete("/me/questions/{question_id}", response_model=AiAvatarOwnerDashboardResponse, summary="删除 AI 分身待回答问题")
async def delete_owner_question_route(
    question_id: Annotated[int, Path(ge=1)],
    current: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AiAvatarOwnerDashboardResponse:
    return await delete_owner_question(db, current.id, question_id)


@router.delete("/me/answers/{answer_id}", response_model=AiAvatarOwnerDashboardResponse, summary="删除我的 AI 分身回答")
async def delete_owner_answer_route(
    answer_id: Annotated[int, Path(ge=1)],
    current: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AiAvatarOwnerDashboardResponse:
    return await delete_owner_answer(db, current.id, answer_id)


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
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> AiAvatarSendResponse:
    return await send_ai_message(
        db,
        current.id,
        current.realname_status,
        target_user_id,
        body.content,
        idempotency_key=idempotency_key,
    )


@router.delete("/{target_user_id}/conversations", response_model=AiAvatarClearResponse, summary="清空 AI 分身聊天记录")
async def clear_conversation(
    target_user_id: Annotated[int, Path(ge=1)],
    current: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AiAvatarClearResponse:
    return await clear_ai_conversation(db, current.id, current.realname_status, target_user_id)

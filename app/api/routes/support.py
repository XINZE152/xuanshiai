"""C 端反馈 / 意见箱与帮助中心接口（Q54，2026-11-02）。

- ``POST /support/feedback``：意见箱，幂等 + 每日 10 条上限；
- ``GET /support/feedback/mine``：仅本人反馈；
- ``GET /support/help-docs``：读取后台维护的 ``help_doc`` 内容；
- ``POST /support/contact``：转人工客服，落 ``user_feedback``（category=咨询）；
- ``POST /support/ai-chat``：AI 客服（简单版：知识库检索 + 无关内容拒答）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas.support import (
    AiChatReply,
    AiChatRequest,
    ContactRequest,
    FeedbackCreate,
    FeedbackItem,
    FeedbackPage,
    HelpDocPage,
)
from app.services import support as service
from app.services import support_ai
from app.services.sensitive_operations import (
    abort_sensitive_operation,
    begin_sensitive_operation,
    finish_sensitive_operation,
)

router = APIRouter(prefix="/support")

_IDEMPOTENCY_HEADER = Header(
    ...,
    alias="Idempotency-Key",
    min_length=8,
    max_length=128,
    description="客户端生成的幂等键，同键同载荷只会创建一条",
)


@router.post("/feedback", response_model=FeedbackItem, status_code=201, summary="提交反馈")
async def create_feedback(
    body: FeedbackCreate = ...,
    idempotency_key: str = _IDEMPOTENCY_HEADER,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FeedbackItem:
    reservation, replayed = await begin_sensitive_operation(
        db,
        actor_id=current.id,
        operation="support.feedback.create",
        idempotency_key=idempotency_key,
        payload=body.model_dump(mode="json"),
    )
    if replayed is not None:
        return FeedbackItem.model_validate(replayed)
    try:
        item = await service.create_feedback(
            db,
            current.id,
            category=body.category,
            content=body.content,
            images=body.images,
            contact_phone=body.contact_phone,
        )
    except Exception:
        await abort_sensitive_operation(db, reservation)
        raise
    await finish_sensitive_operation(db, reservation, item.model_dump(mode="json"))
    return item


@router.get("/feedback/mine", response_model=FeedbackPage, summary="我的反馈列表")
async def my_feedback(
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(20, ge=1, le=100),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FeedbackPage:
    return await service.list_my_feedback(db, current.id, page, page_size)


@router.get("/help-docs", response_model=HelpDocPage, summary="帮助中心文档")
async def help_docs(
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = Query(None, max_length=64),
    db: AsyncSession = Depends(get_db),
) -> HelpDocPage:
    return await service.list_help_docs(db, page, page_size, keyword)


@router.post("/ai-chat", response_model=AiChatReply, summary="AI 客服（简单版）")
async def ai_chat(
    body: AiChatRequest,
    _current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AiChatReply:
    """知识库检索式问答，不调大模型。

    刻意不做限流与落库：这是一次纯计算（读帮助文档 + 字符串匹配），没有外部调用、
    没有写入、没有成本，加限流只会多一个让用户卡住的分支。真要防刷，交给网关。
    """
    docs = await service.list_help_docs(db, 1, support_ai.DOC_LIMIT)
    return support_ai.answer_support_question(body.content, docs.items)


@router.post("/contact", response_model=FeedbackItem, status_code=201, summary="联系人工客服")
async def contact_support(
    body: ContactRequest = ...,
    idempotency_key: str = _IDEMPOTENCY_HEADER,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FeedbackItem:
    reservation, replayed = await begin_sensitive_operation(
        db,
        actor_id=current.id,
        operation="support.contact.create",
        idempotency_key=idempotency_key,
        payload=body.model_dump(mode="json"),
    )
    if replayed is not None:
        return FeedbackItem.model_validate(replayed)
    try:
        item = await service.create_feedback(
            db,
            current.id,
            category="咨询",
            content=body.content,
            images=body.images,
            contact_phone=body.contact_phone,
        )
    except Exception:
        await abort_sensitive_operation(db, reservation)
        raise
    await finish_sensitive_operation(db, reservation, item.model_dump(mode="json"))
    return item

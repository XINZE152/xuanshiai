"""资料卡开放文本草稿路由：summarize / draft / apply。"""

from __future__ import annotations

import re
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, get_current_user
from app.core.config import settings
from app.core.logging import request_id_context
from app.db.session import get_db
from app.schemas.ai_common import AiErrorResponse
from app.schemas.ai_profile_card import (
    ProfileCardApplyRequest,
    ProfileCardApplyResponse,
    ProfileCardDraftRead,
    ProfileCardSummarizeAccepted,
    ProfileCardSummarizeRequest,
)
from app.services.ai.flags import AiFeature, AiFeatureDisabledError, require_ai_feature
from app.services.ai.profile import AIConsentRequired, AIInputError
from app.services.ai.profile_card import (
    ProfileCardDraftNotFound,
    ProfileCardQuotaExceeded,
    ProfileCardVersionConflict,
    apply_profile_card_draft,
    load_profile_card_draft,
    request_profile_card_summarize,
)
from app.services.ai.tasks import TaskError

router = APIRouter()
_IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


def _request_id() -> str:
    supplied = request_id_context.get()
    if supplied and supplied != "-":
        return supplied
    return uuid4().hex


def _error_response(
    code: str, message: str, status_code: int, *, retryable: bool = False
) -> HTTPException:
    detail = AiErrorResponse(
        code=code,
        message=message,
        request_id=_request_id(),
        retryable=retryable,
        retry_after_ms=0,
    )
    return HTTPException(status_code=status_code, detail=detail.model_dump())


def _require_profile_feature() -> None:
    try:
        require_ai_feature(AiFeature.PROFILE, settings)
    except AiFeatureDisabledError as exc:
        raise _error_response(
            exc.code,
            "AI 画像功能当前不可用",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc


def _check_idempotency_key(idempotency_key: str | None) -> None:
    if not idempotency_key or not _IDEMPOTENCY_KEY_PATTERN.fullmatch(idempotency_key):
        raise _error_response(
            "AI_INPUT_INVALID",
            "Idempotency-Key 必须为 8-128 位 ASCII 字符",
            status.HTTP_400_BAD_REQUEST,
        )


@router.post(
    "/profile-card/summarize",
    response_model=ProfileCardSummarizeAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="根据已确认成稿异步生成资料卡开放文本草稿",
    openapi_extra={
        "parameters": [
            {
                "name": "Idempotency-Key",
                "in": "header",
                "required": True,
                "schema": {"type": "string"},
            }
        ]
    },
)
async def summarize_profile_card_route(
    body: ProfileCardSummarizeRequest | None = None,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ProfileCardSummarizeAccepted:
    _require_profile_feature()
    _check_idempotency_key(idempotency_key)
    request = body or ProfileCardSummarizeRequest()
    try:
        submission = await request_profile_card_summarize(
            db,
            current.id,
            force=request.force,
            idempotency_key=idempotency_key or "",
        )
    except AIConsentRequired as exc:
        raise _error_response(exc.code, exc.message, exc.status_code) from exc
    except AIInputError as exc:
        raise _error_response(exc.code, exc.message, exc.status_code) from exc
    except ProfileCardQuotaExceeded as exc:
        raise _error_response(
            exc.code, exc.message, exc.status_code, retryable=exc.retryable
        ) from exc
    except TaskError as exc:
        raise _error_response(
            exc.code, exc.message, exc.status_code, retryable=exc.retryable
        ) from exc
    await db.commit()
    return ProfileCardSummarizeAccepted(
        task_id=submission.task.task_id,
        status=submission.task.status,
        poll_url=f"/api/v1/ai/tasks/{submission.task.task_id}",
        replayed=submission.replayed,
    )


@router.get(
    "/profile-card/draft",
    response_model=ProfileCardDraftRead,
    status_code=status.HTTP_200_OK,
    summary="读取本人最新未丢弃的资料卡草稿",
)
async def get_profile_card_draft_route(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProfileCardDraftRead:
    _require_profile_feature()
    try:
        return await load_profile_card_draft(db, current.id)
    except AIConsentRequired as exc:
        raise _error_response(exc.code, exc.message, exc.status_code) from exc
    except ProfileCardDraftNotFound as exc:
        raise _error_response(exc.code, exc.message, exc.status_code) from exc


@router.post(
    "/profile-card/draft/apply",
    response_model=ProfileCardApplyResponse,
    status_code=status.HTTP_200_OK,
    summary="将用户显式采用的资料卡草稿写入资料",
    openapi_extra={
        "parameters": [
            {
                "name": "Idempotency-Key",
                "in": "header",
                "required": True,
                "schema": {"type": "string"},
            }
        ]
    },
)
async def apply_profile_card_draft_route(
    body: ProfileCardApplyRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ProfileCardApplyResponse:
    _require_profile_feature()
    _check_idempotency_key(idempotency_key)
    try:
        result = await apply_profile_card_draft(
            db, current.id, body, idempotency_key or ""
        )
    except AIConsentRequired as exc:
        raise _error_response(exc.code, exc.message, exc.status_code) from exc
    except AIInputError as exc:
        raise _error_response(exc.code, exc.message, exc.status_code) from exc
    except ProfileCardDraftNotFound as exc:
        raise _error_response(exc.code, exc.message, exc.status_code) from exc
    except ProfileCardVersionConflict as exc:
        raise _error_response(exc.code, exc.message, exc.status_code) from exc
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - 统一包装未知错误
        raise _error_response(
            "AI_TEMPORARILY_UNAVAILABLE",
            "资料卡草稿写入暂时不可用",
            status.HTTP_503_SERVICE_UNAVAILABLE,
            retryable=True,
        ) from exc
    await db.commit()
    return ProfileCardApplyResponse.model_validate(result)

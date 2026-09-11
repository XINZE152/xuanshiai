"""AI 记忆内核 API（Memory Kernel Core v1 + Phase 3 Memory View）。

端点（挂载于 ``/ai`` 前缀）：

- ``GET  /ai/memory``——当前用户的记忆条目列表（Claim 视图，按主体分页）；
- ``GET  /ai/memory/view``——记忆管理页视图（跨主体条目 + updated_at +
  投影授权概览，Phase 3）；
- ``POST /ai/memory/{claim_id}/confirm``——确认（expected_revision 乐观锁）；
- ``POST /ai/memory/{claim_id}/correct``——纠正（保留因果链）；
- ``POST /ai/memory/{claim_id}/suppress``——删除墓碑；
- ``POST /ai/memory/suppressions/{suppression_id}/lift``——解除墓碑；
- ``POST /ai/memory/grants/{grant_id}/revoke``——撤销单个投影授权并立即
  失效对应投影（owner-scoped 404，Phase 3）。

复用既有鉴权（``get_current_user``）、AI 错误 envelope、HMAC 签名 cursor
（与 AI 搜索 cursor 同构，密钥 ``settings.secret_key``）与 Idempotency-Key
头。响应只包含最小字段：content/value/status/confidence/stability/
source_quote/source_ref——完整 transcript 不出现在任何响应中。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.schemas.ai_common import AiErrorResponse
from app.schemas.ai_memory import MemoryIdempotencyConflict
from app.services.ai.memory.policy import MemoryPolicyDenied
from app.services.ai.memory.service import (
    MemoryClaimNotFound,
    MemoryClaimStateDenied,
    MemoryRevisionConflict,
    MemoryService,
    normalize_idempotency_key,
)
from app.services.revisions import RevisionKind, increment_revision_and_enqueue

_IDEMPOTENCY_CONFLICT = ("MEMORY_IDEMPOTENCY_CONFLICT", "Idempotency-Key conflict", 409)

router = APIRouter()

_MEMORY_CURSOR_VERSION = 2
_MEMORY_CURSOR_TTL_SECONDS = 900
# claim 状态全集 + "active" 别名（proposed+confirmed 的有效记忆）。
_CLAIM_STATUSES = frozenset({"proposed", "confirmed", "superseded", "contradicted", "user_corrected", "expired"})
_ACTIVE_ALIAS = frozenset({"proposed", "confirmed"})

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 错误 envelope（与 ai_moxiang 同构）
# ---------------------------------------------------------------------------


def _request_id() -> str:
    import uuid

    return uuid.uuid4().hex


def _error_response(code: str, message: str, status_code: int, *, retryable: bool = False) -> HTTPException:
    detail = AiErrorResponse(
        code=code,
        message=message,
        request_id=_request_id(),
        retryable=retryable,
        retry_after_ms=0,
    )
    return HTTPException(status_code=status_code, detail=detail.model_dump())


# ---------------------------------------------------------------------------
# HMAC 签名 cursor（服务端签发；示例值/伪造值不可复用）
# ---------------------------------------------------------------------------


class InvalidMemoryCursor(ValueError):
    """Raised when a caller presents a cursor this server never signed."""


def _encode_memory_cursor(
    after_seq: int,
    *,
    owner_user_id: int,
    subject: str | None,
    status: str | None,
) -> str:
    payload = json.dumps(
        {
            "version": _MEMORY_CURSOR_VERSION,
            "after_seq": int(after_seq),
            "owner_user_id": int(owner_user_id),
            "subject": subject,
            "status": status,
            "issued_at": int(time.time()),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    signature = hmac.new(
        settings.secret_key.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{encoded}.{base64.urlsafe_b64encode(signature).decode('ascii').rstrip('=')}"


def _decode_memory_cursor(
    token: str,
    *,
    owner_user_id: int,
    subject: str | None,
    status: str | None,
) -> int:
    if not token or len(token) > 512 or "." not in token:
        raise InvalidMemoryCursor("invalid memory cursor")
    encoded, signature = token.split(".", 1)
    try:
        expected = hmac.new(
            settings.secret_key.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256
        ).digest()
        padding = "=" * (-len(signature) % 4)
        actual = base64.urlsafe_b64decode((signature + padding).encode("ascii"))
        if not hmac.compare_digest(expected, actual):
            raise InvalidMemoryCursor("invalid memory cursor")
        padding = "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode((encoded + padding).encode("ascii")))
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise InvalidMemoryCursor("invalid memory cursor") from exc
    if payload.get("version") != _MEMORY_CURSOR_VERSION:
        raise InvalidMemoryCursor("invalid memory cursor version")
    after_seq = payload.get("after_seq")
    if not isinstance(after_seq, int) or after_seq < 0:
        raise InvalidMemoryCursor("invalid memory cursor payload")
    issued_at = payload.get("issued_at")
    now = int(time.time())
    if (
        not isinstance(issued_at, int)
        or issued_at > now + 60
        or issued_at < now - _MEMORY_CURSOR_TTL_SECONDS
    ):
        raise InvalidMemoryCursor("expired memory cursor")
    if (
        payload.get("owner_user_id") != int(owner_user_id)
        or payload.get("subject") != subject
        or payload.get("status") != status
    ):
        raise InvalidMemoryCursor("memory cursor does not match request")
    return after_seq


# ---------------------------------------------------------------------------
# 请求 / 响应 schema
# ---------------------------------------------------------------------------


class MemoryConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(..., ge=1)
    importance: float = Field(..., ge=0.0, le=1.0)
    constraint_type: str | None = Field(default=None, max_length=32)


class MemoryCorrectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(..., ge=1)
    value: Any
    importance: float | None = Field(default=None, ge=0.0, le=1.0)
    constraint_type: str | None = Field(default=None, max_length=32)
    source_quote: str | None = Field(default=None, max_length=512)


class MemorySuppressRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=200)


class MemoryItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    subject: str
    node_type: str = "claim"
    content: str | None = None
    value: Any = None
    status: str
    confidence: float | None = None
    stability: float | None = None
    importance: float | None = None
    source_quote: str | None = None
    source_ref: str | None = None
    canonical_key: str
    revision: int = Field(..., ge=1)


class MemoryListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: tuple[MemoryItemResponse, ...] = ()
    next_cursor: str | None = None
    has_more: bool = False


class MemoryViewItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    subject: str
    node_type: str = "claim"
    content: str | None = None
    value: Any = None
    status: str
    confidence: float | None = None
    stability: float | None = None
    importance: float | None = None
    source_quote: str | None = None
    source_ref: str | None = None
    canonical_key: str
    updated_at: str | None = None
    revision: int = Field(..., ge=1)


class MemoryGrantSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grant_id: str
    function_key: str
    purpose: str
    data_category: str
    status: str
    policy_revision: str
    granted_at: str | None = None
    revoked_at: str | None = None


class MemoryViewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: tuple[MemoryViewItemResponse, ...] = ()
    grants: tuple[MemoryGrantSummary, ...] = ()
    next_cursor: str | None = None
    has_more: bool = False


class MemoryGrantRevokeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grant_id: str
    status: str
    function_key: str
    purpose: str
    data_category: str
    invalidated_projections: int = 0


def _require_idempotency_key(idempotency_key: str | None) -> str:
    try:
        return normalize_idempotency_key(idempotency_key)
    except ValueError:
        raise _error_response(
            "AI_INPUT_INVALID", "Idempotency-Key 必须为 1-128 个字符且不得包含空白或控制字符", 422
        )


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------


@router.get("/memory", response_model=MemoryListResponse)
async def list_memory(
    current: CurrentUser = Depends(get_current_user),
    subject: Literal["personal", "ideal_partner"] = Query(...),
    status: str | None = Query(default=None, max_length=24),
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
) -> MemoryListResponse:
    """列出当前用户的长期记忆条目（Claim 视图，owner 隔离）。"""

    after_seq = 0
    if cursor is not None:
        try:
            after_seq = _decode_memory_cursor(
                cursor,
                owner_user_id=current.id,
                subject=subject,
                status=status,
            )
        except InvalidMemoryCursor:
            raise _error_response("AI_INPUT_INVALID", "无效的 cursor", 400)
    if status is not None and status not in _CLAIM_STATUSES and status != "active":
        raise _error_response("AI_INPUT_INVALID", f"未知状态: {status}", 422)
    statuses = _ACTIVE_ALIAS if status == "active" else (frozenset({status}) if status else _CLAIM_STATUSES)
    service = MemoryService(db)
    fetched_items, _ = await service.list_memory_items(
        current.id, subject=subject, statuses=statuses, after_seq=after_seq, limit=limit + 1
    )
    has_more = len(fetched_items) > limit
    items = fetched_items[:limit]
    next_cursor = (
        _encode_memory_cursor(
            int(items[-1]["revision"]),
            owner_user_id=current.id,
            subject=subject,
            status=status,
        )
        if has_more and items
        else None
    )
    return MemoryListResponse(
        items=tuple(MemoryItemResponse(**item) for item in items),
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.post("/memory/{claim_id}/confirm")
async def confirm_memory_claim(
    claim_id: str = Path(..., min_length=1, max_length=64),
    body: MemoryConfirmRequest = ...,
    current: CurrentUser = Depends(get_current_user),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    key = _require_idempotency_key(idempotency_key)
    service = MemoryService(db)
    try:
        record = await service.confirm_claim(
            owner_user_id=current.id,
            claim_id=claim_id,
            expected_revision=body.expected_revision,
            importance=body.importance,
            constraint_type=body.constraint_type,
            idempotency_key=key,
        )
    except MemoryClaimNotFound as exc:
        raise _error_response("MEMORY_CLAIM_NOT_FOUND", str(exc), 404)
    except MemoryRevisionConflict as exc:
        raise _error_response("MEMORY_REVISION_CONFLICT", str(exc), 409)
    except MemoryClaimStateDenied as exc:
        raise _error_response("MEMORY_CLAIM_STATE_DENIED", str(exc), 409)
    except MemoryIdempotencyConflict:
        raise _error_response(*_IDEMPOTENCY_CONFLICT)
    except MemoryPolicyDenied as exc:
        raise _error_response("AI_POLICY_DENIED", str(exc), 403)
    # get_db 只负责关闭会话（未提交即回滚），写端点必须显式提交。
    await db.commit()
    return {
        "claim_id": claim_id,
        "event_id": record.event_id,
        "status": "confirmed",
        "revision": record.server_seq,
        "importance_confirmed": True,
    }


@router.post("/memory/{claim_id}/correct")
async def correct_memory_claim(
    claim_id: str = Path(..., min_length=1, max_length=64),
    body: MemoryCorrectRequest = ...,
    current: CurrentUser = Depends(get_current_user),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    key = _require_idempotency_key(idempotency_key)
    service = MemoryService(db)
    try:
        record = await service.correct_claim(
            owner_user_id=current.id,
            claim_id=claim_id,
            expected_revision=body.expected_revision,
            value=body.value,
            importance=body.importance,
            constraint_type=body.constraint_type,
            source_quote=body.source_quote,
            idempotency_key=key,
        )
    except MemoryClaimNotFound as exc:
        raise _error_response("MEMORY_CLAIM_NOT_FOUND", str(exc), 404)
    except MemoryRevisionConflict as exc:
        raise _error_response("MEMORY_REVISION_CONFLICT", str(exc), 409)
    except MemoryClaimStateDenied as exc:
        raise _error_response("MEMORY_CLAIM_STATE_DENIED", str(exc), 409)
    except MemoryIdempotencyConflict:
        raise _error_response(*_IDEMPOTENCY_CONFLICT)
    except MemoryPolicyDenied as exc:
        raise _error_response("AI_POLICY_DENIED", str(exc), 403)
    await db.commit()
    return {
        "claim_id": claim_id,
        "event_id": record.event_id,
        "status": "user_corrected",
        "revision": record.server_seq,
    }


@router.post("/memory/{claim_id}/suppress")
async def suppress_memory_claim(
    claim_id: str = Path(..., min_length=1, max_length=64),
    body: MemorySuppressRequest = ...,
    current: CurrentUser = Depends(get_current_user),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    key = _require_idempotency_key(idempotency_key)
    service = MemoryService(db)
    try:
        record = await service.suppress_claim(
            owner_user_id=current.id,
            claim_id=claim_id,
            reason=body.reason,
            idempotency_key=key,
        )
    except MemoryClaimNotFound as exc:
        raise _error_response("MEMORY_CLAIM_NOT_FOUND", str(exc), 404)
    except MemoryIdempotencyConflict:
        raise _error_response(*_IDEMPOTENCY_CONFLICT)
    except MemoryPolicyDenied as exc:
        raise _error_response("AI_POLICY_DENIED", str(exc), 403)
    await db.commit()
    return {
        "suppression_id": f"sup_{record.event_id}",
        "event_id": record.event_id,
        "status": "suppressed",
    }


@router.post("/memory/suppressions/{suppression_id}/lift")
async def lift_memory_suppression(
    suppression_id: str = Path(..., min_length=1, max_length=64),
    current: CurrentUser = Depends(get_current_user),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    key = _require_idempotency_key(idempotency_key)
    service = MemoryService(db)
    try:
        record = await service.lift_suppression_by_id(
            owner_user_id=current.id,
            suppression_id=suppression_id,
            idempotency_key=key,
        )
    except MemoryClaimNotFound as exc:
        raise _error_response("MEMORY_SUPPRESSION_NOT_FOUND", str(exc), 404)
    except MemoryIdempotencyConflict:
        raise _error_response(*_IDEMPOTENCY_CONFLICT)
    except MemoryPolicyDenied as exc:
        raise _error_response("AI_POLICY_DENIED", str(exc), 403)
    await db.commit()
    return {
        "suppression_id": suppression_id,
        "event_id": record.event_id,
        "status": "lifted",
    }


@router.get("/memory/view", response_model=MemoryViewResponse)
async def memory_view(
    current: CurrentUser = Depends(get_current_user),
    subject: Literal["personal", "ideal_partner"] | None = Query(default=None),
    status: str | None = Query(default=None, max_length=24),
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
) -> MemoryViewResponse:
    """记忆管理页视图：跨主体条目（含 updated_at）+ 投影授权概览（owner 隔离）。"""

    after_seq = 0
    if cursor is not None:
        try:
            after_seq = _decode_memory_cursor(
                cursor,
                owner_user_id=current.id,
                subject=subject,
                status=status,
            )
        except InvalidMemoryCursor:
            raise _error_response("AI_INPUT_INVALID", "无效的 cursor", 400)
    if status is not None and status not in _CLAIM_STATUSES and status != "active":
        raise _error_response("AI_INPUT_INVALID", f"未知状态: {status}", 422)
    statuses = _ACTIVE_ALIAS if status == "active" else (
        frozenset({status}) if status else _CLAIM_STATUSES
    )
    service = MemoryService(db)
    fetched_items, _ = await service.list_memory_view_items(
        current.id, statuses=statuses, after_seq=after_seq, limit=limit + 1, subject=subject
    )
    has_more = len(fetched_items) > limit
    items = fetched_items[:limit]
    next_cursor = (
        _encode_memory_cursor(
            int(items[-1]["revision"]),
            owner_user_id=current.id,
            subject=subject,
            status=status,
        )
        if has_more and items
        else None
    )
    grants = await service.list_projection_grants(current.id)
    return MemoryViewResponse(
        items=tuple(MemoryViewItemResponse(**item) for item in items),
        grants=tuple(MemoryGrantSummary(**grant) for grant in grants),
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.post("/memory/grants/{grant_id}/revoke", response_model=MemoryGrantRevokeResponse)
async def revoke_memory_grant(
    grant_id: str = Path(..., min_length=1, max_length=96),
    current: CurrentUser = Depends(get_current_user),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
) -> MemoryGrantRevokeResponse:
    """撤销当前用户的一个投影授权；对应 active 投影立即失效（owner-scoped 404）。

    Idempotency-Key 必填（与其余写端点一致）；撤销本身天然幂等——重复请求
    返回 already_revoked，不重复失效。
    """

    key = _require_idempotency_key(idempotency_key)
    service = MemoryService(db)
    try:
        result = await service.revoke_projection_grant(
            current.id, grant_id, idempotency_key=key
        )
    except MemoryClaimNotFound as exc:
        await db.rollback()
        raise _error_response("MEMORY_GRANT_NOT_FOUND", str(exc), 404)
    except MemoryPolicyDenied as exc:
        await db.rollback()
        raise _error_response("AI_POLICY_DENIED", str(exc), 403)
    # 撤权递增 privacy revision：向下游传播隐私变更，同时使挂起的授权生产者
    # 重试事件（旧 revision 向量）按 superseded 收口，不复活已撤销授权。
    # already_revoked 回放不递增（revision 不因重复请求空转）。
    if str(result["status"]) == "revoked":
        await increment_revision_and_enqueue(
            db,
            current.id,
            RevisionKind.PRIVACY,
            ("ai_memory_grant_revoked",),
            "privacy_updated",
            priority=10,
        )
    await db.commit()
    return MemoryGrantRevokeResponse(
        grant_id=grant_id,
        status=str(result["status"]),
        function_key=str(result["function_key"]),
        purpose=str(result["purpose"]),
        data_category=str(result["data_category"]),
        invalidated_projections=int(result.get("invalidated_projections") or 0),
    )

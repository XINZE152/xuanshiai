"""AI 画像（墨相师）路由 —— 画像发布链（统一方案 §7.5/§7.6）。

> **命名说明：墨相师（Moxiang）就是 AI 画像功能的产品更名。** 旧的对话式画像
> REST 问答入口（``/profile-sessions`` 系列，M04 文字问答，含文字/语音双模式）
> 已于 2026-09-11 删除，画像建构统一通过墨相师旅程完成（WS
> ``/voice/moxiang-master`` 与 ``/ai/moxiang/*`` REST，契约见
> ``docs/api/墨相师实时整理WebSocket.md``）。本模块保留的是画像发布链：
> 墨相师旅程产出草稿后，确认、发布、版本历史、叙事层与删除传播仍走以下接口。

前缀 `/api/v1/ai`（由 ``app/api/router.py`` 注册），共 13 个路径：

- ``GET /profile-drafts/{draft_id}``：200 字段草稿（仅本人）
- ``PATCH /profile-drafts/{draft_id}``：200 新草稿 revision（乐观锁）
- ``POST /profile-drafts/{draft_id}/publish``：202 publish task（confirmed-only）
- ``POST /profile-drafts/{draft_id}/preview``：202 生成/复用成稿预览
- ``GET /profile-previews/{preview_id}``：200 读取预览
- ``GET /profile-revisions``：200 游标历史（仅本人，只读）
- ``POST /profile-revisions/{revision_id}/restore``：201 新 draft（旧行只读）
- ``GET /profiles/{subject}/fields``：200 最新发布画像字段
- ``DELETE /profiles/{subject}``：202 cleanup task（同步隐藏 + 异步清理）
- ``DELETE /profiles/{subject}/fields/{field_key}``：202 invalidation task
- ``GET /profiles/{subject}/narrative``：200 最新叙事层（状态透传，WP-P3）
- ``POST /profiles/{subject}/narrative/confirm``：200 确认叙事层（待确认 → 已确认）
- ``POST /profiles/{subject}/narrative/regenerate``：202 重新生成叙事层（每日限 5 次）

所有写操作要求 ``Idempotency-Key`` header；错误统一为 ``AiErrorDetail`` 形状并
携带 request_id。普通响应不携带原文、provider trace 或密钥。
"""

from __future__ import annotations

import re
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, get_current_user
from app.core.config import settings
from app.core.logging import request_id_context
from app.db.session import get_db
from app.schemas.ai_common import AiErrorResponse
from app.schemas.ai_profile import (
    CleanupTaskAccepted,
    ProfileDraftPatchRequest,
    ProfileDraftRead,
    ProfileNarrativeRead,
    ProfilePublishAccepted,
    ProfileRevisionPage,
    ProfilePublishedFieldsPage,
    ProfileSubject,
)
from app.services.ai.flags import AiFeature, AiFeatureDisabledError, require_ai_feature
from app.services.ai.providers import sanitize_narrative_dimension_icon
from app.services.ai.profile import (
    AIConsentRequired,
    AIInputError,
    DraftStatusConflict,
    DraftVersionConflict,
    ProfileDraft,
    ProfileDraftNotFound,
    ProfileRevisionNotFound,
    confirm_profile_draft,
    confirm_profile_narrative,
    list_published_profile_fields,
    delete_ai_profile,
    delete_ai_profile_field,
    list_profile_revisions,
    load_owned_draft,
    load_published_narrative,
    publish_profile_draft,
    request_narrative_regenerate,
    restore_profile_revision,
)
from app.services.ai.tasks import TaskError

router = APIRouter()

# Idempotency-Key 契约（§7.5）：8-128 位 ASCII，禁止空白。
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


def _to_draft_read(draft: ProfileDraft) -> ProfileDraftRead:
    from app.schemas.ai_profile import (
        ProfileDraftFieldRead,
        ProfileFieldConfirmationStatus,
    )

    return ProfileDraftRead(
        draft_id=draft.draft_id,
        subject=ProfileSubject(draft.subject),
        status=draft.status,
        expected_revision=draft.revision,
        policy_revision=draft.policy_revision,
        schema_version=draft.schema_version,
        fields=[
            ProfileDraftFieldRead(
                field_key=field.field_key,
                subject=ProfileSubject(field.subject),
                value=field.value,
                display_value=field.display_value,
                source_quote=field.source_span,
                confidence=field.confidence,
                needs_confirmation=(
                    field.confirmation_status
                    != ProfileFieldConfirmationStatus.CONFIRMED.value
                ),
                confirmation_status=ProfileFieldConfirmationStatus(
                    field.confirmation_status
                ),
                content_hash=field.content_hash,
                # WP-P1 加法透传：structured 行恒为默认值，entry 行带
                # category/content，旧前端零感知。
                field_kind=field.field_kind,
                category=field.category,
                content=field.content,
                replaces_field_key=field.replaces_field_key,
            )
            for field in draft.fields
        ],
        synced_profile_fields=list(getattr(draft, "synced_profile_fields", ()) or ()),
        expires_at=draft.expires_at,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
    )


# ----------------------------------------------------------------------
# Task 8：草稿确认、发布、历史与删除传播
# ----------------------------------------------------------------------


@router.get(
    "/profile-drafts/{draft_id}",
    response_model=ProfileDraftRead,
    status_code=status.HTTP_200_OK,
    summary="查询本人的 AI 画像字段草稿",
)
async def get_profile_draft_route(
    draft_id: str = Path(..., min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$"),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProfileDraftRead:
    """Return the draft for the owner only; missing/foreign is a uniform 404."""
    _require_profile_feature()
    try:
        draft = await load_owned_draft(db, draft_id, current.id)
    except ProfileDraftNotFound as exc:
        raise _error_response(exc.code, exc.message, exc.status_code) from exc
    return _to_draft_read(draft)


@router.patch(
    "/profile-drafts/{draft_id}",
    response_model=ProfileDraftRead,
    status_code=status.HTTP_200_OK,
    summary="逐项确认/修改/拒绝/删除草稿字段",
)
async def patch_profile_draft_route(
    draft_id: str = Path(..., min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$"),
    body: ProfileDraftPatchRequest = Body(...),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ProfileDraftRead:
    """Apply per-field confirm/replace/reject/delete under the optimistic lock."""
    _require_profile_feature()
    _check_idempotency_key(idempotency_key)
    try:
        draft = await confirm_profile_draft(
            db,
            draft_id,
            current.id,
            body.actions,
            body.expected_revision,
            idempotency_key,
        )
    except DraftVersionConflict as exc:
        raise _error_response(exc.code, exc.message, exc.status_code) from exc
    except DraftStatusConflict as exc:
        raise _error_response(exc.code, exc.message, exc.status_code) from exc
    except AIInputError as exc:
        raise _error_response(exc.code, exc.message, exc.status_code) from exc
    except ProfileDraftNotFound as exc:
        raise _error_response(exc.code, exc.message, exc.status_code) from exc
    except TaskError as exc:
        raise _error_response(
            exc.code, exc.message, exc.status_code, retryable=exc.retryable
        ) from exc
    await db.commit()
    return _to_draft_read(draft)


@router.post(
    "/profile-drafts/{draft_id}/publish",
    response_model=ProfilePublishAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="发布已确认字段并创建投影任务",
    openapi_extra={
        # Task6 Step3：明确 expected_revision 是 query 参数（非 body），
        # Idempotency-Key 是 required header。两者均在 OpenAPI 中显式声明。
        "parameters": [
            {
                "name": "expected_revision",
                "in": "query",
                "required": True,
                "schema": {"type": "integer", "minimum": 0},
                "description": "草稿乐观锁版本；必须等于当前 draft 的 expected_revision",
            },
            {
                "name": "Idempotency-Key",
                "in": "header",
                "required": True,
                "schema": {"type": "string", "pattern": "^[A-Za-z0-9._:-]{8,128}$"},
                "description": "幂等键；同 key 同 payload 回放同一任务",
            },
        ],
    },
)
async def publish_profile_draft_route(
    draft_id: str = Path(..., min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$"),
    payload: dict | None = Body(default=None),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    expected_revision: int | None = Query(default=None, ge=0, alias="expected_revision"),
) -> ProfilePublishAccepted:
    """Publish only confirmed fields into an immutable revision + projection task.

    Task6 Step3：``expected_revision`` 作为 **query 参数** 传递（非 body），
    与 draft PATCH body 内的 ``expected_revision``（逐项乐观锁）语义分离、互不冲突。
    缺失 query 参数返回 ``400 AI_INPUT_INVALID``。

    Phase 3 P3-01 —— ``preview_id``(body 字段,可空)若存在,必须与 draft revision
    匹配且 status=active,否则 409 ``DRAFT_VERSION_CONFLICT``。不传则走旧流程(向后兼容)。
    """
    _require_profile_feature()
    _check_idempotency_key(idempotency_key)
    if expected_revision is None:
        raise _error_response(
            "AI_INPUT_INVALID",
            "publish 必须携带 expected_revision 查询参数",
            status.HTTP_400_BAD_REQUEST,
        )
    preview_id = None
    if isinstance(payload, dict):
        preview_id = payload.get("preview_id")
        if preview_id is not None and (not isinstance(preview_id, str) or not preview_id):
            raise _error_response(
                "AI_INPUT_INVALID",
                "preview_id 必须为非空字符串或省略",
                status.HTTP_400_BAD_REQUEST,
            )
    if preview_id:
        try:
            from app.services.ai.preview import (
                PreviewConflict,
                SqlPreviewRepository,
                confirm_publish_with_preview,
            )

            preview_repo = SqlPreviewRepository(db)
            await confirm_publish_with_preview(
                user_id=current.id,
                draft_id=draft_id,
                expected_revision=int(expected_revision),
                preview_id=str(preview_id),
                repo=preview_repo,
            )
        except PreviewConflict as exc:
            raise _error_response(
                exc.code, exc.message, status.HTTP_409_CONFLICT
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise _error_response(
                "AI_TEMPORARILY_UNAVAILABLE",
                "预览校验失败,请稍后",
                status.HTTP_503_SERVICE_UNAVAILABLE,
                retryable=True,
            ) from exc
    try:
        submission = await publish_profile_draft(
            db, draft_id, current.id, expected_revision, idempotency_key
        )
    except DraftVersionConflict as exc:
        raise _error_response(exc.code, exc.message, exc.status_code) from exc
    except DraftStatusConflict as exc:
        raise _error_response(exc.code, exc.message, exc.status_code) from exc
    except AIInputError as exc:
        raise _error_response(exc.code, exc.message, exc.status_code) from exc
    except ProfileDraftNotFound as exc:
        raise _error_response(exc.code, exc.message, exc.status_code) from exc
    except TaskError as exc:
        raise _error_response(
            exc.code, exc.message, exc.status_code, retryable=exc.retryable
        ) from exc
    await db.commit()
    revision = submission.revision
    return ProfilePublishAccepted(
        task_id=submission.task_id,
        status=submission.status,
        stage=None,
        poll_after_ms=1000 if not submission.replayed else 0,
        expires_at=None,
        replayed=submission.replayed,
        revision_id=revision.revision_id if revision else None,
        revision_no=revision.revision_no if revision else None,
        subject=ProfileSubject(revision.subject) if revision else None,
        field_count=len(revision.changed_field_keys) if revision else None,
        narrative_task_id=submission.narrative_task_id,
        synced_profile_fields=list(getattr(submission, "synced_profile_fields", ()) or ()),
    )


# ===== Phase 3 P3-01: 成稿预览 =====
@router.post(
    "/profile-drafts/{draft_id}/preview",
    status_code=status.HTTP_202_ACCEPTED,
    summary="生成/复用草稿预览(Phase 3 P3-01)",
)
async def create_profile_preview_route(
    draft_id: str = Path(..., min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$"),
    payload: dict | None = Body(default=None),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """为当前草稿 revision 生成/复用预览。

    请求 body: ``{"expected_revision": <int>}`` —— 必须等于 draft.expected_revision。
    响应: ``{preview_id, draft_id, expected_revision, status, content}``。
    旧客户端不传 preview_id 仍可走 publish(向后兼容)。
    """
    _require_profile_feature()
    if not isinstance(payload, dict):
        raise _error_response(
            "AI_INPUT_INVALID",
            "body 必须为 JSON 对象,包含 expected_revision",
            status.HTTP_400_BAD_REQUEST,
        )
    expected_revision = payload.get("expected_revision")
    if not isinstance(expected_revision, int) or expected_revision < 0:
        raise _error_response(
            "AI_INPUT_INVALID",
            "expected_revision 必须为非负整数",
            status.HTTP_400_BAD_REQUEST,
        )
    try:
        from app.services.ai.preview import (
            PreviewConflict,
            SqlPreviewRepository,
            generate_preview,
        )

        preview_repo = SqlPreviewRepository(db)
        rec = await generate_preview(
            user_id=current.id,
            draft_id=draft_id,
            expected_revision=expected_revision,
            repo=preview_repo,
        )
    except PreviewConflict as exc:
        # DRAFT_NOT_FOUND → 404;DRAFT_VERSION_CONFLICT → 409
        status_code = (
            status.HTTP_404_NOT_FOUND
            if exc.code == "DRAFT_NOT_FOUND"
            else status.HTTP_409_CONFLICT
        )
        raise _error_response(exc.code, exc.message, status_code) from exc
    except Exception as exc:  # noqa: BLE001
        raise _error_response(
            "AI_TEMPORARILY_UNAVAILABLE",
            "生成预览失败,请稍后重试",
            status.HTTP_503_SERVICE_UNAVAILABLE,
            retryable=True,
        ) from exc
    await db.commit()
    return {
        "preview_id": rec.preview_id,
        "draft_id": rec.draft_id,
        "expected_revision": rec.expected_revision,
        "subject": rec.subject,
        "status": rec.status,
        "content": rec.content,
        "task_id": rec.task_id,
    }


@router.get(
    "/profile-previews/{preview_id}",
    status_code=status.HTTP_200_OK,
    summary="读取草稿预览(Phase 3 P3-01)",
)
async def get_profile_preview_route(
    preview_id: str = Path(..., min_length=1, max_length=96),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """读取本人预览;越权/不存在 → 404。"""
    _require_profile_feature()
    try:
        from app.services.ai.preview import SqlPreviewRepository, get_preview

        preview_repo = SqlPreviewRepository(db)
        rec = await get_preview(
            user_id=current.id, preview_id=preview_id, repo=preview_repo
        )
    except Exception as exc:  # noqa: BLE001
        raise _error_response(
            "AI_TEMPORARILY_UNAVAILABLE",
            "读取预览失败",
            status.HTTP_503_SERVICE_UNAVAILABLE,
            retryable=True,
        ) from exc
    if rec is None:
        raise _error_response(
            "PREVIEW_NOT_FOUND", "预览不存在或不属于当前用户", status.HTTP_404_NOT_FOUND
        )
    return {
        "preview_id": rec.preview_id,
        "draft_id": rec.draft_id,
        "expected_revision": rec.expected_revision,
        "subject": rec.subject,
        "status": rec.status,
        "content": rec.content,
        "task_id": rec.task_id,
        "last_error": rec.last_error,
        "created_at": rec.created_at,
        "updated_at": rec.updated_at,
    }


@router.get(
    "/profile-revisions",
    response_model=ProfileRevisionPage,
    status_code=status.HTTP_200_OK,
    summary="查询本人的发布版本历史（游标，只读）",
)
async def list_profile_revisions_route(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=20, ge=1, le=100),
) -> ProfileRevisionPage:
    """Return the owner's immutable revision history; nothing is leaked."""
    _require_profile_feature()
    page = await list_profile_revisions(db, current.id, cursor, limit)
    return page


@router.post(
    "/profile-revisions/{revision_id}/restore",
    response_model=ProfileDraftRead,
    status_code=status.HTTP_201_CREATED,
    summary="从历史版本恢复为新的可编辑草稿",
)
async def restore_profile_revision_route(
    revision_id: int,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ProfileDraftRead:
    """Restore a snapshot into a new draft; the old revision stays read-only."""
    _require_profile_feature()
    _check_idempotency_key(idempotency_key)
    try:
        draft = await restore_profile_revision(
            db, revision_id, current.id, idempotency_key or ""
        )
    except ProfileRevisionNotFound as exc:
        raise _error_response(exc.code, exc.message, exc.status_code) from exc
    except AIInputError as exc:
        raise _error_response(exc.code, exc.message, exc.status_code) from exc
    await db.commit()
    return _to_draft_read(draft)


@router.get(
    "/profiles/{subject}/fields",
    response_model=ProfilePublishedFieldsPage,
    status_code=status.HTTP_200_OK,
    summary="读取本人最新发布画像字段（条目按分类携带 is_new 角标）",
)
async def list_published_profile_fields_route(
    subject: ProfileSubject,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProfilePublishedFieldsPage:
    """WP-P4b：New 条目置顶（is_new DESC, updated_at DESC），旧条目保留原位。"""
    _require_profile_feature()
    fields = await list_published_profile_fields(db, current.id, subject.value)
    return ProfilePublishedFieldsPage(subject=subject, fields=fields)


@router.delete(
    "/profiles/{subject}",
    response_model=CleanupTaskAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="删除 AI 画像并创建清理任务（同步隐藏）",
)
async def delete_ai_profile_route(
    subject: ProfileSubject,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> CleanupTaskAccepted:
    """Delete one subject's AI profile: drafts/results hidden synchronously."""
    _require_profile_feature()
    _check_idempotency_key(idempotency_key)
    try:
        task = await delete_ai_profile(db, current.id, subject, idempotency_key)
    except TaskError as exc:
        raise _error_response(
            exc.code, exc.message, exc.status_code, retryable=exc.retryable
        ) from exc
    await db.commit()
    return CleanupTaskAccepted(
        task_id=task.task_id, status=task.status, cleanup_requested=True
    )


@router.delete(
    "/profiles/{subject}/fields/{field_key}",
    response_model=CleanupTaskAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="删除画像单个字段并创建失效任务",
)
async def delete_ai_profile_field_route(
    subject: ProfileSubject,
    field_key: str = Path(..., min_length=1, max_length=64),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> CleanupTaskAccepted:
    """Hide one field synchronously and enqueue its invalidation task."""
    _require_profile_feature()
    _check_idempotency_key(idempotency_key)
    try:
        task = await delete_ai_profile_field(
            db, current.id, subject, field_key, idempotency_key
        )
    except AIInputError as exc:
        raise _error_response(exc.code, exc.message, exc.status_code) from exc
    except TaskError as exc:
        raise _error_response(
            exc.code, exc.message, exc.status_code, retryable=exc.retryable
        ) from exc
    await db.commit()
    return CleanupTaskAccepted(
        task_id=task.task_id, status=task.status, cleanup_requested=True
    )


@router.get(
    "/profiles/{subject}/narrative",
    response_model=ProfileNarrativeRead,
    status_code=status.HTTP_200_OK,
    summary="获取画像叙事层（AI 人格画像成品）",
)
async def get_profile_narrative_route(
    subject: ProfileSubject,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProfileNarrativeRead:
    """返回用户最新发布的画像叙事层成品。

    叙事层在发布画像后由 Worker 异步生成（``profile_narrative`` 任务），
    包含人格标题、标签、AI 洞察、维度卡片、理想型权重和最近变化趋势。
    如果尚未发布或叙事层任务尚未完成，返回 ``status='pending'``。
    """
    _require_profile_feature()
    narrative = await load_published_narrative(db, current.id, subject.value)
    if narrative is None or narrative.get("data") is None:
        return ProfileNarrativeRead(
            subject=subject.value,
            status="pending",
        )
    data: dict = narrative["data"]
    raw_dimensions = list(data.get("dimensions") or [])
    dimensions = []
    for item in raw_dimensions:
        if not isinstance(item, dict):
            continue
        cleaned = dict(item)
        cleaned["icon"] = sanitize_narrative_dimension_icon(
            str(cleaned.get("key") or ""), cleaned.get("icon")
        )
        dimensions.append(cleaned)
    return ProfileNarrativeRead(
        subject=subject.value,
        status=str(narrative.get("status") or "published"),
        persona_title=str(data.get("persona_title") or ""),
        persona_tags=list(data.get("persona_tags") or []),
        insight=str(data.get("insight") or ""),
        dimensions=dimensions,
        ideal_weights=list(data.get("ideal_weights") or []),
        recent_change=data.get("recent_change"),
        history_observations=list(data.get("history_observations") or []),
        conclusion=str(data.get("conclusion") or ""),
        emotional_insight=data.get("emotional_insight"),
    )


@router.post(
    "/profiles/{subject}/narrative/confirm",
    response_model=ProfileNarrativeRead,
    status_code=status.HTTP_200_OK,
    summary="确认画像叙事层（待确认 → 已确认）",
)
async def confirm_profile_narrative_route(
    subject: ProfileSubject,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProfileNarrativeRead:
    """确认最新一条画像叙事层（良配对齐 WP-P3 确认闭环）。

    叙事层由 Worker 生成后为 ``pending_confirmation``，用户确认后转
    ``confirmed``。无任何可确认的叙事层返回 ``404 NARRATIVE_NOT_FOUND``。
    """
    _require_profile_feature()
    changed = await confirm_profile_narrative(db, current.id, subject.value)
    if not changed:
        raise _error_response(
            "NARRATIVE_NOT_FOUND",
            "暂无可确认的画像叙事层",
            status.HTTP_404_NOT_FOUND,
        )
    await db.commit()
    narrative = await load_published_narrative(db, current.id, subject.value)
    data: dict = narrative["data"] if narrative and narrative.get("data") else {}
    return ProfileNarrativeRead(
        subject=subject.value,
        status=str((narrative or {}).get("status") or "confirmed"),
        persona_title=str(data.get("persona_title") or ""),
        persona_tags=list(data.get("persona_tags") or []),
        insight=str(data.get("insight") or ""),
        emotional_insight=data.get("emotional_insight"),
    )


@router.post(
    "/profiles/{subject}/narrative/regenerate",
    response_model=ProfilePublishAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="重新生成画像叙事层（每日限 5 次）",
)
async def regenerate_profile_narrative_route(
    subject: ProfileSubject,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ProfilePublishAccepted:
    """重新生成画像叙事层：入队新 ``profile_narrative`` 任务，202 + ``task_id``。

    任务创建复用 publish 流程的同一入口（``_enqueue_narrative_task``）；
    24h（UTC）内该用户 ``profile_narrative`` 任务满 5 次返回
    ``400 AI_INPUT_INVALID``。前端凭 ``task_id`` 轮询任务状态。
    """
    _require_profile_feature()
    _check_idempotency_key(idempotency_key)
    try:
        task = await request_narrative_regenerate(
            db, current.id, subject.value, idempotency_key or ""
        )
    except AIInputError as exc:
        raise _error_response(exc.code, exc.message, exc.status_code) from exc
    except AIConsentRequired as exc:
        raise _error_response(exc.code, exc.message, exc.status_code) from exc
    except TaskError as exc:
        raise _error_response(
            exc.code, exc.message, exc.status_code, retryable=exc.retryable
        ) from exc
    await db.commit()
    return ProfilePublishAccepted(task_id=task.task_id, status=task.status)

"""墨相师四阶段融合 Phase 2 REST 路由 (P2-01 / P2-04)。

挂载前缀 ``/ai``(见 ``app/api/router.py``)。

路径:

- ``GET /ai/moxiang/state`` —— 装配 Contract v1.1 §6.1 响应;Phase 2 增加
  ``can_start_ideal_partner`` 顶层字段供前端"双阶段卡"使用。
- ``POST /ai/moxiang/journey/start?subject=ideal_partner`` —— Phase 2 P2-04:
  用户从档案入口主动开启/恢复 ideal_partner master session。
  前置条件:

  1) ``profile_text_extract`` 授权存在;
  2) personal 已发布(``published_revision_id`` 非空)或该用户已有 ideal_partner
     任何历史记录(老用户恢复路径,P2-01);
  3) subject 必须是 ``ideal_partner``;personal 不通过此入口开启(只走自然聊天)。

未授权 → 200 + ``consent_granted=false``(契约 §6.1)。其他错误统一使用
``AiErrorResponse`` 形状,request_id 由 logging 上下文提供。

不引入 alembic/新依赖;不重复写 profile.py 已有的事务/快照逻辑。
"""

from __future__ import annotations

import json
import re
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Path, Query, status
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, get_current_user
from app.core.config import settings
from app.core.logging import request_id_context
from app.db.session import get_db
from app.schemas.ai_common import AiErrorResponse
from app.schemas.ai_moxiang import (
    MoxiangStateResponse,
    MoxiangTurn,
    MoxiangTurnsResponse,
)
from app.schemas.ai_profile import (
    ContinuousBuildRequest,
    ContinuousStateResponse,
    ContinuousTurnRead,
    ContinuousTurnsResponse,
    ProfileSubject,
)
from app.services.ai.continuous import (
    build_continuous_draft,
    build_state as build_continuous_state,
    list_continuous_turns,
)
from app.services.ai.flags import AiFeature, AiFeatureDisabledError, require_ai_feature
from app.services.ai.moxiang_state import build_state_response, list_turns
from app.services.ai.moxiang_state_db import MoxiangStateSqlRepository
from app.services.ai.profile import (
    AIConsentRequired,
    AIInputError,
    ProfileSessionNotFound,
    create_master_session,
    load_owned_session,
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


def _require_journey_feature() -> None:
    """Require the existing journey and profile feature gates."""
    if not bool(getattr(settings, "ai_moxiang_journey_enabled", False)):
        raise _error_response(
            "AI_FEATURE_DISABLED",
            "墨相师连续旅程当前未启用",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    try:
        require_ai_feature(AiFeature.PROFILE, settings)
    except AiFeatureDisabledError as exc:
        raise _error_response(
            exc.code,
            "AI 画像功能当前不可用",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc


def _continuous_response(payload: dict) -> ContinuousStateResponse:
    return ContinuousStateResponse.model_validate(payload)


@router.get("/moxiang/continuous/state", response_model=ContinuousStateResponse)
async def get_continuous_state(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ContinuousStateResponse:
    _require_journey_feature()
    return _continuous_response(await build_continuous_state(db, current_user.id))


@router.get("/moxiang/continuous/turns", response_model=ContinuousTurnsResponse)
async def get_continuous_turns(
    limit: int = Query(default=50, ge=1, le=100),
    before_id: str | None = Query(default=None, max_length=32),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ContinuousTurnsResponse:
    _require_journey_feature()
    try:
        turns, next_id = await list_continuous_turns(db, current_user.id, limit, before_id)
    except PermissionError as exc:
        raise _error_response("AI_CONSENT_REQUIRED", "请先完成画像文本抽取授权", 403) from exc
    except ValueError as exc:
        raise _error_response("AI_INPUT_INVALID", str(exc), status.HTTP_400_BAD_REQUEST) from exc
    return ContinuousTurnsResponse(
        turns=[ContinuousTurnRead.model_validate(item) for item in turns],
        next_before_id=next_id,
    )


@router.post("/moxiang/continuous/build", response_model=ContinuousStateResponse)
async def build_continuous_route(
    subject: ProfileSubject = Query(...),
    body: ContinuousBuildRequest = Body(default=ContinuousBuildRequest()),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ContinuousStateResponse:
    _require_journey_feature()
    if not idempotency_key or not _IDEMPOTENCY_KEY_PATTERN.fullmatch(idempotency_key):
        raise _error_response("AI_INPUT_INVALID", "Idempotency-Key 必须为 8-128 位 ASCII 字符", 400)
    try:
        payload = await build_continuous_draft(
            db, current_user.id, subject.value, refresh=body.refresh, idempotency_key=idempotency_key
        )
    except PermissionError as exc:
        raise _error_response("AI_CONSENT_REQUIRED", "请先完成画像文本抽取授权", 403) from exc
    except LookupError as exc:
        raise _error_response("CONTINUOUS_BUILD_NOT_READY", "当前主体暂无足够可确认的新信息", 409) from exc
    except ValueError as exc:
        raise _error_response("AI_INPUT_INVALID", str(exc), 400) from exc
    except TaskError as exc:
        raise _error_response(exc.code, exc.message, exc.status_code, retryable=exc.retryable) from exc
    await db.commit()
    return _continuous_response(payload)


@router.get("/moxiang/state", response_model=MoxiangStateResponse)
async def get_moxiang_state(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MoxiangStateResponse:
    """``GET /api/v1/ai/moxiang/state``(契约 v1.1 §6.1)。

    未登录返回 401(由 ``get_current_user`` 负责)。已登录但未授权返回 200 +
    ``consent_granted=false``,绝不抛错。
    """
    repo = MoxiangStateSqlRepository(db)
    return await build_state_response(user_id=current_user.id, repo=repo)


@router.get(
    "/profile-sessions/{session_id}/turns",
    response_model=MoxiangTurnsResponse,
    status_code=status.HTTP_200_OK,
    summary="分页读取本人的墨相师会话历史",
)
async def get_session_turns(
    session_id: str = Path(..., min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$"),
    before_turn_no: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MoxiangTurnsResponse:
    """返回最新一页历史并隐藏外部用户会话是否存在。"""
    try:
        session = await load_owned_session(db, session_id, current_user.id)
    except ProfileSessionNotFound as exc:
        raise _error_response(exc.code, exc.message, exc.status_code) from exc
    except Exception as exc:  # noqa: BLE001
        raise _error_response(
            "AI_TEMPORARILY_UNAVAILABLE",
            "会话历史暂时无法读取,请稍后再试",
            status.HTTP_503_SERVICE_UNAVAILABLE,
            retryable=True,
        ) from exc

    repo = MoxiangStateSqlRepository(db)
    try:
        rows, next_before_turn_no = await list_turns(
            session_id=session_id,
            before_turn_no=before_turn_no,
            limit=limit,
            repo=repo,
        )
    except Exception as exc:  # noqa: BLE001
        raise _error_response(
            "AI_TEMPORARILY_UNAVAILABLE",
            "会话历史暂时无法读取,请稍后再试",
            status.HTTP_503_SERVICE_UNAVAILABLE,
            retryable=True,
        ) from exc

    turns: list[MoxiangTurn] = []
    for row in rows:
        created_at = row.get("created_at")
        if created_at is not None and hasattr(created_at, "isoformat"):
            created_at = created_at.isoformat()
        # 实时语音 v2 回复元数据：JSON 列经原生驱动可能回传字符串，
        # 统一收敛为 dict 或 None（解析失败按无元数据处理，不丢 turn）。
        raw_metadata = row.get("voice_reply_metadata")
        if isinstance(raw_metadata, str):
            try:
                raw_metadata = json.loads(raw_metadata)
            except (TypeError, ValueError):
                raw_metadata = None
        if not isinstance(raw_metadata, dict):
            raw_metadata = None
        turns.append(
            MoxiangTurn(
                turn_id=str(row["turn_id"]),
                turn_no=int(row["turn_no"]),
                role=str(row["role"]),
                answer_text=str(row["answer_text"]),
                client_turn_id=str(row["client_turn_id"]),
                created_at=str(created_at) if created_at is not None else None,
                voice_reply_metadata=raw_metadata,
            )
        )
    return MoxiangTurnsResponse(
        session_id=session_id,
        subject=session.subject.value,
        turns=tuple(turns),
        next_before_turn_no=next_before_turn_no,
    )


@router.post("/moxiang/journey/start")
async def start_journey(
    subject: str = Query(
        ...,
        description="开启/恢复的画像主体;Phase 2 仅允许 ideal_partner。",
        pattern=r"^(personal|ideal_partner)$",
    ),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """``POST /api/v1/ai/moxiang/journey/start`` —— Phase 2 P2-04。

    行为:

    - subject == ``personal``: 返回 400 ``AI_INPUT_INVALID``
      (个人画像只能从墨相师页自然聊天开始,不允许显式开启)。
    - subject == ``ideal_partner``:

      - 调用 ``build_state_response`` 计算 ``can_start_ideal_partner``;
      - 若 ``can_start_ideal_partner=false``(personal 未发布且无 ideal_partner
        历史)→ 返回 403 ``AI_INPUT_INVALID``,文案明确提示用户先完成我的墨相。
      - 否则调用 ``create_master_session`` 创建/恢复 master session;
      - 返回 ``{ subject, session_id, journey_stage, already_existed }``；
        ``already_existed`` 表示复用了既有活动会话（非本次插入），
        ``journey_stage`` 取该会话的真实阶段。
    """
    _require_journey_feature()
    if subject != ProfileSubject.IDEAL_PARTNER.value:
        raise _error_response(
            "AI_INPUT_INVALID",
            "此接口只用于开启愿遇之相;我的墨相请从墨相师页面进入",
            status.HTTP_400_BAD_REQUEST,
        )

    repo = MoxiangStateSqlRepository(db)
    state = await build_state_response(user_id=current_user.id, repo=repo)
    if not state.can_start_ideal_partner:
        raise _error_response(
            "AI_INPUT_INVALID",
            "请先完成我的墨相再开启愿遇之相",
            status.HTTP_403_FORBIDDEN,
        )

    try:
        session = await create_master_session(
            db,
            owner_user_id=current_user.id,
            subject=ProfileSubject.IDEAL_PARTNER,
            consent_version="profile-text-v1",
        )
    except AIConsentRequired as exc:
        raise _error_response(
            "AI_CONSENT_REQUIRED",
            "请先完成画像文本抽取授权",
            status.HTTP_403_FORBIDDEN,
        ) from exc
    except AIInputError as exc:
        raise _error_response(
            "AI_INPUT_INVALID",
            str(exc) or "参数不合法",
            status.HTTP_400_BAD_REQUEST,
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise _error_response(
            "AI_TEMPORARILY_UNAVAILABLE",
            "墨相师暂时无法开启愿遇之相,请稍后再试",
            status.HTTP_503_SERVICE_UNAVAILABLE,
            retryable=True,
        ) from exc

    # journey_stage 不在 profile 服务的会话列集合里，与 WS session_start 同源：
    # 按 session_id 实查，取不到行才回落默认阶段。
    stage_row = (
        await db.execute(
            sql_text(
                "SELECT journey_stage FROM ai_profile_session "
                "WHERE session_id = :session_id"
            ),
            {"session_id": session.session_id},
        )
    ).mappings().first()
    await db.commit()
    return {
        "subject": subject,
        "session_id": session.session_id,
        "journey_stage": str((stage_row or {}).get("journey_stage") or "chatting"),
        # created 只对「本次请求插入的行」为 True，复用（含并发冲突后回读复用
        # 赢家会话）为 False——与 WS 侧 ``resumed: not session.created`` 同源。
        "already_existed": not session.created,
    }


@router.get(
    "/moxiang/archive",
    summary="我的墨相档案聚合(Phase 3 P3-03)",
)
async def get_moxiang_archive(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """``GET /api/v1/ai/moxiang/archive`` —— 双主体归档聚合。

    永远 200(只要登录);未授权 / 无任何画像 → 字段全空 + fallback_available=False。
    路径不存在单独 404,避免暴露「是否有 profile」。
    """
    try:
        from app.services.ai.archive import (
            SqlArchiveRepository,
            build_archive,
        )

        repo = SqlArchiveRepository(db)
        archive = await build_archive(user_id=current_user.id, repo=repo)
    except Exception as exc:  # noqa: BLE001
        raise _error_response(
            "AI_TEMPORARILY_UNAVAILABLE",
            "档案暂时无法读取,请稍后再试",
            status.HTTP_503_SERVICE_UNAVAILABLE,
            retryable=True,
        ) from exc
    return archive.to_dict()

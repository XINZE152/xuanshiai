"""独立 AI 角色扮演 REST。

与知遇对话链路完全隔离：不使用 MasterWS、``submit_journey_turn`` 之外的
画像抽取入口、``moxiang_*`` prompt 或 continuous_v2 查询。对话消息写入
现有 ``ai_profile_turn`` 表，用 ``source_type='ai_roleplay'`` 打标，并使用
一个不登记在 ``ai_profile_session`` 中的独立 session_id —— 因此 continuous_v2
的 ``JOIN ai_profile_session`` 查询天然不会读到这些消息，无需改动任何既有 SQL。

兴趣是唯一的桥接：只有用户显式确认后，才复用现有「落 turn → 抽取候选 →
用户整份确认 → revision」链路把事实补充进本人画像。
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

from fastapi import APIRouter, Body, Depends, Header, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, get_current_user
from app.api.routes.ai_moxiang import _error_response
from app.core.config import settings
from app.db.session import get_db
from app.schemas.ai_common import AiErrorResponse
from app.services.ai.base import AITaskContext
from app.services.ai.flags import AiFeature, AiFeatureDisabledError, require_ai_feature
from app.services.ai.gateway import AIGateway
from app.services.ai.journey import submit_journey_turn
from app.services.ai.profile import moderate_text
from app.services.ai.roleplay_scene import (
    ROLEPLAY_PROMPT_VERSION,
    build_roleplay_system_prompt,
    public_scenes,
    validate_scene_key,
)

router = APIRouter()

ROLEPLAY_SOURCE_TYPE = "ai_roleplay"
ROLEPLAY_INTEREST_SOURCE_TYPE = "roleplay_interest_confirmed"
ROLEPLAY_NOTICE = "AI 角色扮演，角色与对白均为虚构，不代表真实用户或平台意见。"
MAX_HISTORY_TURNS = 12


class RoleplaySceneResponse(BaseModel):
    scenes: list[dict] = Field(default_factory=list)
    notice: str = ROLEPLAY_NOTICE


class RoleplayTurnResponse(BaseModel):
    turns: list[dict] = Field(default_factory=list)


class RoleplaySubmitRequest(BaseModel):
    scene_key: str = Field(..., min_length=1, max_length=64)
    user_message: str = Field(..., min_length=1, max_length=2000)
    client_turn_id: str = Field(..., min_length=8, max_length=128)
    custom_setting: str | None = Field(default=None, max_length=500)


class RoleplaySubmitResponse(BaseModel):
    session_id: str
    turn_id: str
    assistant_reply: str
    notice: str = ROLEPLAY_NOTICE


class RoleplayInterestConfirmRequest(BaseModel):
    turn_id: str = Field(..., min_length=1, max_length=128)
    tag: str = Field(..., min_length=1, max_length=64)
    confirm: bool = Field(..., description="必须显式为 true 才写入画像链路")


class RoleplayInterestConfirmResponse(BaseModel):
    accepted: bool
    notice: str


def _require_feature() -> None:
    try:
        require_ai_feature(AiFeature.PROFILE, settings)
    except AiFeatureDisabledError as exc:
        raise _error_response("AI_FEATURE_DISABLED", "AI 功能暂未开放", 503) from exc


def _check_idempotency_key(key: str | None) -> str:
    if not key or not 8 <= len(key) <= 128 or not key.isascii():
        raise _error_response("AI_INPUT_INVALID", "Idempotency-Key 必须为 8-128 位 ASCII", 400)
    return key


def _roleplay_session_id(user_id: int, scene_key: str) -> str:
    """独立命名空间；刻意不写入 ai_profile_session，避免与画像主会话争抢活动槽。"""
    return f"roleplay:{user_id}:{scene_key}"


@router.get(
    "/roleplay/scenes",
    response_model=RoleplaySceneResponse,
    responses={503: {"model": AiErrorResponse}},
)
async def get_roleplay_scenes(
    current_user: CurrentUser = Depends(get_current_user),
) -> RoleplaySceneResponse:
    _require_feature()
    return RoleplaySceneResponse(scenes=public_scenes())


@router.get(
    "/roleplay/turns",
    response_model=RoleplayTurnResponse,
    responses={400: {"model": AiErrorResponse}, 503: {"model": AiErrorResponse}},
)
async def get_roleplay_turns(
    scene_key: str = Query(..., min_length=1, max_length=64),
    limit: int = Query(50, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RoleplayTurnResponse:
    _require_feature()
    if not validate_scene_key(scene_key):
        raise _error_response("AI_INPUT_INVALID", "scene_key 不在允许范围内", 400)
    rows = (await db.execute(text(
        "SELECT turn_id, role, answer_text, created_at FROM ai_profile_turn "
        "WHERE user_id=:user_id AND session_id=:session_id AND source_type=:source "
        "AND status<>'deleted' ORDER BY id DESC LIMIT :limit"
    ), {"user_id": current_user.id, "session_id": _roleplay_session_id(current_user.id, scene_key),
        "source": ROLEPLAY_SOURCE_TYPE, "limit": limit})).mappings().all()
    turns = [
        {
            "turn_id": str(row["turn_id"]),
            "role": str(row["role"]),
            "answer_text": str(row["answer_text"]),
            "created_at": row["created_at"].isoformat() if isinstance(row["created_at"], datetime) else None,
        }
        for row in reversed(rows)
    ]
    return RoleplayTurnResponse(turns=turns)


@router.post(
    "/roleplay/submit",
    response_model=RoleplaySubmitResponse,
    responses={400: {"model": AiErrorResponse}, 409: {"model": AiErrorResponse},
               422: {"model": AiErrorResponse}, 503: {"model": AiErrorResponse}},
)
async def submit_roleplay_turn(
    request: RoleplaySubmitRequest = Body(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> RoleplaySubmitResponse:
    _require_feature()
    key = _check_idempotency_key(idempotency_key)
    if not validate_scene_key(request.scene_key):
        raise _error_response("AI_INPUT_INVALID", "scene_key 不在允许范围内", 400)
    message = request.user_message.strip()
    if not message:
        raise _error_response("AI_INPUT_INVALID", "user_message 不能为空", 400)

    user_id = int(current_user.id)
    session_id = _roleplay_session_id(user_id, request.scene_key)

    # 幂等回放：同一键 + 同一请求返回原回复；同键不同请求明确冲突。
    replay = (await db.execute(text(
        "SELECT turn_id, answer_text FROM ai_profile_turn "
        "WHERE user_id=:user_id AND session_id=:session_id AND client_turn_id=:cid "
        "AND role='assistant' AND source_type=:source LIMIT 1"
    ), {"user_id": user_id, "session_id": session_id, "cid": key + ":a",
        "source": ROLEPLAY_SOURCE_TYPE})).mappings().first()
    if replay is not None:
        stored = (await db.execute(text(
            "SELECT answer_text FROM ai_profile_turn "
            "WHERE user_id=:user_id AND session_id=:session_id AND client_turn_id=:cid "
            "AND role='user' AND source_type=:source LIMIT 1"
        ), {"user_id": user_id, "session_id": session_id, "cid": key + ":u",
            "source": ROLEPLAY_SOURCE_TYPE})).mappings().first()
        if stored is None or str(stored["answer_text"]) != message:
            raise _error_response("TASK_IDEMPOTENCY_CONFLICT", "幂等键已用于不同请求", 409)
        return RoleplaySubmitResponse(
            session_id=session_id, turn_id=str(replay["turn_id"]),
            assistant_reply=str(replay["answer_text"]),
        )

    moderation = await moderate_text(db, message, field="AI 角色扮演")
    if moderation.action == "reject":
        raise _error_response("AI_POLICY_DENIED", "内容未通过安全校验", 422)
    if moderation.action == "replace" and moderation.display_content:
        message = str(moderation.display_content)

    setting = (request.custom_setting or "").strip()
    if setting:
        setting_moderation = await moderate_text(db, setting, field="AI 角色扮演设定")
        if setting_moderation.action == "reject":
            raise _error_response("AI_POLICY_DENIED", "场景设定未通过安全校验", 422)

    history: list[dict[str, str]] = []
    prior = (await db.execute(text(
        "SELECT role, answer_text FROM ai_profile_turn "
        "WHERE user_id=:user_id AND session_id=:session_id AND source_type=:source "
        "AND status<>'deleted' ORDER BY id DESC LIMIT :limit"
    ), {"user_id": user_id, "session_id": session_id, "source": ROLEPLAY_SOURCE_TYPE,
        "limit": MAX_HISTORY_TURNS * 2})).mappings().all()
    for row in reversed(prior):
        history.append({"role": str(row["role"]), "content": str(row["answer_text"])})
    history.append({"role": "user", "content": message})

    context = AITaskContext(
        task_id=uuid.uuid4().hex, request_id=uuid.uuid4().hex, scene="ai_roleplay",
        provider=settings.ai_provider_name, model=settings.ai_model_name,
        prompt_version=ROLEPLAY_PROMPT_VERSION, schema_version="roleplay-v1",
    )
    outcome = await AIGateway(timeout_seconds=settings.ai_gateway_timeout_seconds).chat(
        context,
        [{"role": "system", "content": build_roleplay_system_prompt(request.scene_key, setting)}, *history],
    )
    if outcome.result is None or not str(outcome.result).strip():
        raise _error_response(
            outcome.error_code or "AI_TEMPORARILY_UNAVAILABLE", "AI 暂时无法回复，请稍后重试", 503
        )
    assistant_reply = str(outcome.result).strip()[:4000]

    next_no = int((await db.execute(text(
        "SELECT COALESCE(MAX(turn_no), 0) + 1 FROM ai_profile_turn WHERE session_id=:sid"
    ), {"sid": session_id})).scalar() or 1)
    user_turn_id, assistant_turn_id = uuid.uuid4().hex, uuid.uuid4().hex
    await db.execute(text(
        "INSERT INTO ai_profile_turn "
        "(turn_id, session_id, client_turn_id, user_id, turn_no, role, answer_text, status, source_type, created_at) "
        "VALUES (:tid, :sid, :cid, :uid, :no, 'user', :text, 'saved', :source, UTC_TIMESTAMP())"
    ), {"tid": user_turn_id, "sid": session_id, "cid": key + ":u", "uid": user_id,
        "no": next_no, "text": message, "source": ROLEPLAY_SOURCE_TYPE})
    await db.execute(text(
        "INSERT INTO ai_profile_turn "
        "(turn_id, session_id, client_turn_id, user_id, turn_no, role, answer_text, status, source_type, created_at) "
        "VALUES (:tid, :sid, :cid, :uid, :no, 'assistant', :text, 'saved', :source, UTC_TIMESTAMP())"
    ), {"tid": assistant_turn_id, "sid": session_id, "cid": key + ":a", "uid": user_id,
        "no": next_no + 1, "text": assistant_reply, "source": ROLEPLAY_SOURCE_TYPE})
    await db.commit()
    return RoleplaySubmitResponse(
        session_id=session_id, turn_id=assistant_turn_id, assistant_reply=assistant_reply
    )


@router.post(
    "/roleplay/interests/confirm",
    response_model=RoleplayInterestConfirmResponse,
    responses={400: {"model": AiErrorResponse}, 409: {"model": AiErrorResponse},
               422: {"model": AiErrorResponse}, 503: {"model": AiErrorResponse}},
)
async def confirm_roleplay_interest(
    request: RoleplayInterestConfirmRequest = Body(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> RoleplayInterestConfirmResponse:
    """用户显式确认后，把兴趣作为已确认事实接入现有画像候选链。

    不自动抽取、不写 ``ai_profile_revision_field``；后续仍须用户在知遇里
    整份确认，才会进入正式画像。
    """
    _require_feature()
    _check_idempotency_key(idempotency_key)
    if not request.confirm:
        raise _error_response("AI_INPUT_INVALID", "未经确认的兴趣不会写入画像", 400)
    tag = request.tag.strip()
    if not tag or len(tag) > 64 or any(ch.isdigit() for ch in tag):
        raise _error_response("AI_INPUT_INVALID", "兴趣标签不合法", 400)
    if any(word in tag for word in ("电话", "微信", "住址", "身份证", "密码")):
        raise _error_response("AI_INPUT_INVALID", "兴趣标签不得包含联系方式或敏感信息", 400)

    # 确认的兴趣必须归属本人角色扮演轮次，不能凭空写入。
    owned = (await db.execute(text(
        "SELECT turn_id FROM ai_profile_turn WHERE turn_id=:turn_id AND user_id=:user_id "
        "AND source_type=:source AND role IN ('user','assistant') LIMIT 1"
    ), {"turn_id": request.turn_id, "user_id": current_user.id,
        "source": ROLEPLAY_SOURCE_TYPE})).mappings().first()
    if owned is None:
        raise _error_response("AI_INPUT_INVALID", "待确认的兴趣没有对应的角色扮演记录", 400)

    session_row = (await db.execute(text(
        "SELECT session_id FROM ai_profile_session "
        "WHERE user_id=:user_id AND session_kind='master' AND active_status=1 "
        "AND status NOT IN ('deleted','cancelled','stale','failed') "
        "ORDER BY id DESC LIMIT 1"
    ), {"user_id": current_user.id})).mappings().first()
    if session_row is None:
        raise _error_response(
            "AI_CONSENT_REQUIRED", "请先完成一次知遇授权对话，兴趣才能同步到画像", 409
        )

    statement = f"我确认：{tag}是我真实喜欢的事。"
    try:
        await submit_journey_turn(
            db,
            session_id=str(session_row["session_id"]),
            owner_user_id=current_user.id,
            client_turn_id=f"roleplay-interest:{request.turn_id}:{hashlib.sha256(tag.encode()).hexdigest()[:16]}",
            answer_text=statement,
            flow_version="continuous_v2",
        )
    except Exception as exc:  # noqa: BLE001 - 会话状态变化需转成稳定业务错误
        raise _error_response("AI_INPUT_INVALID", "兴趣暂时无法同步，请稍后重试", 409) from exc
    await db.commit()
    return RoleplayInterestConfirmResponse(
        accepted=True,
        notice="已记录你确认的兴趣；仍需你在知遇中整份确认后才会进入正式画像。",
    )

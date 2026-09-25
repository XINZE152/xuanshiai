"""墨相师实时语音 v2 的路由侧桥接层。

把 :class:`RealtimeVoiceSession` 需要的业务回调落到现有旅程/画像设施上：
instructions 组装、最终转写提交（现有旅程事务）、助手回复元数据落库、
额度控制与单会话守卫。路由层只负责 WS 消息分派，不接触供应商协议。

日志只记时序、字节数、状态和错误码，不记录音频、转写、提示词或密钥。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

import uuid

from app.services.ai.audit import GenerationAuditEvent, record_generation_audit
from app.services.ai.prompts.moxiang_master import MOXIANG_MASTER_PROMPT_VERSION
from app.core.config import settings
from app.db.session import session_factory as _db_session_factory
from app.services.ai.profile import (
    persist_master_assistant_reply,
    update_voice_reply_metadata,
)
from app.services.ai.prompts.moxiang_master import (
    build_realtime_instructions,
    build_realtime_update_instructions,
)
from app.services.voice.realtime.provider import RealtimeProviderConfig
from app.services.voice.realtime.session import (
    RealtimeSessionCallbacks,
    RealtimeVoiceSession,
    TurnOutcome,
)

logger = logging.getLogger(__name__)

# 每用户同时一条语音会话（方案 §5 初始灰度默认值）。
# 进程内守卫：多 worker 部署下按 worker 各自约束，整体仍由前端单入口与
# 每日额度兜底；跨进程精确互斥留待灰度数据后再评估。
_ACTIVE_REALTIME_SESSIONS: dict[int, RealtimeVoiceSession] = {}

_DAILY_QUOTA_KEY_PREFIX = "ai:rt-voice:daily"
_DAILY_QUOTA_TTL_SECONDS = 2 * 24 * 3600


def realtime_gate_error() -> str | None:
    """v2 实时语音门禁：None 表示通过，否则返回可下发的错误码。"""
    if not bool(getattr(settings, "ai_realtime_voice_enabled", False)):
        return "AI_FEATURE_DISABLED"
    provider = str(getattr(settings, "ai_realtime_voice_provider", "") or "")
    if provider != "senseaudio":
        return "AI_FEATURE_DISABLED"
    if settings.ai_senseaudio_api_key is None:
        return "AI_FEATURE_DISABLED"
    return None


def build_provider_config() -> RealtimeProviderConfig:
    return RealtimeProviderConfig(
        ws_url=settings.ai_senseaudio_ws_url,
        api_key=settings.ai_senseaudio_api_key.get_secret_value()
        if settings.ai_senseaudio_api_key
        else "",
        model=settings.ai_senseaudio_model,
        voice=settings.ai_senseaudio_voice,
    )


def acquire_session_slot(user_id: int) -> bool:
    """占用每用户单会话名额；重复进入返回 False。"""
    existing = _ACTIVE_REALTIME_SESSIONS.get(user_id)
    if existing is not None and not existing_input_stale(existing):
        return False
    return True


def existing_input_stale(session: RealtimeVoiceSession) -> bool:
    """上一条会话空闲超过退出阈值时视为已结束，允许接管。"""
    return (
        session.idle_seconds > settings.ai_realtime_idle_exit_seconds
    )


def register_session(user_id: int, session: RealtimeVoiceSession) -> None:
    _ACTIVE_REALTIME_SESSIONS[user_id] = session


def release_session_slot(user_id: int) -> None:
    current = _ACTIVE_REALTIME_SESSIONS.get(user_id)
    if current is not None:
        _ACTIVE_REALTIME_SESSIONS.pop(user_id, None)


# ----------------------------------------------------------------------
# 每日连接时长额度（Redis 原子；限额存储不可用时停止新语音会话）
# ----------------------------------------------------------------------


def _daily_key(user_id: int) -> str:
    day = datetime.now(UTC).strftime("%Y%m%d")
    return f"{_DAILY_QUOTA_KEY_PREFIX}:{user_id}:{day}"


async def realtime_daily_minutes_used(user_id: int) -> int | None:
    """读取当日已用连接分钟数；Redis 不可用返回 None（fail closed）。"""
    try:
        from app.core.redis import redis_client

        raw = await redis_client.get(_daily_key(user_id))
        return int(raw) if raw is not None else 0
    except Exception:  # noqa: BLE001
        logger.debug("realtime_quota_read_failed", exc_info=True)
        return None


async def consume_realtime_minutes(user_id: int, minutes: int) -> None:
    """按实际用满分钟数累计当日额度（best-effort，只在关闭时结算）。"""
    if minutes <= 0:
        return
    try:
        from app.core.redis import redis_client

        key = _daily_key(user_id)
        await redis_client.incrby(key, minutes)
        await redis_client.expire(key, _DAILY_QUOTA_TTL_SECONDS)
    except Exception:  # noqa: BLE001
        logger.debug("realtime_quota_consume_failed", exc_info=True)


@dataclass
class RealtimeRouteContext:
    """当前业务会话状态（主体切换时由路由更新）。"""

    session_id: str
    subject: str
    narrative_context: str


class MoxiangRealtimeBridge:
    """把实时会话回调绑定到某条 WS 连接的某个用户。"""

    def __init__(
        self,
        *,
        ws: Any,
        user_id: int,
        context: RealtimeRouteContext,
        poll_tasks: set[asyncio.Task[None]],
        emit: Callable[[dict[str, Any]], Awaitable[None]],
        submit_candidate: Callable[[str, str], Awaitable[str]],
    ) -> None:
        self._ws = ws
        self._user_id = user_id
        self.context = context
        self._poll_tasks = poll_tasks
        self._emit = emit
        # 路由注入的候选提交钩子：落库+入队+推送 extraction_status+启动监听。
        self._submit_candidate = submit_candidate
        self.session: RealtimeVoiceSession | None = None
        self.started_at = time.monotonic()

    # -- instructions --------------------------------------------------

    async def build_instructions(self) -> str:
        history = await self._load_history()
        return build_realtime_instructions(
            subject=self.context.subject,
            narrative_context=self.context.narrative_context,
            build_context=await self._build_context(),
            history=history,
        )

    async def build_update_instructions(self) -> str:
        return build_realtime_update_instructions(
            subject=self.context.subject,
            narrative_context=self.context.narrative_context,
            build_context=await self._build_context(),
        )

    async def _build_context(self) -> str:
        from app.api.routes.voice_moxiang import _journey_build_context

        ctx = await _journey_build_context(
            self.context.session_id, self.context.subject
        )
        return ctx or ""

    async def _load_history(self) -> list[dict[str, str]]:
        from app.api.routes.voice_moxiang import _load_master_history

        if _db_session_factory is None or not self.context.session_id:
            return []
        try:
            async with _db_session_factory() as db:
                return await _load_master_history(db, self.context.session_id)
        except Exception:  # noqa: BLE001
            logger.debug("realtime_history_load_failed", exc_info=True)
            return []

    # -- 业务回调 -------------------------------------------------------

    async def submit_final_transcript(
        self, text: str, client_turn_id: str
    ) -> TurnOutcome:
        turn_id, task_id = await self._submit_candidate(text, client_turn_id)
        return TurnOutcome(turn_id=turn_id, task_id=task_id)

    async def persist_reply(self, text: str, metadata: dict[str, Any]) -> str:
        if _db_session_factory is None or not self.context.session_id:
            return ""
        try:
            async with _db_session_factory() as db:
                turn_id = await persist_master_assistant_reply(
                    db,
                    self.context.session_id,
                    self._user_id,
                    text,
                    voice_reply_metadata=metadata,
                )
                await db.commit()
            return turn_id
        except Exception:  # noqa: BLE001
            logger.exception("realtime_persist_reply_failed")
            return ""

    async def update_reply_metadata(
        self, assistant_turn_id: str, metadata: dict[str, Any]
    ) -> None:
        if _db_session_factory is None or not assistant_turn_id:
            return
        try:
            async with _db_session_factory() as db:
                await update_voice_reply_metadata(db, assistant_turn_id, metadata)
                await db.commit()
        except Exception:  # noqa: BLE001
            logger.exception("realtime_update_metadata_failed")

    async def _audit_session_started(self) -> None:
        """会话开始只记 scene 与 prompt 版本，不记音频、转写或供应商事件。"""
        try:
            await record_generation_audit(
                GenerationAuditEvent(
                    request_id=uuid.uuid4().hex,
                    task_id=None,
                    scene="moxiang_realtime_session",
                    provider=settings.ai_realtime_voice_provider or "senseaudio",
                    model=settings.ai_senseaudio_model,
                    prompt_version=MOXIANG_MASTER_PROMPT_VERSION,
                    schema_version="moxiang-realtime-v2",
                    status="started",
                    display_eligible=False,
                )
            )
        except Exception:  # noqa: BLE001
            logger.warning("realtime_session_audit_failed user_id=%s", self._user_id)

    # -- 会话生命周期 ---------------------------------------------------

    async def start_session(self) -> RealtimeVoiceSession | None:
        """创建并启动实时会话；失败返回 None（错误已发给客户端）。"""
        await self._audit_session_started()
        callbacks = RealtimeSessionCallbacks(
            emit=self._emit,
            build_instructions=self.build_instructions,
            build_update_instructions=self.build_update_instructions,
            submit_final_transcript=self.submit_final_transcript,
            persist_reply=self.persist_reply,
            update_reply_metadata=self.update_reply_metadata,
        )
        session = RealtimeVoiceSession(
            provider_config=build_provider_config(),
            callbacks=callbacks,
            submission_hold_seconds=settings.ai_realtime_submission_hold_seconds,
        )
        ok = await session.start()
        if not ok:
            return None
        self.session = session
        register_session(self._user_id, session)
        return session

    async def close_session(self) -> None:
        """连接收尾：结算额度、释放单会话名额、关闭实时会话。"""
        session = self.session
        self.session = None
        elapsed_minutes = int((time.monotonic() - self.started_at) // 60)
        await consume_realtime_minutes(self._user_id, elapsed_minutes)
        release_session_slot(self._user_id)
        if session is not None:
            await session.aclose()


async def realtime_watchdog(
    bridge: MoxiangRealtimeBridge,
    ws: Any,
) -> None:
    """会话级守护：空闲超时/单次时长上限到达时关闭连接。

    每 10 秒巡检一次；到限先给客户端一条可恢复错误，再关闭 WS
    （连接关闭由路由 finally 统一收尾）。
    """
    try:
        while True:
            await asyncio.sleep(10)
            session = bridge.session
            if session is None:
                continue
            idle_limit = settings.ai_realtime_idle_exit_seconds
            total_limit = settings.ai_realtime_session_max_minutes * 60
            elapsed = time.monotonic() - bridge.started_at
            if elapsed >= total_limit:
                logger.info(
                    "realtime_session_time_limit user_id=%s", bridge._user_id  # noqa: SLF001
                )
                break
            if session.idle_seconds >= idle_limit:
                logger.info(
                    "realtime_session_idle_exit user_id=%s", bridge._user_id  # noqa: SLF001
                )
                break
    except asyncio.CancelledError:
        return
    try:
        from app.api.routes.voice_moxiang import _send_json

        await _send_json(
            ws,
            {
                "type": "error",
                "code": "REALTIME_SESSION_LIMIT",
                "message": "本次语音已结束，可重新开始",
            },
        )
        await ws.close(code=1000)
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "RealtimeRouteContext",
    "MoxiangRealtimeBridge",
    "realtime_gate_error",
    "realtime_daily_minutes_used",
    "realtime_watchdog",
    "acquire_session_slot",
    "release_session_slot",
]

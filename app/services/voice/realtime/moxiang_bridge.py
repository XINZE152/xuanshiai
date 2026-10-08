"""墨相师实时语音 v2 的路由侧桥接层。

把 :class:`RealtimeVoiceSession` 需要的业务回调落到现有旅程/画像设施上：
instructions 组装、最终转写提交（现有旅程事务）、助手回复元数据落库、
额度控制与单会话守卫。路由层只负责 WS 消息分派，不接触供应商协议。

一条 WS 连接可以先后承载多轮语音会话。单次时长上限与每日额度都按
会话计，不按连接寿命计；没有进行中的会话时不结算额度。

日志只记时序、字节数、状态和错误码，不记录音频、转写、提示词或密钥。
"""

from __future__ import annotations

import asyncio
import logging
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
from app.services.voice.realtime.context import (
    build_continuous_context,
    build_journey_context,
    load_continuous_history,
    load_master_history,
    send_json,
)
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
# 每日会话时长额度（Redis 原子；限额存储不可用时停止新语音会话）
# ----------------------------------------------------------------------


def _daily_key(user_id: int) -> str:
    day = datetime.now(UTC).strftime("%Y%m%d")
    return f"{_DAILY_QUOTA_KEY_PREFIX}:{user_id}:{day}"


def billable_minutes(elapsed_seconds: float) -> int:
    """会话时长按分钟向上取整。

    不满 1 分钟也记 1 分钟，避免短会话完全不计费；宁可多算，与
    「额度不足停止新会话」的 fail-closed 一致。非正时长记 0。
    """
    if elapsed_seconds <= 0:
        return 0
    return -(-int(elapsed_seconds) // 60)


async def realtime_daily_minutes_used(user_id: int) -> int | None:
    """读取当日已用会话分钟数；Redis 不可用返回 None（fail closed）。"""
    try:
        from app.core.redis import redis_client

        raw = await redis_client.get(_daily_key(user_id))
        return int(raw) if raw is not None else 0
    except Exception:  # noqa: BLE001
        logger.debug("realtime_quota_read_failed", exc_info=True)
        return None


# incrby 与 expire 必须同一次往返：进程若在两步之间退出，key 会没有 TTL
# 并永久累加。2 天 TTL 只是防泄漏兑底，日界由键名里的日期后缀决定。
_CONSUME_MINUTES_LUA = """
local value = redis.call('INCRBY', KEYS[1], ARGV[1])
redis.call('EXPIRE', KEYS[1], ARGV[2])
return value
"""


async def consume_realtime_minutes(user_id: int, minutes: int) -> None:
    """按实际用满分钟数累计当日额度（best-effort，只在会话关闭时结算）。"""
    if minutes <= 0:
        return
    try:
        from app.core.redis import redis_client

        await redis_client.eval(
            _CONSUME_MINUTES_LUA,
            1,
            _daily_key(user_id),
            minutes,
            _DAILY_QUOTA_TTL_SECONDS,
        )
    except Exception:  # noqa: BLE001
        logger.debug("realtime_quota_consume_failed", exc_info=True)


@dataclass
class RealtimeRouteContext:
    """当前业务会话状态（主体切换时由路由更新）。

    ``flow_version`` 决定回复注入哪一份上下文：continuous_v2 用双主体 +
    跨会话，legacy 用单主体建构状态。缺省 legacy 保持既有连接行为不变。
    """

    session_id: str
    subject: str
    narrative_context: str
    flow_version: str = "legacy"


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
        submit_candidate: Callable[[str, str], Awaitable[tuple[str, str]]],
        require_transcript_confirmation: bool = False,
    ) -> None:
        self._ws = ws
        self._user_id = user_id
        self.context = context
        self._poll_tasks = poll_tasks
        self._emit = emit
        self._require_transcript_confirmation = require_transcript_confirmation
        self._transcript_sessions: dict[str, str] = {}
        async def emit_with_context(event: dict[str, Any]) -> None:
            event_type = event.get("type")
            if event_type in {
                "transcript_preview",
                "transcript_confirmed",
                "transcript_cancelled",
                "transcript_expired",
            }:
                transcript_id = str(event.get("transcript_id") or "")
                if event_type == "transcript_preview" and transcript_id:
                    self._transcript_sessions[transcript_id] = self.context.session_id
                event = {
                    **event,
                    "session_id": self.context.session_id,
                    "subject": self.context.subject,
                }
            await emit(event)
        self._emit_with_context = emit_with_context
        self._submit_candidate = submit_candidate
        self.session: RealtimeVoiceSession | None = None

    async def submit_final_transcript(
        self, text: str, client_turn_id: str
    ) -> TurnOutcome:
        """确认后的转写只经统一 journey 提交回调，返回业务轮次结果。"""
        turn_id, task_id = await self._submit_candidate(text, client_turn_id)
        return TurnOutcome(turn_id=str(turn_id or ""), task_id=str(task_id or ""))

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
        if self.context.flow_version == "continuous_v2":
            # 双主体 + 跨内部会话：连续对话恢复不依赖当前 session 是否新建。
            context = await build_continuous_context(
                self._user_id, session_factory=_db_session_factory
            )
            if context is None:
                raise RuntimeError("continuous context unavailable")
            return context
        if not self.context.session_id:
            return ""
        context = await build_journey_context(
            self.context.session_id,
            self.context.subject,
            session_factory=_db_session_factory,
        )
        if context is None:
            raise RuntimeError("journey context unavailable")
        return context

    async def _load_history(self) -> list[dict[str, str]]:
        continuous = self.context.flow_version == "continuous_v2"
        if not continuous and not self.context.session_id:
            return []
        if _db_session_factory is None:
            raise RuntimeError("realtime history unavailable")

        try:
            async with _db_session_factory() as db:
                if continuous:
                    return await load_continuous_history(db, self._user_id)
                return await load_master_history(db, self.context.session_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "realtime_history_load_failed session_id=%s err=%s",
                self.context.session_id,
                type(exc).__name__,
            )
            raise RuntimeError("realtime history unavailable") from exc

    async def handle_confirm_transcript(
        self,
        transcript_id: str,
        text: str,
        client_turn_id: str = "",
        session_id: str = "",
    ) -> bool:
        if self.session is None:
            return False
        if (
            not session_id
            or self._transcript_sessions.get(transcript_id) != session_id
            or session_id != self.context.session_id
        ):
            return False
        return await self.session.handle_confirm_transcript(
            transcript_id, text, client_turn_id
        )

    async def handle_cancel_transcript(
        self,
        transcript_id: str,
        client_turn_id: str = "",
        session_id: str = "",
    ) -> bool:
        if self.session is None:
            return False
        if (
            not session_id
            or self._transcript_sessions.get(transcript_id) != session_id
            or session_id != self.context.session_id
        ):
            return False
        return await self.session.handle_cancel_transcript(
            transcript_id, client_turn_id
        )


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
            emit=self._emit_with_context,
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
            require_transcript_confirmation=self._require_transcript_confirmation,
        )
        ok = await session.start()
        if not ok:
            return None
        self.session = session
        register_session(self._user_id, session)
        return session

    async def close_session(self) -> None:
        """连接收尾：有进行中的会话才结算额度，并释放名额、关闭会话。

        结算按本轮会话时长向上取整，不用连接寿命。没有会话（从未开麦，
        或已切到文字）时不扣额度。
        """
        session = self.session
        self.session = None
        if session is not None:
            await consume_realtime_minutes(
                self._user_id, billable_minutes(session.session_elapsed_seconds)
            )
        release_session_slot(self._user_id)
        if session is not None:
            await session.aclose()


async def realtime_watchdog(
    bridge: MoxiangRealtimeBridge,
    ws: Any,
) -> None:
    """会话级守护：空闲超时或本轮时长上限到达时关闭连接。

    每 10 秒巡检一次。没有进行中的会话时跳过，不因连接已经活了很久
    而误断。到限先给客户端一条可恢复错误，再关闭 WS（连接关闭由路由
    finally 统一收尾）。
    """
    try:
        while True:
            await asyncio.sleep(10)
            session = bridge.session
            if session is None:
                continue
            idle_limit = settings.ai_realtime_idle_exit_seconds
            total_limit = settings.ai_realtime_session_max_minutes * 60
            if session.session_elapsed_seconds >= total_limit:
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
        await send_json(
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

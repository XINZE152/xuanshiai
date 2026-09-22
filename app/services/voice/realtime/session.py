"""实时语音会话控制器（v2）。

职责边界（方案 §2/§3）：
- 身份体系：``generation_id``（一次回复生成+播放过程）、连接代次
  ``epoch``（供应商连接退役/重建时递增）。供应商二进制帧绑定接收它的
  连代次与响应上下文；客户端事件按 ``generation_id`` 归位。
- 正常轮转：voice_ready → 录音 → 供应商 VAD 定稿 → input_closed →
  审核放行门 → 下行（文字+音频）→ response_done → 播放回执 →
  下一轮 voice_ready。四条件齐备才进入下一轮。
- 打断：客户端 ``interrupt`` 立即作废当前世代（停止下行、清空缓冲）、
  发供应商原生 ``cancel``、退役旧上游并重建；被打断回复的文字快照
  持久化并标注 ``interrupted``。打断不制造新的用户发言。
- 审核放行门：最终转写经现有旅程提交（含审核）通过前，供应商下行
  （文字+音频）全部进入有界缓冲；拒绝/超时/失败则作废本轮回复并
  退役上游，绝不放行未经审核的内容。
- 受管理任务：所有异步工作（泵、提交、轮转、持久化）都是本会话
  持有的任务，``aclose`` 时统一取消；上行经有界队列，队满丢帧并计数。

会话不知道数据库与会话 ID：业务经回调注入（路由侧提供自有 DB 会话）。
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.services.ai.profile import AIInputError
from app.services.voice.realtime.provider import (
    RealtimeProviderConfig,
    RealtimeProviderError,
    SenseAudioUpstream,
    VendorAssistantAudioDone,
    VendorAssistantAudioStart,
    VendorAssistantTextDelta,
    VendorAssistantTextDone,
    VendorAudio,
    VendorConnectionClosed,
    VendorError,
    VendorReady,
    VendorSpeechStarted,
    VendorTurnDone,
    VendorUserTranscriptDelta,
    VendorUserTranscriptDone,
)
from app.services.voice.realtime.protocol import (
    CLIENT_EVENT_AUDIO_CHUNK,
    CLIENT_EVENT_AUDIO_OUTPUT_END,
    CLIENT_EVENT_AUDIO_OUTPUT_START,
    CLIENT_EVENT_CANCELLED,
    CLIENT_EVENT_INPUT_CLOSED,
    CLIENT_EVENT_RESPONSE_DONE,
    CLIENT_EVENT_RESPONSE_STATUS,
    CLIENT_EVENT_VOICE_READY,
    DOWNLINK_SAMPLE_RATE,
    PLAYBACK_STATUSES,
    PROTOCOL_VERSION_V2,
    GENERATION_STATUSES,
)

logger = logging.getLogger(__name__)

# 审核放行缓冲上限（事件条数）。供应商下行音频帧约 40ms/帧，800 条
# 远超放行等待上限内可能到达的数量；溢出即作废本轮（fail closed）。
_HOLD_BUFFER_MAX_EVENTS = 800
# 上行有界队列：64 帧 × 10KB ≈ 0.4s 音频，网络短暂抖动吸收；队满丢帧。
_UPLINK_QUEUE_MAX = 64
# 完成宽限：文本与音频都已收齐但供应商迟迟不发 ``turn.done`` 时，按完成
# 收尾的等待秒数（2026-09-12 实测：供应商偶发漏发 turn.done，音频已在
# audio.done 处完整送达；让用户干等 40s 到 fatal 是错误的产品行为）。
_TURN_DONE_GRACE_SECONDS = 10.0


@dataclass(frozen=True)
class TurnOutcome:
    """一次最终转写的业务提交结果。"""

    turn_id: str
    task_id: str


@dataclass
class RealtimeSessionCallbacks:
    """会话与外界的唯一交互面（全部 async）。"""

    # 发送一条 wire 事件给前端（绝不抛异常，由路由层兜底）。
    emit: Callable[[dict[str, Any]], Awaitable[None]]
    # 构造完整 instructions（连接建立时）：人设 + 业务上下文 + 历史数据块。
    build_instructions: Callable[[], Awaitable[str]]
    # 构造增量 instructions（正常轮转前，业务上下文变化时）：不含历史。
    build_update_instructions: Callable[[], Awaitable[str]]
    # 提交最终转写：现有旅程落库+审核+入队；失败抛 AIInputError/Exception。
    submit_final_transcript: Callable[[str, str], Awaitable[TurnOutcome]]
    # 持久化助手回复快照（结束或打断时一次）；返回 assistant turn_id（可空）。
    persist_reply: Callable[[str, dict[str, Any]], Awaitable[str]]
    # 播放状态变化时刷新已有助手行的元数据。
    update_reply_metadata: Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass
class ActiveResponse:
    """一个活跃回复世代的状态。"""

    generation_id: str
    client_turn_id: str
    status: str = "streaming"  # GENERATION_STATUSES
    playback_status: str = "not_started"  # PLAYBACK_STATUSES
    played_ms: int = 0
    response_id: str = ""
    seq: int = 0
    text_parts: list[str] = field(default_factory=list)
    full_text: str = ""
    gate_open: bool = False
    hold_buffer: list[dict[str, Any]] = field(default_factory=list)
    user_turn_id: str = ""
    task_id: str = ""
    submission_task: asyncio.Task[None] | None = None
    vendor_text_done: bool = False
    vendor_audio_done: bool = False
    vendor_turn_done: bool = False
    finalized: bool = False
    persisted: bool = False
    reply_turn_id: str = ""

    def metadata(self, protocol_version: int = PROTOCOL_VERSION_V2) -> dict[str, Any]:
        return {
            "protocol": f"v{protocol_version}",
            "generation_id": self.generation_id,
            "client_turn_id": self.client_turn_id,
            "user_turn_id": self.user_turn_id,
            "response_id": self.response_id,
            "generation_status": self.status,
            "playback_status": self.playback_status,
            "played_ms": self.played_ms,
        }


class RealtimeVoiceSession:
    """一条业务 WS 连接内的实时语音会话（生命周期与连接一致）。"""

    def __init__(
        self,
        *,
        provider_config: RealtimeProviderConfig,
        callbacks: RealtimeSessionCallbacks,
        upstream_factory: Callable[[int], SenseAudioUpstream] | None = None,
        submission_hold_seconds: float = 15.0,
    ) -> None:
        self._provider_config = provider_config
        self._callbacks = callbacks
        self._submission_hold_seconds = max(1.0, submission_hold_seconds)
        self._upstream_factory = upstream_factory

        self._upstream: SenseAudioUpstream | None = None
        self._epoch = 0
        self._pump_tasks: set[asyncio.Task[None]] = set()
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._uplink_queue: asyncio.Queue[bytes] = asyncio.Queue(
            maxsize=_UPLINK_QUEUE_MAX
        )
        self._uplink_task: asyncio.Task[None] | None = None

        self._active: ActiveResponse | None = None
        self._awaiting_final = False  # speech.started 后置位，定稿后清位
        self._pending_client_turn_id = ""
        self._input_open = False
        self._closed = False
        self._rotating = False
        self._context_dirty = False
        self._last_update_instructions = ""
        self._late_audio_frames = 0
        self._dropped_uplink_frames = 0
        self._last_activity_ts = time.monotonic()

    # ------------------------------------------------------------------
    # 对路由层暴露的事件入口（客户端消息驱动）
    # ------------------------------------------------------------------

    async def start(self) -> bool:
        """建立首个上游并进入待录状态。返回是否就绪。"""
        return await self._activate_upstream(initial=True)

    async def handle_audio_chunk(self, pcm: bytes) -> None:
        """转发一帧客户端录音（输入窗口关闭时静默丢弃并计数）。"""
        self._last_activity_ts = time.monotonic()
        if self._closed or not self._input_open or self._upstream is None:
            self._late_audio_frames += 1
            return
        try:
            self._uplink_queue.put_nowait(pcm)
        except asyncio.QueueFull:
            self._dropped_uplink_frames += 1
            if self._dropped_uplink_frames % 50 == 1:
                logger.warning(
                    "realtime_uplink_overflow dropped=%s generation=%s",
                    self._dropped_uplink_frames,
                    self._active.generation_id if self._active else "-",
                )

    async def handle_voice_input_begin(self, client_turn_id: str) -> None:
        """客户端真实开麦；绑定本轮发言的幂等身份。"""
        if self._closed or not self._input_open:
            return
        self._pending_client_turn_id = client_turn_id or uuid.uuid4().hex

    async def handle_audio_end(self) -> None:
        """v2 下 ``audio_end`` 是 no-op：说话结束由供应商 VAD 判定。

        保留事件以兼容旧客户端时序，不据此关闭输入窗口。
        """

    async def handle_interrupt(self, generation_id: str = "") -> None:
        """客户端点击打断：立即作废当前世代并轮转上游。"""
        if self._closed:
            return
        self._last_activity_ts = time.monotonic()
        resp = self._active
        if resp is None or (
            generation_id and generation_id != resp.generation_id
        ):
            return  # 无活跃回复或世代不符（迟到打断）——忽略
        if resp.status == "streaming":
            resp.status = "interrupted"
            resp.playback_status = "interrupted"
        # 先关放行门并清空缓冲（停止下行），再通知供应商与客户端。
        resp.hold_buffer.clear()
        resp.gate_open = False
        await self._emit(
            build_cancelled_event(resp.generation_id)
        )
        await self._emit(
            build_response_done_event(resp.generation_id, "interrupted")
        )
        await self._persist_if_needed(resp)
        self._active = None
        upstream = self._upstream
        if upstream is not None:
            await upstream.send_cancel()
        # 退役旧上游并重建（打断必换连接，方案 §2）。
        self._spawn(self._rotate_upstream(reason="interrupt"))

    async def handle_playback_progress(
        self, generation_id: str, played_ms: int
    ) -> None:
        resp = self._active
        if resp is None or generation_id != resp.generation_id:
            return
        resp.played_ms = max(0, int(played_ms))
        if resp.playback_status not in {"playing", "completed"}:
            resp.playback_status = "playing"
            await self._sync_response_status(resp)

    async def handle_playback_finished(
        self, generation_id: str, status: str, played_ms: int = 0
    ) -> None:
        resp = self._active
        if resp is None or generation_id != resp.generation_id:
            return
        normalized = status if status in PLAYBACK_STATUSES else "unknown"
        resp.playback_status = normalized
        resp.played_ms = max(resp.played_ms, int(played_ms or 0))
        await self._sync_response_status(resp)
        if resp.status == "completed" and normalized == "completed":
            # 生成与本地播放都完成 → 进入下一轮录音。
            self._active = None
            self._spawn(self._enter_listening())

    async def mark_context_dirty(self) -> None:
        """业务上下文变化（主体切换/建构状态变化），下一轮前增量更新。"""
        self._context_dirty = True

    async def stop_playback_only(self) -> None:
        """页面切走/切文字：取消播放与回复，但不轮转上游（随后整体关闭）。"""
        resp = self._active
        if resp is None:
            return
        if resp.status == "streaming":
            resp.status = "interrupted"
            resp.playback_status = "interrupted"
        resp.hold_buffer.clear()
        resp.gate_open = False
        upstream = self._upstream
        if upstream is not None:
            await upstream.send_cancel()

    async def aclose(self) -> None:
        """连接收尾：作废在途回复、标注 unknown、取消全部受管理任务。"""
        if self._closed:
            return
        self._closed = True
        self._input_open = False
        resp = self._active
        if resp is not None:
            if resp.playback_status in {"not_started", "playing"}:
                resp.playback_status = "unknown"
            resp.hold_buffer.clear()
            resp.gate_open = False
            await self._persist_if_needed(resp)
            self._active = None
        uplink = self._uplink_task
        if uplink is not None:
            uplink.cancel()
            self._uplink_task = None
        for task in tuple(self._pump_tasks) + tuple(self._background_tasks):
            task.cancel()
        self._pump_tasks.clear()
        self._background_tasks.clear()
        upstream = self._upstream
        self._upstream = None
        if upstream is not None:
            await upstream.aclose()

    # ------------------------------------------------------------------
    # 观测
    # ------------------------------------------------------------------

    @property
    def idle_seconds(self) -> float:
        return time.monotonic() - self._last_activity_ts

    @property
    def input_open(self) -> bool:
        return self._input_open

    @property
    def late_audio_frames(self) -> int:
        """输入窗口关闭后到达的迟到录音帧计数（测试/观测用）。"""
        return self._late_audio_frames

    @property
    def active_generation_id(self) -> str:
        return self._active.generation_id if self._active else ""

    # ------------------------------------------------------------------
    # 供应商事件处理（单个泵任务内顺序执行，保证下行顺序）
    # ------------------------------------------------------------------

    async def _handle_vendor_event(self, event: Any, epoch: int) -> None:
        self._last_activity_ts = time.monotonic()
        if isinstance(event, VendorReady):
            return
        if isinstance(event, VendorSpeechStarted):
            self._awaiting_final = True
            return
        if isinstance(event, VendorUserTranscriptDelta):
            await self._emit({"type": "partial_transcript", "text": event.text})
            return
        if isinstance(event, VendorUserTranscriptDone):
            await self._on_final_transcript(event.text)
            return
        if isinstance(event, VendorAssistantTextDelta):
            resp = self._active
            if resp is not None and resp.status == "streaming":
                resp.text_parts.append(event.text)
                await self._emit_downstream(
                    resp, {"type": "ai_content", "text": event.text}
                )
            return
        if isinstance(event, VendorAssistantTextDone):
            resp = self._active
            if resp is not None and resp.status == "streaming":
                resp.vendor_text_done = True
                resp.full_text = event.text
                await self._emit_downstream(
                    resp,
                    {"type": "ai_reply", "text": event.text, "generation_id": resp.generation_id},
                )
                await self._maybe_finalize(resp)
                if (
                    not resp.finalized
                    and resp.vendor_audio_done
                    and not resp.vendor_turn_done
                ):
                    # 音频先于文本完成时的对称宽限调度。
                    self._spawn(self._grace_finalize(resp))
            return
        if isinstance(event, VendorAssistantAudioStart):
            resp = self._active
            if resp is not None and resp.status == "streaming":
                resp.response_id = event.response_id
                resp.seq = 0
                await self._emit_downstream(
                    resp,
                    build_audio_output_start_event(
                        generation_id=resp.generation_id,
                        response_id=event.response_id,
                        sample_rate=event.sample_rate or DOWNLINK_SAMPLE_RATE,
                    ),
                )
            return
        if isinstance(event, VendorAudio):
            resp = self._active
            if resp is not None and resp.status == "streaming":
                resp.seq += 1
                await self._emit_downstream(
                    resp,
                    build_audio_chunk_event(
                        generation_id=resp.generation_id,
                        seq=resp.seq,
                        response_id=event.response_id,
                        pcm=event.pcm,
                    ),
                )
            return
        if isinstance(event, VendorAssistantAudioDone):
            resp = self._active
            if resp is not None and resp.status == "streaming":
                resp.vendor_audio_done = True
                await self._emit_downstream(
                    resp,
                    build_audio_output_end_event(
                        generation_id=resp.generation_id,
                        response_id=event.response_id,
                    ),
                )
                await self._maybe_finalize(resp)
                if (
                    not resp.finalized
                    and resp.vendor_text_done
                    and not resp.vendor_turn_done
                ):
                    # turn.done 迟到/漏发的完成宽限计时器（见常量注释）。
                    self._spawn(self._grace_finalize(resp))
            return
        if isinstance(event, VendorTurnDone):
            resp = self._active
            if resp is not None and resp.status == "streaming":
                resp.vendor_turn_done = True
                await self._maybe_finalize(resp)
            return
        if isinstance(event, VendorError):
            await self._on_vendor_error(event, epoch)
            return
        if isinstance(event, VendorConnectionClosed):
            await self._on_upstream_closed(epoch)
            return

    async def _on_final_transcript(self, text: str) -> None:
        """供应商定稿：关闭输入窗口，开启审核放行门，提交业务落库。

        只有先出现 ``speech.started`` 的定稿才是新发言；同一最终事件的
        重复投递（无新的 speech.started）直接丢弃，保证同一转写只保存
        和分析一次。两次真实发言各自带 speech.started，即使文本相同也
        是不同发言。
        """
        if not self._awaiting_final:
            logger.debug("realtime_duplicate_final_dropped")
            return
        self._awaiting_final = False
        if self._active is not None:
            # 未终态的旧回复仍在（turn.done 迟到等）：先作废旧回复再开新轮，
            # 保证一轮只生成一份口头回复。
            old = self._active
            old.status = "interrupted"
            old.hold_buffer.clear()
            old.gate_open = False
            self._active = None
            await self._emit(
                build_response_done_event(old.generation_id, "interrupted")
            )
            await self._persist_if_needed(old)
            logger.warning(
                "realtime_overlapping_final generation=%s", old.generation_id
            )
        clean_text = text.strip()
        if not clean_text:
            # 空定稿（纯噪声）：重新进入录音，不计发言。
            self._spawn(self._enter_listening())
            return
        await self._emit({"type": "final_transcript", "text": clean_text})
        client_turn_id = self._pending_client_turn_id or uuid.uuid4().hex
        self._pending_client_turn_id = ""
        resp = ActiveResponse(
            generation_id=uuid.uuid4().hex,
            client_turn_id=client_turn_id,
        )
        self._active = resp
        self._input_open = False
        await self._emit(build_input_closed_event())
        resp.submission_task = self._spawn(
            self._run_submission(resp, clean_text)
        )

    async def _run_submission(self, resp: ActiveResponse, text: str) -> None:
        """提交最终转写（落库+审核+入队）；决定放行门的开关。"""
        try:
            outcome = await self._callbacks.submit_final_transcript(
                text, resp.client_turn_id
            )
        except AIInputError as exc:
            # 审核拒绝：用户发言未落库，也不得残留 assistant 快照行。
            await self._fail_response(
                resp,
                code="AI_INPUT_INVALID",
                message=str(exc) or "回答内容包含违规信息,请修改后重试",
                rotate=True,
                persist_snapshot=False,
            )
            return
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception(
                "realtime_submit_failed generation=%s", resp.generation_id
            )
            # 提交失败时用户发言未确认保存，不生成无主 assistant 行；
            # client_turn_id 已保留，重试经幂等键去重。
            await self._fail_response(
                resp,
                code="AI_TEMPORARILY_UNAVAILABLE",
                message="本轮发言暂时无法保存，请重试",
                rotate=False,
                persist_snapshot=False,
            )
            return
        resp.user_turn_id = outcome.turn_id
        resp.task_id = outcome.task_id
        if resp.status == "streaming" and not resp.gate_open:
            resp.gate_open = True
            await self._flush_hold_buffer(resp)
        # 打断发生在提交期间：门保持关闭、缓冲已清空；回复快照由
        # _persist_if_needed 在打断路径中落库，无需在此放行。

    async def _flush_hold_buffer(self, resp: ActiveResponse) -> None:
        buffered = resp.hold_buffer
        resp.hold_buffer = []
        for event in buffered:
            await self._emit(event)

    async def _emit_downstream(
        self, resp: ActiveResponse, event: dict[str, Any]
    ) -> None:
        """下行事件出口：放行门关闭时进有界缓冲，作废世代直接丢弃。"""
        if resp.status != "streaming":
            return
        if resp.gate_open:
            await self._emit(event)
            return
        if len(resp.hold_buffer) >= _HOLD_BUFFER_MAX_EVENTS:
            logger.warning(
                "realtime_hold_overflow generation=%s", resp.generation_id
            )
            self._spawn(
                self._fail_response(
                    resp,
                    code="AI_TEMPORARILY_UNAVAILABLE",
                    message="回复缓冲超限，本轮已终止",
                    rotate=True,
                )
            )
            return
        resp.hold_buffer.append(event)

    async def _grace_finalize(self, resp: ActiveResponse) -> None:
        """turn.done 漏发时的完成宽限收尾（内容已完整送达）。"""
        await asyncio.sleep(_TURN_DONE_GRACE_SECONDS)
        if (
            self._closed
            or self._active is not resp
            or resp.finalized
            or resp.status != "streaming"
            or not (resp.vendor_text_done and resp.vendor_audio_done)
        ):
            return
        logger.info(
            "realtime_turn_done_grace_finalized generation=%s",
            resp.generation_id,
        )
        await self._finalize_completed(resp)

    async def _maybe_finalize(self, resp: ActiveResponse) -> None:
        """turn.done + 文本完成 + 音频完成三者齐备才终态化。"""
        if resp.finalized or resp.status != "streaming":
            return
        if not (
            resp.vendor_turn_done
            and resp.vendor_text_done
            and resp.vendor_audio_done
        ):
            return
        await self._finalize_completed(resp)

    async def _finalize_completed(self, resp: ActiveResponse) -> None:
        """按 completed 收尾：发 response_done、落快照、等播放回执。"""
        resp.finalized = True
        # 放行门未开（审核慢）：等提交结果决定放行或作废，绝不越门。
        if not resp.gate_open and resp.submission_task is not None:
            try:
                await asyncio.wait_for(
                    asyncio.shield(resp.submission_task),
                    timeout=self._submission_hold_seconds,
                )
            except asyncio.TimeoutError:
                await self._fail_response(
                    resp,
                    code="AI_TEMPORARILY_UNAVAILABLE",
                    message="输入确认超时，本轮已终止",
                    rotate=True,
                )
                return
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 提交任务内部已失败并终态化
                return
            if resp.status != "streaming" or not resp.gate_open:
                return
        resp.status = "completed"
        await self._emit(
            build_response_done_event(resp.generation_id, "completed")
        )
        await self._persist_if_needed(resp)
        await self._sync_response_status(resp)
        # 播放完成回执到达后才进入下一轮（handle_playback_finished）。

    async def _fail_response(
        self,
        resp: ActiveResponse,
        *,
        code: str,
        message: str,
        rotate: bool,
        persist_snapshot: bool = True,
    ) -> None:
        """作废回复：审核拒绝/提交失败/放行超时/缓冲超限。"""
        if resp.finalized and resp.status == "completed":
            return
        resp.finalized = True
        resp.status = "failed"
        resp.hold_buffer.clear()
        resp.gate_open = False
        upstream = self._upstream
        if upstream is not None:
            await upstream.send_cancel()
        await self._emit(
            build_response_done_event(resp.generation_id, "failed")
        )
        await self._emit({"type": "error", "code": code, "message": message})
        if persist_snapshot:
            await self._persist_if_needed(resp)
        if self._active is resp:
            self._active = None
        if rotate:
            self._spawn(self._rotate_upstream(reason=f"fail:{code}"))
        else:
            self._spawn(self._enter_listening())

    async def _on_vendor_error(self, event: VendorError, epoch: int) -> None:
        if self._epoch != epoch:
            return
        resp = self._active
        if event.fatal:
            logger.warning(
                "realtime_vendor_fatal_error code=%s epoch=%s",
                event.code,
                epoch,
            )
            if resp is not None and resp.status == "streaming":
                await self._fail_response(
                    resp,
                    code="AI_TEMPORARILY_UNAVAILABLE",
                    message="实时语音暂时不可用",
                    rotate=True,
                )
            else:
                self._spawn(self._rotate_upstream(reason=f"vendor:{event.code}"))
            return
        # 非 fatal（empty_audio_buffer / no_active_response 等）：记录即可。
        logger.info(
            "realtime_vendor_error code=%s epoch=%s", event.code, epoch
        )

    async def _on_upstream_closed(self, epoch: int) -> None:
        if self._closed or self._epoch != epoch:
            return
        logger.warning("realtime_upstream_closed epoch=%s", epoch)
        resp = self._active
        if resp is not None and resp.status == "streaming":
            await self._fail_response(
                resp,
                code="AI_TEMPORARILY_UNAVAILABLE",
                message="实时语音连接中断",
                rotate=True,
            )
        else:
            self._spawn(self._rotate_upstream(reason="closed"))

    # ------------------------------------------------------------------
    # 上游生命周期
    # ------------------------------------------------------------------

    def _new_upstream(self, epoch: int) -> SenseAudioUpstream:
        if self._upstream_factory is not None:
            return self._upstream_factory(epoch)
        return SenseAudioUpstream(
            self._provider_config, epoch=epoch
        )

    async def _activate_upstream(self, *, initial: bool) -> bool:
        """建立上游连接；成功即进入待录状态。"""
        if self._closed:
            return False
        epoch = self._epoch + 1
        self._epoch = epoch
        try:
            instructions = await self._callbacks.build_instructions()
        except Exception:  # noqa: BLE001
            logger.exception("realtime_build_instructions_failed")
            await self._emit(
                {
                    "type": "error",
                    "code": "AI_TEMPORARILY_UNAVAILABLE",
                    "message": "实时语音准备失败",
                }
            )
            return False
        upstream = self._new_upstream(epoch)
        try:
            await upstream.start(instructions)
        except RealtimeProviderError as exc:
            logger.warning(
                "realtime_upstream_start_failed epoch=%s code=%s",
                epoch,
                exc.code,
            )
            await self._emit(
                {
                    "type": "error",
                    "code": "AI_TEMPORARILY_UNAVAILABLE",
                    "message": "实时语音连接失败，请稍后重试",
                }
            )
            return False
        self._upstream = upstream
        self._last_update_instructions = instructions
        self._context_dirty = False
        self._uplink_queue = asyncio.Queue(maxsize=_UPLINK_QUEUE_MAX)
        pump_task = asyncio.create_task(
            self._pump_upstream(upstream),
            name=f"realtime-pump-epoch-{epoch}",
        )
        self._pump_tasks.add(pump_task)
        pump_task.add_done_callback(self._pump_tasks.discard)
        self._start_uplink_task()
        self._input_open = True
        self._awaiting_final = False
        # 无论首建还是轮转，就绪即广播 voice_ready：客户端只在收到它之后开麦。
        await self._emit(build_voice_ready_event())
        return True

    async def _rotate_upstream(self, *, reason: str) -> None:
        """打断/失败后重建上游（single-flight）。"""
        if self._closed or self._rotating:
            return
        self._rotating = True
        try:
            old = self._upstream
            self._upstream = None
            self._input_open = False
            self._uplink_task_cancel()
            if old is not None:
                await old.aclose()
            ready = await self._activate_upstream(initial=False)
            logger.info(
                "realtime_upstream_rotated reason=%s ready=%s", reason, ready
            )
        finally:
            self._rotating = False

    async def _enter_listening(self) -> None:
        """正常轮转收尾：业务上下文变化则轮转上游，然后广播 voice_ready。

        实测（2026-09-12 P0 探针）供应商对 instructions-only 的 ``update``
        返回 ``invalid_tts_speed`` 并整条忽略；上下文正确性优先，变化时
        直接重建连接（携带完整 instructions），重建代价约一次建连耗时。
        """
        if self._closed or self._input_open:
            return
        upstream = self._upstream
        if upstream is None:
            await self._rotate_upstream(reason="enter-listening-no-upstream")
            return
        if self._context_dirty:
            await self._rotate_upstream(reason="context-dirty")
            return
        self._input_open = True
        self._awaiting_final = False
        await self._emit(build_voice_ready_event())

    # ------------------------------------------------------------------
    # 上行泵与任务管理
    # ------------------------------------------------------------------

    def _start_uplink_task(self) -> None:
        self._uplink_task_cancel()
        self._uplink_queue = asyncio.Queue(maxsize=_UPLINK_QUEUE_MAX)
        task = asyncio.create_task(
            self._uplink_loop(), name=f"realtime-uplink-epoch-{self._epoch}"
        )
        self._uplink_task = task

    def _uplink_task_cancel(self) -> None:
        if self._uplink_task is not None:
            self._uplink_task.cancel()
            self._uplink_task = None

    async def _uplink_loop(self) -> None:
        """把有界队列里的录音帧写入当前上游（网络背压不阻塞路由循环）。"""
        while not self._closed:
            pcm = await self._uplink_queue.get()
            upstream = self._upstream
            if upstream is None or self._closed:
                continue
            try:
                await upstream.send_audio(pcm)
            except RealtimeProviderError as exc:
                logger.warning(
                    "realtime_uplink_send_failed epoch=%s code=%s",
                    self._epoch,
                    exc.code,
                )
                # 上行断裂视为上游故障，作废在途回复并轮转。
                resp = self._active
                if resp is not None and resp.status == "streaming":
                    self._spawn(
                        self._fail_response(
                            resp,
                            code="AI_TEMPORARILY_UNAVAILABLE",
                            message="实时语音连接中断",
                            rotate=True,
                        )
                    )
                else:
                    self._spawn(self._rotate_upstream(reason="uplink-failed"))

    def _spawn(self, coro: Any) -> asyncio.Task[None]:
        """创建受管理任务：aclose 时统一取消。"""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def _pump_upstream(self, upstream: SenseAudioUpstream) -> None:
        epoch = upstream.epoch
        try:
            async for event in upstream.events():
                if self._closed or self._epoch != epoch:
                    break
                await self._handle_vendor_event(event, epoch)
        except asyncio.CancelledError:
            raise
        except RealtimeProviderError as exc:
            logger.warning(
                "realtime_pump_event_timeout epoch=%s code=%s", epoch, exc.code
            )
            await self._on_upstream_closed(epoch)

    # ------------------------------------------------------------------
    # 持久化与状态同步
    # ------------------------------------------------------------------

    async def _persist_if_needed(self, resp: ActiveResponse) -> None:
        """结束或打断时保存回复文字快照（一次）；无文字不落空行。"""
        if resp.persisted:
            return
        text = (resp.full_text or "".join(resp.text_parts)).strip()
        if not text:
            return
        resp.persisted = True
        try:
            resp.reply_turn_id = str(
                await self._callbacks.persist_reply(text, resp.metadata())
                or ""
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "realtime_persist_reply_failed generation=%s", resp.generation_id
            )

    async def _sync_response_status(self, resp: ActiveResponse) -> None:
        await self._emit(
            build_response_status_event(
                generation_id=resp.generation_id,
                generation_status=resp.status,
                playback_status=resp.playback_status,
            )
        )
        if resp.reply_turn_id and resp.persisted:
            try:
                await self._callbacks.update_reply_metadata(
                    resp.reply_turn_id, resp.metadata()
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "realtime_update_metadata_failed generation=%s",
                    resp.generation_id,
                )

    async def _emit(self, event: dict[str, Any]) -> None:
        if self._closed:
            return
        try:
            await self._callbacks.emit(event)
        except Exception:  # noqa: BLE001  # emit 契约：绝不抛
            logger.debug("realtime_emit_failed", exc_info=True)


# ----------------------------------------------------------------------
# v2 wire 事件构造（保持与 protocol.py 常量一致）
# ----------------------------------------------------------------------


def build_voice_ready_event() -> dict[str, Any]:
    return {"type": CLIENT_EVENT_VOICE_READY}


def build_input_closed_event() -> dict[str, Any]:
    return {"type": CLIENT_EVENT_INPUT_CLOSED}


def build_audio_output_start_event(
    *, generation_id: str, response_id: str, sample_rate: int
) -> dict[str, Any]:
    return {
        "type": CLIENT_EVENT_AUDIO_OUTPUT_START,
        "generation_id": generation_id,
        "response_id": response_id,
        "sample_rate": sample_rate,
        "format": "pcm_s16le",
    }


def build_audio_chunk_event(
    *, generation_id: str, seq: int, response_id: str, pcm: bytes
) -> dict[str, Any]:
    data = base64.b64encode(pcm).decode("ascii")
    return {
        "type": CLIENT_EVENT_AUDIO_CHUNK,
        "generation_id": generation_id,
        "response_id": response_id,
        "seq": seq,
        "data": data,
    }


def build_audio_output_end_event(
    *, generation_id: str, response_id: str
) -> dict[str, Any]:
    return {
        "type": CLIENT_EVENT_AUDIO_OUTPUT_END,
        "generation_id": generation_id,
        "response_id": response_id,
    }


def build_response_done_event(
    generation_id: str, status: str
) -> dict[str, Any]:
    status_normalized = status if status in GENERATION_STATUSES else "failed"
    return {
        "type": CLIENT_EVENT_RESPONSE_DONE,
        "generation_id": generation_id,
        "status": status_normalized,
    }


def build_cancelled_event(generation_id: str) -> dict[str, Any]:
    return {
        "type": CLIENT_EVENT_CANCELLED,
        "generation_id": generation_id,
    }


def build_response_status_event(
    *,
    generation_id: str,
    generation_status: str,
    playback_status: str,
) -> dict[str, Any]:
    return {
        "type": CLIENT_EVENT_RESPONSE_STATUS,
        "generation_id": generation_id,
        "generation_status": generation_status,
        "playback_status": playback_status,
    }


__all__ = [
    "RealtimeSessionCallbacks",
    "RealtimeVoiceSession",
    "TurnOutcome",
]

"""SenseAudio 实时语音上游适配（一条连接 = 一个连接代次）。

边界（方案 §2/§3）：
- 连接后发送一次 ``start``，等待 ``ready`` 才允许上行音频。
- 供应商二进制 PCM 帧绑定到**接收它的这条连接**与当前响应上下文
  （最近一次 ``assistant.audio.start``），绝不到达时读取"最新世代"
  重新编号——世代/连接代次由 :mod:`session` 管理，适配层只忠实标注。
- 用户转写增量是"最新值替换"语义，助手文本增量是"追加"语义；
  两者在此保持原样，由上层区分处理。
- 打断用供应商原生 ``cancel``；被打断后调用方应退役整条连接
  （新建 upstream），本类不做自动重连。
- 凭据只经构造参数注入；日志只记时序/字节数/状态/错误码，
  不记音频、转写、提示词或密钥。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable

from app.services.voice.realtime.protocol import (
    VENDOR_TYPE_ASSISTANT_AUDIO_DONE,
    VENDOR_TYPE_ASSISTANT_AUDIO_START,
    VENDOR_TYPE_ASSISTANT_TEXT_DELTA,
    VENDOR_TYPE_ASSISTANT_TEXT_DONE,
    VENDOR_TYPE_ERROR,
    VENDOR_TYPE_READY,
    VENDOR_TYPE_SPEECH_STARTED,
    VENDOR_TYPE_TURN_DONE,
    VENDOR_TYPE_UPDATE,
    VENDOR_TYPE_USER_TRANSCRIPT_DELTA,
    VENDOR_TYPE_USER_TRANSCRIPT_DONE,
    VENDOR_CANCEL_EVENT,
    VENDOR_END_EVENT,
    build_vendor_start_event,
    build_vendor_update_event,
    is_fatal_vendor_error,
)

logger = logging.getLogger(__name__)

# 事件队列上限：供应商事件速率有限（音频走二进制帧不占本队列），
# 512 足以吸收突发文本/控制事件；溢出视为上游异常并退役连接。
_VENDOR_EVENT_QUEUE_MAX = 512


class RealtimeProviderError(Exception):
    """实时供应商适配错误。``code`` 与供应商错误码/本地错误分类对齐。"""

    def __init__(self, code: str, message: str, *, fatal: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.fatal = fatal


@dataclass(frozen=True)
class RealtimeProviderConfig:
    """供应商连接参数。``api_key`` 仅内存传递，不进日志。"""

    ws_url: str
    api_key: str
    model: str
    voice: str = ""
    connect_timeout: float = 10.0
    ready_timeout: float = 15.0


# ----------------------------------------------------------------------
# 供应商事件（tagged union，frozen dataclass）
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class VendorReady:
    session_id: str


@dataclass(frozen=True)
class VendorSpeechStarted:
    pass


@dataclass(frozen=True)
class VendorUserTranscriptDelta:
    text: str  # 最新值替换语义


@dataclass(frozen=True)
class VendorUserTranscriptDone:
    text: str


@dataclass(frozen=True)
class VendorAssistantTextDelta:
    text: str  # 追加语义


@dataclass(frozen=True)
class VendorAssistantTextDone:
    text: str


@dataclass(frozen=True)
class VendorAssistantAudioStart:
    response_id: str
    sample_rate: int


@dataclass(frozen=True)
class VendorAudio:
    response_id: str
    pcm: bytes


@dataclass(frozen=True)
class VendorAssistantAudioDone:
    response_id: str


@dataclass(frozen=True)
class VendorTurnDone:
    pass


@dataclass(frozen=True)
class VendorError:
    code: str
    message: str
    fatal: bool


@dataclass(frozen=True)
class VendorConnectionClosed:
    code: int
    reason: str


VendorEvent = (
    VendorReady
    | VendorSpeechStarted
    | VendorUserTranscriptDelta
    | VendorUserTranscriptDone
    | VendorAssistantTextDelta
    | VendorAssistantTextDone
    | VendorAssistantAudioStart
    | VendorAudio
    | VendorAssistantAudioDone
    | VendorTurnDone
    | VendorError
    | VendorConnectionClosed
)


# ----------------------------------------------------------------------
# 传输抽象（测试注入 fake，用与不用的默认实现都走同一条代码路径）
# ----------------------------------------------------------------------


class RealtimeTransport:
    """WS 传输薄封装：send text/bytes、按帧接收、关闭。"""

    async def send_text(self, payload: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    async def send_bytes(self, payload: bytes) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    async def recv(self) -> str | bytes | None:  # pragma: no cover - interface
        """One frame; ``None`` 表示连接已正常关闭（EOF）。"""
        raise NotImplementedError

    async def aclose(self, code: int = 1000) -> None:  # pragma: no cover - interface
        raise NotImplementedError


def _default_transport_factory(
    config: RealtimeProviderConfig,
) -> Callable[[], Awaitable[RealtimeTransport]]:
    """默认传输工厂：``websockets`` 库 + Bearer 鉴权头。"""

    async def factory() -> RealtimeTransport:
        import websockets  # 延迟导入，避免无关路径强依赖

        conn = await asyncio.wait_for(
            websockets.connect(
                config.ws_url,
                additional_headers={"Authorization": f"Bearer {config.api_key}"},
                max_size=2 * 1024 * 1024,
            ),
            timeout=config.connect_timeout,
        )
        return _WebsocketsTransport(conn)

    return factory


class _WebsocketsTransport(RealtimeTransport):
    """``websockets`` >= 12 (asyncio client) 适配。"""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def send_text(self, payload: str) -> None:
        await self._conn.send(payload)

    async def send_bytes(self, payload: bytes) -> None:
        await self._conn.send(payload)

    async def recv(self) -> str | bytes | None:
        try:
            frame = await self._conn.recv()
        except Exception:  # noqa: BLE001 - 关闭/异常一律视为 EOF，由上层分类
            return None
        if isinstance(frame, (str, bytes)):
            return frame
        return None

    async def aclose(self, code: int = 1000) -> None:
        try:
            await self._conn.close(code=code)
        except Exception:  # noqa: BLE001
            pass


TransportFactory = Callable[[], Awaitable[RealtimeTransport]]


# ----------------------------------------------------------------------
# 上游连接
# ----------------------------------------------------------------------


class SenseAudioUpstream:
    """一条供应商实时连接。

    生命周期：``start(instructions)`` 成功后 → ``send_audio`` /
    ``send_update`` / ``send_cancel`` → ``aclose``。事件经
    :meth:`events` 异步迭代产出；连接结束（EOF/致命错误）后迭代终止。
    """

    def __init__(
        self,
        config: RealtimeProviderConfig,
        *,
        transport_factory: TransportFactory | None = None,
        epoch: int = 0,
    ) -> None:
        self.config = config
        self.epoch = epoch
        self._transport_factory = transport_factory
        self._transport: RealtimeTransport | None = None
        self._queue: asyncio.Queue[VendorEvent] = asyncio.Queue(
            maxsize=_VENDOR_EVENT_QUEUE_MAX
        )
        self._pump_task: asyncio.Task[None] | None = None
        # 当前响应上下文：二进制帧归属最近一次 assistant.audio.start 的
        # response_id；audio.done 后清空。每条连接独立。
        self._current_response_id: str = ""
        self._current_response_sample_rate: int = 0
        self._closed = False
        self.bytes_sent = 0  # 观测用，仅计数字节

    # -- 生命周期 ------------------------------------------------------

    async def start(self, instructions: str) -> None:
        """连接 → 发送 start → 等待 ready。失败抛 :class:`RealtimeProviderError`。"""
        factory = self._transport_factory or _default_transport_factory(self.config)
        try:
            # connect_timeout 对默认与注入的传输工厂统一生效。
            self._transport = await asyncio.wait_for(
                factory(), timeout=self.config.connect_timeout
            )
        except asyncio.TimeoutError as exc:
            raise RealtimeProviderError(
                "PROVIDER_CONNECT_TIMEOUT", "实时供应商连接超时"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise RealtimeProviderError(
                "PROVIDER_CONNECT_FAILED",
                "实时供应商连接失败",
            ) from exc
        await self._send_json(
            build_vendor_start_event(
                model=self.config.model,
                voice=self.config.voice,
                instructions=instructions,
            )
        )
        self._pump_task = asyncio.create_task(
            self._pump(), name=f"senseaudio-pump-epoch-{self.epoch}"
        )
        # 等待 ready；期间出现的致命 error 直接转译为异常。
        while True:
            event = await self._next_event(self.config.ready_timeout)
            if event is None:
                await self.aclose()
                raise RealtimeProviderError(
                    "PROVIDER_READY_TIMEOUT", "实时供应商就绪超时"
                )
            if isinstance(event, VendorReady):
                logger.info(
                    "senseaudio_ready epoch=%s session_id=%s",
                    self.epoch,
                    event.session_id,
                )
                return
            if isinstance(event, VendorError):
                await self.aclose()
                raise RealtimeProviderError(
                    f"PROVIDER_{event.code.upper()}",
                    event.message,
                    fatal=event.fatal,
                )
            if isinstance(event, VendorConnectionClosed):
                raise RealtimeProviderError(
                    "PROVIDER_CLOSED_BEFORE_READY", "实时供应商连接提前关闭"
                )
            # ready 之前出现其它事件（正常协议不应出现）忽略并继续等待。

    # -- 上行 ----------------------------------------------------------

    async def send_audio(self, pcm: bytes) -> None:
        if self._transport is None or self._closed:
            raise RealtimeProviderError("PROVIDER_NOT_READY", "上游未就绪")
        try:
            await self._transport.send_bytes(pcm)
            self.bytes_sent += len(pcm)
        except Exception as exc:  # noqa: BLE001
            raise RealtimeProviderError(
                "PROVIDER_SEND_FAILED", "实时供应商音频写入失败"
            ) from exc

    async def send_update(self, instructions: str) -> None:
        await self._send_json(build_vendor_update_event(instructions))

    async def send_cancel(self) -> None:
        """打断当前回复（best-effort；连接已死时静默）。"""
        try:
            await self._send_json(VENDOR_CANCEL_EVENT)
        except RealtimeProviderError:
            logger.debug("senseaudio_cancel_send_failed epoch=%s", self.epoch)

    async def end(self) -> None:
        """优雅结束供应商会话（服务端随后关闭连接）。"""
        try:
            await self._send_json(VENDOR_END_EVENT)
        except RealtimeProviderError:
            pass

    async def aclose(self) -> None:
        """关闭连接并终止泵任务（幂等、best-effort）。"""
        self._closed = True
        if self._pump_task is not None:
            self._pump_task.cancel()
            try:
                await self._pump_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._pump_task = None
        if self._transport is not None:
            await self._transport.aclose()
            self._transport = None

    # -- 事件流 --------------------------------------------------------

    async def events(self) -> AsyncIterator[VendorEvent]:
        """逐个产出解析后的供应商事件，连接结束后终止。

        ``VendorConnectionClosed`` 是终结事件：产出一次后迭代终止，
        保证消费者不会把关闭通知之后的补投帧当作新事件。
        """
        while True:
            event = await self._next_event(None)
            if event is None:
                return
            yield event
            if isinstance(event, VendorConnectionClosed):
                return

    # -- 内部 ----------------------------------------------------------

    async def _send_json(self, payload: dict[str, Any]) -> None:
        if self._transport is None or self._closed:
            raise RealtimeProviderError("PROVIDER_NOT_READY", "上游未就绪")
        try:
            await self._transport.send_text(json.dumps(payload, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            raise RealtimeProviderError(
                "PROVIDER_SEND_FAILED", "实时供应商指令写入失败"
            ) from exc

    async def _next_event(self, timeout: float | None) -> VendorEvent | None:
        """取一个事件；超时抛 RealtimeProviderError、EOF 返回 None。

        队列满时丢最旧事件：控制事件丢一批比整条会话卡死更好，
        音频不经过本队列（二进制帧在 pump 内直接进队列，见下）。
        """
        try:
            if timeout is None:
                return await self._queue.get()
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise RealtimeProviderError(
                "PROVIDER_EVENT_TIMEOUT", "实时供应商事件超时"
            ) from exc

    async def _pump(self) -> None:
        """recv 循环：JSON → 解析入队；二进制 → 绑定当前响应上下文。"""
        assert self._transport is not None
        try:
            while not self._closed:
                frame = await self._transport.recv()
                if frame is None:
                    self._push(VendorConnectionClosed(code=1000, reason="eof"))
                    return
                if isinstance(frame, bytes):
                    self._push_binary(frame)
                    continue
                event = self._parse_text_event(frame)
                if event is not None:
                    self._push(event)
                    if isinstance(event, VendorError) and event.fatal:
                        return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "senseaudio_pump_failed epoch=%s err=%s", self.epoch, type(exc).__name__
            )
            self._push(VendorConnectionClosed(code=1011, reason=type(exc).__name__))
        finally:
            # 泵退出后保证消费者不被永久阻塞。
            self._push_poison()

    def _push(self, event: VendorEvent) -> None:
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning(
                "senseaudio_event_queue_overflow epoch=%s", self.epoch
            )

    def _push_poison(self) -> None:
        try:
            self._queue.put_nowait(
                VendorConnectionClosed(code=1006, reason="pump-exit")
            )
        except asyncio.QueueFull:
            pass

    def _push_binary(self, frame: bytes) -> None:
        if not self._current_response_id:
            # 无响应上下文的音频帧：协议不允许，丢弃并记录（不计数到世代）。
            logger.debug(
                "senseaudio_orphan_audio_frame epoch=%s bytes=%s",
                self.epoch,
                len(frame),
            )
            return
        self._push(
            VendorAudio(
                response_id=self._current_response_id, pcm=frame
            )
        )

    def _parse_text_event(self, frame: str) -> VendorEvent | None:
        try:
            data = json.loads(frame)
        except json.JSONDecodeError:
            logger.debug("senseaudio_bad_json epoch=%s", self.epoch)
            return None
        if not isinstance(data, dict):
            return None
        event_type = str(data.get("type") or "")
        if event_type == VENDOR_TYPE_READY:
            return VendorReady(session_id=str(data.get("session_id") or ""))
        if event_type == VENDOR_TYPE_SPEECH_STARTED:
            return VendorSpeechStarted()
        if event_type == VENDOR_TYPE_USER_TRANSCRIPT_DELTA:
            return VendorUserTranscriptDelta(text=str(data.get("text") or ""))
        if event_type == VENDOR_TYPE_USER_TRANSCRIPT_DONE:
            return VendorUserTranscriptDone(text=str(data.get("text") or ""))
        if event_type == VENDOR_TYPE_ASSISTANT_TEXT_DELTA:
            return VendorAssistantTextDelta(text=str(data.get("text") or ""))
        if event_type == VENDOR_TYPE_ASSISTANT_TEXT_DONE:
            return VendorAssistantTextDone(text=str(data.get("text") or ""))
        if event_type == VENDOR_TYPE_ASSISTANT_AUDIO_START:
            response_id = str(data.get("response_id") or "")
            self._current_response_id = response_id
            self._current_response_sample_rate = int(data.get("sample_rate") or 0)
            return VendorAssistantAudioStart(
                response_id=response_id,
                sample_rate=self._current_response_sample_rate,
            )
        if event_type == VENDOR_TYPE_ASSISTANT_AUDIO_DONE:
            response_id = str(data.get("response_id") or "")
            self._current_response_id = ""
            self._current_response_sample_rate = 0
            return VendorAssistantAudioDone(response_id=response_id)
        if event_type == VENDOR_TYPE_TURN_DONE:
            return VendorTurnDone()
        if event_type == VENDOR_TYPE_ERROR:
            code = str(data.get("code") or "unknown")
            message = str(data.get("message") or "")
            fatal = is_fatal_vendor_error(code)
            logger.warning(
                "senseaudio_error epoch=%s code=%s fatal=%s", self.epoch, code, fatal
            )
            return VendorError(code=code, message=message, fatal=fatal)
        # 未知事件（command.ack / session.action.completed / tool.* 等）：
        # 首版无工具流程，忽略。
        logger.debug(
            "senseaudio_unhandled_event epoch=%s type=%s", self.epoch, event_type
        )
        return None


__all__ = [
    "RealtimeProviderConfig",
    "RealtimeProviderError",
    "RealtimeTransport",
    "TransportFactory",
    "SenseAudioUpstream",
    "VendorEvent",
    "VendorReady",
    "VendorSpeechStarted",
    "VendorUserTranscriptDelta",
    "VendorUserTranscriptDone",
    "VendorAssistantTextDelta",
    "VendorAssistantTextDone",
    "VendorAssistantAudioStart",
    "VendorAudio",
    "VendorAssistantAudioDone",
    "VendorTurnDone",
    "VendorError",
    "VendorConnectionClosed",
    "VENDOR_TYPE_UPDATE",
]

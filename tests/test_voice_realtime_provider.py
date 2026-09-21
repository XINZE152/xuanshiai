"""实时语音供应商适配（SenseAudioUpstream）单测。

覆盖：start/ready 握手、二进制帧绑定当前响应上下文、致命错误转译、
取消与 update 指令、EOF/异常事件流终止。传输全部注入 fake，
不访问网络。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.services.voice.realtime.provider import (
    RealtimeProviderConfig,
    RealtimeProviderError,
    SenseAudioUpstream,
    VendorAssistantAudioDone,
    VendorAssistantAudioStart,
    VendorAudio,
    VendorConnectionClosed,
    VendorTurnDone,
)


class FakeTransport:
    """脚本化传输：测试往 incoming 推帧，记录全部上行。"""

    def __init__(self) -> None:
        self.sent_text: list[str] = []
        self.sent_bytes: list[bytes] = []
        self.incoming: asyncio.Queue[str | bytes | None] = asyncio.Queue()
        self.close_calls: list[int] = []

    async def send_text(self, payload: str) -> None:
        self.sent_text.append(payload)

    async def send_bytes(self, payload: bytes) -> None:
        self.sent_bytes.append(payload)

    async def recv(self) -> str | bytes | None:
        return await self.incoming.get()

    async def aclose(self, code: int = 1000) -> None:
        self.close_calls.append(code)


def sa(event_type: str, **fields: object) -> str:
    return json.dumps({"type": event_type, **fields})


def make_config() -> RealtimeProviderConfig:
    return RealtimeProviderConfig(
        ws_url="wss://senseaudio.test/ws",
        api_key="test-key",
        model="senseaudio-realtime-1.0",
        ready_timeout=1.0,
    )


async def feed(transport: FakeTransport, *frames: str | bytes | None) -> None:
    for frame in frames:
        transport.incoming.put_nowait(frame)


async def collect(upstream: SenseAudioUpstream, count: int) -> list:
    events = []
    iterator = upstream.events()
    for _ in range(count):
        events.append(await iterator.__anext__())
    return events


@pytest.mark.asyncio
async def test_start_sends_start_event_and_waits_ready() -> None:
    transport = FakeTransport()

    async def factory() -> FakeTransport:
        return transport

    upstream = SenseAudioUpstream(make_config(), transport_factory=factory, epoch=1)
    start_task = asyncio.create_task(upstream.start("测试 instructions"))
    await asyncio.sleep(0.05)
    transport.incoming.put_nowait(sa("ready", session_id="sess-1"))
    await asyncio.wait_for(start_task, timeout=2.0)

    sent = json.loads(transport.sent_text[0])
    assert sent["type"] == "start"
    assert sent["model"] == "senseaudio-realtime-1.0"
    assert sent["instructions"] == "测试 instructions"
    assert sent["greeting"] == ""
    assert sent["tools"] == []
    assert sent["audio_setting"]["sample_rate"] == 16000
    assert sent["audio_setting"]["format"] == "pcm_s16le"
    await upstream.aclose()


@pytest.mark.asyncio
async def test_fatal_error_before_ready_raises() -> None:
    transport = FakeTransport()

    async def factory() -> FakeTransport:
        return transport

    upstream = SenseAudioUpstream(make_config(), transport_factory=factory, epoch=1)
    start_task = asyncio.create_task(upstream.start("x"))
    await asyncio.sleep(0.05)
    transport.incoming.put_nowait(
        sa("error", code="insufficient_funds", message="余额不足")
    )
    with pytest.raises(RealtimeProviderError) as exc_info:
        await asyncio.wait_for(start_task, timeout=2.0)
    assert exc_info.value.code == "PROVIDER_INSUFFICIENT_FUNDS"
    await upstream.aclose()


@pytest.mark.asyncio
async def test_binary_frames_bind_to_current_response_context() -> None:
    transport = FakeTransport()

    async def factory() -> FakeTransport:
        return transport

    upstream = SenseAudioUpstream(make_config(), transport_factory=factory, epoch=1)
    start_task = asyncio.create_task(upstream.start("x"))
    await asyncio.sleep(0.05)
    transport.incoming.put_nowait(sa("ready", session_id="s"))
    await asyncio.wait_for(start_task, timeout=2.0)

    # 无响应上下文的音频帧被丢弃，不出事件。
    transport.incoming.put_nowait(b"\x01\x02")
    # 有上下文后按序产出 audio 事件，audio.done 后上下文清空。
    await feed(
        transport,
        sa("assistant.audio.start", response_id="r-1", sample_rate=24000),
        b"\x01\x02\x03\x04",
        b"\x05\x06",
        sa("assistant.audio.done", response_id="r-1"),
        b"\x07\x08",  # 又是孤儿帧
        sa("turn.done"),
    )
    events = await collect(upstream, 5)
    assert isinstance(events[0], VendorAssistantAudioStart)
    assert events[0].response_id == "r-1"
    assert events[0].sample_rate == 24000
    assert isinstance(events[1], VendorAudio)
    assert events[1].pcm == b"\x01\x02\x03\x04"
    assert events[1].response_id == "r-1"
    assert isinstance(events[2], VendorAudio)
    assert events[2].pcm == b"\x05\x06"
    assert isinstance(events[3], VendorAssistantAudioDone)
    assert isinstance(events[4], VendorTurnDone)
    await upstream.aclose()


@pytest.mark.asyncio
async def test_text_and_binary_events_mixed() -> None:
    transport = FakeTransport()

    async def factory() -> FakeTransport:
        return transport

    upstream = SenseAudioUpstream(make_config(), transport_factory=factory, epoch=1)
    start_task = asyncio.create_task(upstream.start("x"))
    await asyncio.sleep(0.05)
    transport.incoming.put_nowait(sa("ready", session_id="s"))
    await asyncio.wait_for(start_task, timeout=2.0)

    await feed(
        transport,
        sa("speech.started"),
        sa("user.transcript.delta", text="我喜欢"),
        sa("user.transcript.delta", text="我喜欢爬山"),
        sa("user.transcript.done", text="我喜欢爬山"),
        sa("assistant.text.delta", text="好"),
        sa("assistant.text.done", text="好啊"),
    )
    events = await collect(upstream, 6)
    assert [type(e).__name__ for e in events] == [
        "VendorSpeechStarted",
        "VendorUserTranscriptDelta",
        "VendorUserTranscriptDelta",
        "VendorUserTranscriptDone",
        "VendorAssistantTextDelta",
        "VendorAssistantTextDone",
    ]
    await upstream.aclose()


@pytest.mark.asyncio
async def test_cancel_and_update_send_json() -> None:
    transport = FakeTransport()

    async def factory() -> FakeTransport:
        return transport

    upstream = SenseAudioUpstream(make_config(), transport_factory=factory, epoch=1)
    start_task = asyncio.create_task(upstream.start("x"))
    await asyncio.sleep(0.05)
    transport.incoming.put_nowait(sa("ready", session_id="s"))
    await asyncio.wait_for(start_task, timeout=2.0)

    await upstream.send_update("新 instructions")
    await upstream.send_cancel()
    await upstream.end()
    payloads = [json.loads(t) for t in transport.sent_text]
    assert payloads[1] == {"type": "update", "instructions": "新 instructions"}
    assert payloads[2] == {"type": "cancel"}
    assert payloads[3] == {"type": "end"}
    await upstream.aclose()


@pytest.mark.asyncio
async def test_eof_terminates_event_stream() -> None:
    transport = FakeTransport()

    async def factory() -> FakeTransport:
        return transport

    upstream = SenseAudioUpstream(make_config(), transport_factory=factory, epoch=1)
    start_task = asyncio.create_task(upstream.start("x"))
    await asyncio.sleep(0.05)
    transport.incoming.put_nowait(sa("ready", session_id="s"))
    await asyncio.wait_for(start_task, timeout=2.0)

    transport.incoming.put_nowait(None)
    iterator = upstream.events()
    event = await iterator.__anext__()
    assert isinstance(event, VendorConnectionClosed)
    with pytest.raises(StopAsyncIteration):
        await iterator.__anext__()
    await upstream.aclose()


@pytest.mark.asyncio
async def test_connect_timeout_raises() -> None:
    config = RealtimeProviderConfig(
        ws_url="wss://senseaudio.test/ws",
        api_key="k",
        model="m",
        connect_timeout=0.01,
        ready_timeout=1.0,
    )

    async def factory() -> FakeTransport:
        await asyncio.sleep(0.2)
        return FakeTransport()

    upstream = SenseAudioUpstream(config, transport_factory=factory, epoch=1)
    with pytest.raises(RealtimeProviderError) as exc_info:
        await upstream.start("x")
    assert exc_info.value.code == "PROVIDER_CONNECT_TIMEOUT"

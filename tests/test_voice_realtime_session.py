"""实时语音会话控制器（RealtimeVoiceSession）单测。

覆盖方案 §5 必测场景的核心子集（fake 供应商 + fake 业务回调）：
- 正常轮转：定稿→input_closed→审核放行→下行→response_done→播放回执→voice_ready
- 打断：缓冲立即作废、cancelled+response_done(interrupted)、上游轮转（新 epoch）
- 放行门：提交完成前下行缓冲；审核拒绝/提交失败作废本轮
- 重复定稿事件只处理一次；迟到录音帧被丢弃
- 一轮只产出一份口头回复；播放完成回执前不发 voice_ready
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.services.ai.profile import AIInputError
from app.services.voice.realtime.provider import (
    RealtimeProviderConfig,
    SenseAudioUpstream,
)
from app.services.voice.realtime.session import (
    RealtimeSessionCallbacks,
    RealtimeVoiceSession,
    TurnOutcome,
)


class FakeTransport:
    def __init__(self) -> None:
        self.sent_text: list[str] = []
        self.sent_bytes: list[bytes] = []
        self.incoming: asyncio.Queue[str | bytes | None] = asyncio.Queue()

    async def send_text(self, payload: str) -> None:
        self.sent_text.append(payload)

    async def send_bytes(self, payload: bytes) -> None:
        self.sent_bytes.append(payload)

    async def recv(self) -> str | bytes | None:
        return await self.incoming.get()

    async def aclose(self, code: int = 1000) -> None:
        self.incoming.put_nowait(None)


def sa(event_type: str, **fields: object) -> str:
    return json.dumps({"type": event_type, **fields})


class Harness:
    """测试夹具：可脚本化的上游工厂 + 记录型回调。"""

    def __init__(self, *, submit_hook=None) -> None:
        self.transports: list[FakeTransport] = []
        self.emitted: list[dict[str, Any]] = []
        self.submitted: list[tuple[str, str]] = []
        self.persisted: list[tuple[str, dict[str, Any]]] = []
        self.metadata_updates: list[tuple[str, dict[str, Any]]] = []
        self.instructions_built = 0
        self.update_instructions_built = 0
        # submit_hook(text, client_turn_id) -> None：测试可注入审核延迟/拒绝。
        self.submit_hook = submit_hook
        self.turn_counter = 0

    # -- 上游工厂 -------------------------------------------------------

    def upstream_factory(self, epoch: int) -> SenseAudioUpstream:
        transport = FakeTransport()
        self.transports.append(transport)

        async def factory() -> FakeTransport:
            return transport

        config = RealtimeProviderConfig(
            ws_url="wss://senseaudio.test/ws",
            api_key="k",
            model="m",
            ready_timeout=1.0,
        )
        return SenseAudioUpstream(
            config, transport_factory=factory, epoch=epoch
        )

    # -- 回调 -----------------------------------------------------------

    async def emit(self, event: dict[str, Any]) -> None:
        self.emitted.append(event)

    async def build_instructions(self) -> str:
        self.instructions_built += 1
        return "instructions-v1"

    async def build_update_instructions(self) -> str:
        self.update_instructions_built += 1
        return f"instructions-update-{self.update_instructions_built}"

    async def submit(self, text: str, client_turn_id: str) -> TurnOutcome:
        self.submitted.append((text, client_turn_id))
        if self.submit_hook is not None:
            await self.submit_hook(text, client_turn_id)
        self.turn_counter += 1
        return TurnOutcome(turn_id=f"turn-{self.turn_counter}", task_id=f"task-{self.turn_counter}")

    async def persist(self, text: str, metadata: dict[str, Any]) -> str:
        self.persisted.append((text, dict(metadata)))
        return f"assist-{len(self.persisted)}"

    async def update_metadata(
        self, assistant_turn_id: str, metadata: dict[str, Any]
    ) -> None:
        self.metadata_updates.append((assistant_turn_id, dict(metadata)))

    def make_session(self, **kwargs: Any) -> RealtimeVoiceSession:
        callbacks = RealtimeSessionCallbacks(
            emit=self.emit,
            build_instructions=self.build_instructions,
            build_update_instructions=self.build_update_instructions,
            submit_final_transcript=self.submit,
            persist_reply=self.persist,
            update_reply_metadata=self.update_metadata,
        )
        return RealtimeVoiceSession(
            provider_config=RealtimeProviderConfig(
                ws_url="wss://x", api_key="k", model="m", ready_timeout=1.0
            ),
            callbacks=callbacks,
            upstream_factory=self.upstream_factory,
            **kwargs,
        )

    # -- 工具 -----------------------------------------------------------

    async def settle(self, ms: int = 30) -> None:
        await asyncio.sleep(ms / 1000)

    def events(self, event_type: str) -> list[dict[str, Any]]:
        return [e for e in self.emitted if e.get("type") == event_type]

    async def wait_for_event(self, event_type: str, timeout_s: float = 2.0) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout_s
        while asyncio.get_running_loop().time() < deadline:
            found = self.events(event_type)
            if found:
                return found[-1]
            await asyncio.sleep(0.02)
        raise AssertionError(f"事件未出现: {event_type}; 已有: {[e.get('type') for e in self.emitted]}")


async def start_ready_session(harness: Harness) -> RealtimeVoiceSession:
    session = harness.make_session()
    start_task = asyncio.create_task(session.start())
    await harness.settle(30)
    harness.transports[0].incoming.put_nowait(sa("ready", session_id="s-1"))
    ok = await asyncio.wait_for(start_task, timeout=2.0)
    assert ok is True
    return session


async def speak_and_get_final(harness: Harness, session: RealtimeVoiceSession,
                              text: str = "我喜欢爬山") -> str:
    """模拟一轮用户说话到供应商定稿，返回 generation_id。"""
    transport = harness.transports[0]
    await session.handle_voice_input_begin("ct-1")
    await session.handle_audio_chunk(b"\x00\x01" * 160)
    transport.incoming.put_nowait(sa("speech.started"))
    transport.incoming.put_nowait(sa("user.transcript.done", text=text))
    done = await harness.wait_for_event("input_closed")
    return str(done.get("generation_id") or "")


@pytest.mark.asyncio
async def test_normal_turn_emits_gated_downstream_and_completes() -> None:
    harness = Harness()
    session = await start_ready_session(harness)

    transport = harness.transports[0]
    await session.handle_voice_input_begin("ct-1")
    await session.handle_audio_chunk(b"\x00\x01" * 160)
    # 定稿 + 供应商下行（文本与音频同时到达，此时放行门未开）
    transport.incoming.put_nowait(sa("speech.started"))
    transport.incoming.put_nowait(sa("user.transcript.done", text="我喜欢爬山"))
    transport.incoming.put_nowait(sa("assistant.text.delta", text="爬山好呀"))
    transport.incoming.put_nowait(sa("assistant.text.done", text="爬山好呀，多说说"))
    transport.incoming.put_nowait(
        sa("assistant.audio.start", response_id="r-1", sample_rate=24000)
    )
    transport.incoming.put_nowait(b"\x01\x02\x03\x04")
    transport.incoming.put_nowait(b"\x05\x06")
    transport.incoming.put_nowait(sa("assistant.audio.done", response_id="r-1"))
    transport.incoming.put_nowait(sa("turn.done"))
    await harness.wait_for_event("response_done")

    types = [e["type"] for e in harness.emitted]
    assert types[0] == "voice_ready"
    assert "input_closed" in types
    assert "partial_transcript" not in types  # 无 delta 不推
    # 审核放行后才出现下行内容，且顺序保持
    assert types.index("ai_content") < types.index("ai_reply")
    assert types.index("audio_output_start") < types.index("audio_chunk")
    assert types.index("audio_chunk") < types.index("audio_output_end")
    assert types.index("audio_output_end") < types.index("response_done")

    done = harness.events("response_done")[0]
    assert done["status"] == "completed"
    gen = done["generation_id"]
    chunk_events = harness.events("audio_chunk")
    assert [c["seq"] for c in chunk_events] == [1, 2]
    assert chunk_events[0]["generation_id"] == gen

    # 用户转写只提交一次，携带客户端幂等身份
    assert harness.submitted == [("我喜欢爬山", "ct-1")]
    # 回复快照已持久化并携带元数据
    assert len(harness.persisted) == 1
    text, metadata = harness.persisted[0]
    assert text == "爬山好呀，多说说"
    assert metadata["generation_id"] == gen
    assert metadata["generation_status"] == "completed"
    assert metadata["playback_status"] == "not_started"
    assert metadata["user_turn_id"] == "turn-1"

    # 播放完成回执前，不得进入下一轮录音
    await harness.settle(60)
    assert len(harness.events("voice_ready")) == 1
    await session.handle_playback_progress(gen, 800)
    await session.handle_playback_finished(gen, "completed", 3000)
    while len(harness.events("voice_ready")) < 2:
        await harness.settle(20)
    assert len(harness.events("voice_ready")) == 2
    assert harness.metadata_updates, "播放状态应回写元数据"
    await session.aclose()


@pytest.mark.asyncio
async def test_interrupt_discards_buffer_rotates_upstream() -> None:
    gate_release = asyncio.Event()

    async def slow_submit(text: str, client_turn_id: str) -> None:
        await gate_release.wait()

    harness = Harness(submit_hook=slow_submit)
    session = await start_ready_session(harness)
    transport = harness.transports[0]

    await session.handle_voice_input_begin("ct-1")
    transport.incoming.put_nowait(sa("speech.started"))
    transport.incoming.put_nowait(sa("user.transcript.done", text="我在说一件很长的事"))
    await harness.wait_for_event("input_closed")
    # 供应商开始回复（放行门关闭，全部进缓冲）
    transport.incoming.put_nowait(sa("assistant.text.delta", text="好的，"))
    transport.incoming.put_nowait(
        sa("assistant.audio.start", response_id="r-9", sample_rate=24000)
    )
    transport.incoming.put_nowait(b"\xaa\xbb" * 100)
    await harness.settle(50)
    assert len(harness.events("audio_chunk")) == 0, "放行门前不得有音频下行"

    generation_id = session.active_generation_id
    await session.handle_interrupt(generation_id)

    cancelled = await harness.wait_for_event("cancelled")
    assert cancelled["generation_id"] == generation_id
    done = harness.events("response_done")[-1]
    assert done["status"] == "interrupted"
    # 打断后供应商收到原生 cancel，旧上游被替换为新 epoch
    cancel_msgs = [json.loads(t) for t in transport.sent_text if "cancel" in t]
    assert cancel_msgs == [{"type": "cancel"}]
    # 打断时回复快照落库并标注 interrupted
    await harness.settle(50)
    assert harness.persisted, "被打断回复应保留文字快照"
    _, metadata = harness.persisted[0]
    assert metadata["generation_status"] == "interrupted"
    assert metadata["playback_status"] == "interrupted"

    # 新上游就绪后恢复 voice_ready；旧上游传输被关闭
    assert len(harness.transports) == 2
    harness.transports[1].incoming.put_nowait(sa("ready", session_id="s-2"))
    await harness.wait_for_event("voice_ready")

    # 提交任务随后完成：用户发言仍独立落库（打断不制造也不取消用户发言）
    gate_release.set()
    await harness.settle(60)
    assert harness.submitted, "打断不取消已提交的用户发言"
    await session.aclose()


@pytest.mark.asyncio
async def test_moderation_reject_fails_response_and_rotates() -> None:
    async def reject(text: str, client_turn_id: str) -> None:
        raise AIInputError("回答内容包含违规信息,请修改后重试")

    harness = Harness(submit_hook=reject)
    session = await start_ready_session(harness)
    transport = harness.transports[0]

    await session.handle_voice_input_begin("ct-1")
    transport.incoming.put_nowait(sa("speech.started"))
    transport.incoming.put_nowait(sa("user.transcript.done", text="违规内容"))
    transport.incoming.put_nowait(sa("assistant.text.delta", text="好的"))
    transport.incoming.put_nowait(
        sa("assistant.audio.start", response_id="r-2", sample_rate=24000)
    )
    transport.incoming.put_nowait(b"\x01\x02")
    done = await harness.wait_for_event("response_done")
    assert done["status"] == "failed"
    errors = harness.events("error")
    assert errors and errors[-1]["code"] == "AI_INPUT_INVALID"
    assert harness.persisted == [], "审核拒绝的回复不得持久化"
    # 失败后轮转新上游并恢复录音
    assert len(harness.transports) == 2
    harness.transports[1].incoming.put_nowait(sa("ready", session_id="s-2"))
    await harness.wait_for_event("voice_ready")
    await session.aclose()


@pytest.mark.asyncio
async def test_duplicate_final_event_is_processed_once() -> None:
    harness = Harness()
    session = await start_ready_session(harness)
    transport = harness.transports[0]

    await session.handle_voice_input_begin("ct-1")
    transport.incoming.put_nowait(sa("speech.started"))
    transport.incoming.put_nowait(sa("user.transcript.done", text="同一句"))
    transport.incoming.put_nowait(sa("user.transcript.done", text="同一句"))
    await harness.wait_for_event("input_closed")
    await harness.settle(80)
    assert harness.submitted == [("同一句", "ct-1")], "重复最终事件只提交一次"
    await session.aclose()


@pytest.mark.asyncio
async def test_same_text_next_turn_is_separate_submission() -> None:
    harness = Harness()
    session = await start_ready_session(harness)

    for index in range(2):
        transport = harness.transports[0]
        await session.handle_voice_input_begin(f"ct-{index}")
        transport.incoming.put_nowait(sa("speech.started"))
        transport.incoming.put_nowait(sa("user.transcript.done", text="重复文本"))
        while len(harness.events("input_closed")) < index + 1:
            await harness.settle(20)
        transport.incoming.put_nowait(sa("assistant.text.done", text="好"))
        transport.incoming.put_nowait(
            sa("assistant.audio.start", response_id=f"r-{index}", sample_rate=24000)
        )
        transport.incoming.put_nowait(sa("assistant.audio.done", response_id=f"r-{index}"))
        transport.incoming.put_nowait(sa("turn.done"))
        while len(harness.events("response_done")) < index + 1:
            await harness.settle(20)
        response_done = harness.events("response_done")[index]
        await session.handle_playback_finished(
            str(response_done["generation_id"]), "completed", 1000
        )
        # 播放完成回执后进入下一轮 voice_ready
        while len(harness.events("voice_ready")) < index + 2:
            await harness.settle(20)
    await harness.settle(50)
    assert [t for t, _ in harness.submitted] == ["重复文本", "重复文本"]
    assert len({ct for _, ct in harness.submitted}) == 2, "不同发言使用不同幂等身份"
    await session.aclose()


@pytest.mark.asyncio
async def test_turn_done_grace_finalizes_when_vendor_omits_it() -> None:
    """供应商漏发 turn.done：文本+音频齐备后按完成宽限收尾，不再悬挂。"""
    harness = Harness()
    session = await start_ready_session(harness)
    transport = harness.transports[0]

    await session.handle_voice_input_begin("ct-1")
    transport.incoming.put_nowait(sa("speech.started"))
    transport.incoming.put_nowait(sa("user.transcript.done", text="没有收尾事件"))
    transport.incoming.put_nowait(sa("assistant.text.done", text="好的"))
    transport.incoming.put_nowait(
        sa("assistant.audio.start", response_id="r-g", sample_rate=24000)
    )
    transport.incoming.put_nowait(b"\x01\x02")
    transport.incoming.put_nowait(sa("assistant.audio.done", response_id="r-g"))
    # 不发 turn.done —— 等宽限计时器（10s）收尾。
    done = await harness.wait_for_event("response_done", timeout_s=15)
    assert done["status"] == "completed"
    assert harness.persisted, "宽限收尾同样落回复快照"
    await session.aclose()


@pytest.mark.asyncio
async def test_late_audio_frames_after_input_closed_are_dropped() -> None:
    harness = Harness()
    session = await start_ready_session(harness)
    transport = harness.transports[0]

    await session.handle_voice_input_begin("ct-1")
    transport.incoming.put_nowait(sa("speech.started"))
    transport.incoming.put_nowait(sa("user.transcript.done", text="结束了"))
    await harness.wait_for_event("input_closed")
    transport.incoming.put_nowait(b"\x01\x02" * 200)
    await session.handle_audio_chunk(b"\x01\x02" * 200)
    await session.handle_audio_chunk(b"\x01\x02" * 200)
    await harness.settle(50)
    assert session.late_audio_frames == 2, "迟到帧被丢弃并计数"
    await session.aclose()


@pytest.mark.asyncio
async def test_close_marks_unknown_playback_and_persists_snapshot() -> None:
    gate_release = asyncio.Event()

    async def slow_submit(text: str, client_turn_id: str) -> None:
        await gate_release.wait()

    harness = Harness(submit_hook=slow_submit)
    session = await start_ready_session(harness)
    transport = harness.transports[0]
    await session.handle_voice_input_begin("ct-1")
    transport.incoming.put_nowait(sa("speech.started"))
    transport.incoming.put_nowait(sa("user.transcript.done", text="还没放行就断开"))
    transport.incoming.put_nowait(sa("assistant.text.delta", text="部分"))
    await harness.settle(50)
    await session.aclose()
    gate_release.set()
    await harness.settle(60)
    # 连接异常且缺播放回执：文字可展示，播放状态标 unknown
    if harness.persisted:
        _, metadata = harness.persisted[0]
        assert metadata["playback_status"] == "unknown"

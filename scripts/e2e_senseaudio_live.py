"""P0/P3 真实端到端探针：真语音 → SenseAudio 全轮次 + 打断 + update。

与 ``probe_senseaudio_realtime.py`` 的区别：上行音频是**真实普通话语音**
（阿里云 cosyvoice 合成测试语句 → WAV → 剥离 44 字节头得到 16k/单声道
pcm_s16le），用于核验供应商 VAD/转写/回复/音频下行的完整真实链路。

用法::

    python scripts/e2e_senseaudio_live.py [--out reports/senseaudio_e2e_live.json]

前置：``.env`` 含 AI_SENSEAUDIO_API_KEY 与阿里云 REST TTS 凭据。
输出仅记录事件时序、字节数与测试语句文本（非用户数据），不保存音频。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.services.voice.providers import get_voice_provider  # noqa: E402
from app.services.voice.realtime.provider import (  # noqa: E402
    RealtimeProviderConfig,
    RealtimeProviderError,
    SenseAudioUpstream,
    VendorAssistantAudioDone,
    VendorAssistantAudioStart,
    VendorAssistantTextDone,
    VendorAudio,
    VendorConnectionClosed,
    VendorError,
    VendorReady,
    VendorSpeechStarted,
    VendorTurnDone,
    VendorUserTranscriptDone,
)
from app.services.voice.realtime.protocol import (  # noqa: E402
    build_vendor_update_event,
)

TEST_PHRASE_1 = "我平时喜欢爬山，周末经常一个人去郊外走走看看风景。"
TEST_PHRASE_2 = "我对未来的另一半没有什么硬性要求，聊得来最重要。"

INSTRUCTIONS = (
    "你是实时语音对话测试助手知遇。每次只说一句话，最多 20 个字，"
    "自然口语，不要列表和排版。本会话由协议探针发起，仅用于核验真实链路。"
)


async def tts_to_pcm16k(text: str) -> bytes:
    """阿里云 cosyvoice 合成 WAV 并剥离 44 字节头 → 裸 pcm_s16le。"""
    provider = get_voice_provider("aliyun")
    client = provider._client  # noqa: SLF001 - 探针直用内部客户端

    result = await client.synthesize_speech(
        text=text,
        voice="xiaoyun",
        model=settings.ai_aliyun_voice_tts_model,
        audio_format="wav",
        sample_rate=16000,
        speed=1.0,
    )
    rel = str(result["audio_url"]).replace("/storage/uploads/", "")
    wav_path = Path(settings.upload_dir) / rel
    wav = wav_path.read_bytes()
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE", "不是 WAV"
    # 标准 PCM WAV 头 44 字节；校验 fmt 块后直接截断。
    assert wav[36:40] == b"data", f"WAV 头非标准 44 字节: {wav[36:40]!r}"
    return wav[44:]


class Timeline:
    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.rows: list[dict[str, Any]] = []

    def mark(self, stage: str, event: str, detail: str = "") -> None:
        self.rows.append(
            {
                "t_ms": round((time.monotonic() - self.t0) * 1000, 1),
                "stage": stage,
                "event": event,
                "detail": detail,
            }
        )
        print(f"  [{self.rows[-1]['t_ms']:>8.1f}ms] {stage}: {event} {detail}")


async def collect_until(
    upstream: SenseAudioUpstream,
    timeline: Timeline,
    stage: str,
    stop_types: tuple[type, ...],
    timeout: float,
    audio_sink: list[int] | None = None,
) -> tuple[Any | None, list[Any]]:
    """逐事件消费直到命中 stop_types 或超时；返回 (命中事件, 全部事件)。"""
    seen: list[Any] = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    getter = asyncio.create_task(upstream.events().__anext__())
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            getter.cancel()
            return None, seen
        done, _ = await asyncio.wait({getter}, timeout=min(remaining, 0.4))
        if not done:
            continue
        try:
            event = getter.result()
        except StopAsyncIteration:
            return None, seen
        seen.append(event)
        detail = ""
        if isinstance(event, VendorAudio):
            if audio_sink is not None:
                audio_sink.append(len(event.pcm))
            detail = f"bytes={len(event.pcm)}"
        elif isinstance(event, (VendorUserTranscriptDone, VendorAssistantTextDone)):
            detail = f"len={len(event.text)}"
        elif isinstance(event, VendorError):
            detail = f"code={event.code} fatal={event.fatal} msg={event.message[:80]}"
        timeline.mark(stage, type(event).__name__, detail)
        if isinstance(event, stop_types):
            return event, seen
        getter = asyncio.create_task(upstream.events().__anext__())


async def speak(upstream: SenseAudioUpstream, pcm: bytes, frame_ms: int = 40,
                *, trailing_silence_s: float = 1.2) -> None:
    """按实时节奏送音频；尾部补静音让供应商 VAD 判定轮次结束。"""
    frame_bytes = 16000 * 2 * frame_ms // 1000
    payload = pcm + b"\x00" * int(16000 * 2 * trailing_silence_s)
    for i in range(0, len(payload), frame_bytes):
        await upstream.send_audio(payload[i : i + frame_bytes])
        await asyncio.sleep(frame_ms / 1000)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="reports/senseaudio_e2e_live.json")
    args = parser.parse_args()

    api_key = (
        settings.ai_senseaudio_api_key.get_secret_value()
        if settings.ai_senseaudio_api_key
        else ""
    )
    if not api_key:
        print("缺少 AI_SENSEAUDIO_API_KEY（.env）")
        return 2

    print("合成测试语音（阿里云 cosyvoice → 16k PCM）...")
    pcm1 = await tts_to_pcm16k(TEST_PHRASE_1)
    pcm2 = await tts_to_pcm16k(TEST_PHRASE_2)
    print(f"  语音1 {len(pcm1)}B ≈ {len(pcm1)/32000:.1f}s；语音2 {len(pcm2)}B")

    config = RealtimeProviderConfig(
        ws_url=settings.ai_senseaudio_ws_url,
        api_key=api_key,
        model=settings.ai_senseaudio_model,
        voice=settings.ai_senseaudio_voice,
        ready_timeout=15.0,
    )
    timeline = Timeline()
    upstream = SenseAudioUpstream(config, epoch=1)
    verdict = "FAIL"

    try:
        started = time.monotonic()
        await upstream.start(INSTRUCTIONS)
        timeline.mark("A_ready", "ready",
                      f"elapsed_ms={round((time.monotonic()-started)*1000,1)}")

        # ---- 轮次 1：真实语音全轮次 -------------------------------------
        sink1: list[int] = []
        await speak(upstream, pcm1)
        final, _ = await collect_until(
            upstream, timeline, "T1", (VendorUserTranscriptDone,), 15.0
        )
        if final is None:
            raise AssertionError("未见 user.transcript.done")
        transcript = final.text  # type: ignore[union-attr]
        reply_done, events1 = await collect_until(
            upstream, timeline, "T1",
            (VendorAssistantTextDone, VendorTurnDone, VendorError), 30.0,
            audio_sink=sink1,
        )
        if isinstance(reply_done, VendorError) and reply_done.fatal:
            raise AssertionError(f"致命错误: {reply_done.code}")
        await collect_until(
            upstream, timeline, "T1", (VendorTurnDone,), 30.0
        )
        audio_total = sum(sink1)
        audio_seconds = audio_total / (24000 * 2)
        print(f"  T1 转写={transcript!r}")
        print(f"  T1 回复文本长度={sum(1 for e in events1 if isinstance(e, VendorAssistantTextDone))}"
              f" 下行音频={audio_total}B≈{audio_seconds:.1f}s（{len(sink1)} 帧）")
        ok_t1 = bool(transcript.strip()) and audio_total > 10000

        # ---- 轮次 2：回复中途打断 ---------------------------------------
        sink2: list[int] = []
        cancel_ms = -1.0
        final2, _ = await collect_until(
            upstream, timeline, "T2", (VendorUserTranscriptDone,), 15.0
        )
        if final2 is not None:
            await collect_until(
                upstream, timeline, "T2",
                (VendorAssistantAudioStart, VendorTurnDone), 30.0,
            )
            # 等首帧回复音频真实到达后再打断（audio.start 与首帧间有间隔）。
            await collect_until(
                upstream, timeline, "T2", (VendorAudio,), 10.0,
                audio_sink=sink2,
            )
            if sink2:
                pre_cancel_bytes = sum(sink2)
                cancel_at = time.monotonic()
                await upstream.send_cancel()
                timeline.mark("T2_cancel", "cancel_sent",
                              f"pre_bytes={pre_cancel_bytes}")
                tail, _ = await collect_until(
                    upstream, timeline, "T2",
                    (VendorAssistantAudioDone, VendorTurnDone, VendorError), 25.0,
                    audio_sink=sink2,
                )
                cancel_ms = round((time.monotonic() - cancel_at) * 1000, 1)
                post_bytes = sum(sink2) - pre_cancel_bytes
                print(f"  T2 打断后 {cancel_ms}ms 内结束="
                      f"{type(tail).__name__ if tail else '无事件(25s)'}，"
                      f"打断后新增音频={post_bytes}B")
            # 等本轮彻底收尾再进入 update 测试。
            await collect_until(
                upstream, timeline, "T2", (VendorTurnDone,), 30.0
            )
        ok_t2 = cancel_ms >= 0

        # ---- update 实测（记录供应商行为）-------------------------------
        await upstream.send_update(build_vendor_update_event(INSTRUCTIONS)["instructions"])
        upd, _ = await collect_until(
            upstream, timeline, "U", (VendorError, VendorConnectionClosed), 5.0
        )
        if upd is None:
            print("  U: update 后 5s 无错误（可能已接受或静默忽略）")
            ok_update = True
        else:
            print(f"  U: update 行为={getattr(upd, 'code', type(upd).__name__)}")
            ok_update = not getattr(upd, "fatal", True)

        verdict = ("PASS" if ok_t1 and ok_t2 and ok_update
                   else f"FAIL(t1={ok_t1},t2={ok_t2},update={ok_update})")
    except (AssertionError, RealtimeProviderError) as exc:
        timeline.mark("FATAL", type(exc).__name__, str(exc)[:120])
        verdict = f"FAIL: {exc}"
    finally:
        await upstream.end()
        await asyncio.sleep(0.5)
        await upstream.aclose()

    report = {
        "probe": "senseaudio_e2e_live",
        "verdict": verdict,
        "timeline": timeline.rows,
        "transcripts": {"t1": locals().get("transcript", ""), "t2": locals().get("final2").text if locals().get("final2") else ""},
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(verdict)
    print(f"报告已写入 {out}")
    return 0 if verdict.startswith("PASS") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

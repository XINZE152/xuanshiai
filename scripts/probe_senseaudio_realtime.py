"""P0 技术探针：SenseAudio 实时语音供应商协议核验。

方案 §4 P0-A：用非敏感合成音频核实供应商连接、多轮、取消、空输入、
分片节奏、``update`` 等核心能力；任一核心能力失败则暂不启用实时分支。

用法（凭据只从环境注入，不落盘、不进日志）::

    SENSEAUDIO_API_KEY=sk-xxx python scripts/probe_senseaudio_realtime.py \
        [--audio-seconds 3] [--hold 6] [--out reports/senseaudio_probe.json]

产出：JSON 时序报告（各阶段相对时刻、事件序列、错误分类）。音频为
本地合成的正弦扫频/静音，不含任何用户数据；转写文本仅用于计位，
报告中不保存转写原文。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import struct
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.voice.realtime.provider import (  # noqa: E402
    RealtimeProviderConfig,
    RealtimeProviderError,
    SenseAudioUpstream,
    VendorAssistantAudioStart,
    VendorConnectionClosed,
    VendorError,
    VendorSpeechStarted,
    VendorTurnDone,
    VendorUserTranscriptDone,
)

SAMPLE_RATE = 16000


def synth_speech_like_pcm(seconds: float) -> list[bytes]:
    """合成"说话感"音频：50ms 正弦突发 + 30ms 静音交替，10ms 帧。

    纯合成数据，不含任何用户信息；用于驱动供应商 VAD 与转写事件流。
    """
    chunk_ms = 10
    chunk_bytes = SAMPLE_RATE * 2 * chunk_ms // 1000
    total_chunks = int(seconds * 1000 / chunk_ms)
    frames: list[bytes] = []
    phase = 0.0
    for i in range(total_chunks):
        in_burst = (i % 8) < 5
        samples: list[int] = []
        for _ in range(SAMPLE_RATE * chunk_ms // 1000):
            if in_burst:
                phase += 2 * math.pi * (220 + 60 * math.sin(phase / 40))
                sample = int(9000 * math.sin(phase))
            else:
                sample = 0
            samples.append(max(-32768, min(32767, sample)))
        frames.append(struct.pack(f"<{len(samples)}h", *samples))
        assert len(frames[-1]) == chunk_bytes
    return frames


class ProbeReport:
    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.stages: dict[str, Any] = {}
        self.errors: list[dict[str, str]] = []

    def rel(self) -> float:
        return round((time.monotonic() - self.t0) * 1000, 1)

    def note(self, stage: str, event: str, detail: str = "") -> None:
        self.stages.setdefault(stage, []).append(
            {"t_ms": self.rel(), "event": event, "detail": detail}
        )

    def error(self, stage: str, code: str, message: str) -> None:
        self.errors.append({"stage": stage, "code": code, "message": message})
        self.note(stage, "error", code)


def event_name(event: Any) -> str:
    return type(event).__name__


async def wait_for(upstream: SenseAudioUpstream, report: ProbeReport, stage: str,
                   wanted: tuple[type, ...], timeout: float) -> Any | None:
    """收集事件直到命中 wanted 类型或超时；全部事件记录到报告。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    collect = asyncio.create_task(upstream.events().__anext__())
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            collect.cancel()
            return None
        done, _ = await asyncio.wait({collect}, timeout=min(remaining, 0.5))
        if not done:
            continue
        try:
            event = collect.result()
        except StopAsyncIteration:
            return None
        report.note(stage, event_name(event))
        if isinstance(event, VendorError):
            report.error(stage, event.code, event.message)
            if event.fatal:
                return event
        if isinstance(event, VendorConnectionClosed):
            return event
        if isinstance(event, wanted):
            return event
        collect = asyncio.create_task(upstream.events().__anext__())


async def send_frames(upstream: SenseAudioUpstream, frames: list[bytes],
                      interval_ms: int = 10) -> None:
    for frame in frames:
        await upstream.send_audio(frame)
        await asyncio.sleep(interval_ms / 1000)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio-seconds", type=float, default=3.0)
    parser.add_argument("--hold", type=float, default=8.0,
                        help="每阶段等待事件的最长秒数")
    parser.add_argument("--out", default="reports/senseaudio_probe.json")
    args = parser.parse_args()

    api_key = os.environ.get("SENSEAUDIO_API_KEY", "")
    if not api_key:
        print("缺少 SENSEAUDIO_API_KEY 环境变量（仅存于本进程，不落盘）")
        return 2

    config = RealtimeProviderConfig(
        ws_url=os.environ.get(
            "SENSEAUDIO_WS_URL",
            "wss://api.senseaudio.cn/ws/v1/realtime/voice-dialog",
        ),
        api_key=api_key,
        model=os.environ.get("SENSEAUDIO_MODEL", "senseaudio-realtime-1.0"),
        voice=os.environ.get("SENSEAUDIO_VOICE", ""),
        ready_timeout=args.hold,
    )
    report = ProbeReport()
    instructions = (
        "你是实时语音对话测试助手。用一句不超过 20 字的口语回复。"
        "本条 instructions 由协议探针脚本注入，仅用于核验事件时序。"
    )

    upstream = SenseAudioUpstream(config, epoch=1)

    # ---- 阶段 A：连接与就绪 -------------------------------------------------
    try:
        started = time.monotonic()
        await upstream.start(instructions)
        report.note("A_connect_ready", "ready",
                    f"elapsed_ms={round((time.monotonic() - started) * 1000, 1)}")
    except RealtimeProviderError as exc:
        report.error("A_connect_ready", exc.code, exc.message)
        report.stages["verdict"] = "FAIL: 无法建立/就绪上游连接"
        return _write(args, report)
    except Exception as exc:  # noqa: BLE001
        report.error("A_connect_ready", type(exc).__name__, str(exc)[:200])
        report.stages["verdict"] = "FAIL: 连接阶段未分类异常"
        return _write(args, report)

    frames = synth_speech_like_pcm(args.audio_seconds)

    # ---- 阶段 B：说话→定稿→回复→turn.done（多轮第 1 轮）--------------------
    await send_frames(upstream, frames)
    final = await wait_for(upstream, report, "B_first_turn",
                           (VendorUserTranscriptDone,), args.hold)
    if final is None:
        report.error("B_first_turn", "NO_FINAL_TRANSCRIPT", "未见 user.transcript.done")
    else:
        await wait_for(upstream, report, "B_first_turn",
                       (VendorAssistantAudioStart,), args.hold)
        await wait_for(upstream, report, "B_first_turn",
                       (VendorTurnDone,), args.hold)

    # ---- 阶段 C：多轮第 2 轮（复用连接）------------------------------------
    if not isinstance(final, (VendorError, VendorConnectionClosed)):
        await send_frames(upstream, frames)
        second = await wait_for(upstream, report, "C_second_turn",
                                (VendorUserTranscriptDone,), args.hold)
        if second is None:
            report.error("C_second_turn", "NO_FINAL_TRANSCRIPT", "复用连接第二轮无定稿")
        else:
            await wait_for(upstream, report, "C_second_turn",
                           (VendorTurnDone,), args.hold)

    # ---- 阶段 D：update 指令 ----------------------------------------------
    try:
        await upstream.send_update(instructions)
        report.note("D_update", "update_sent")
        await wait_for(upstream, report, "D_update", (VendorSpeechStarted,
                                                      VendorTurnDone,
                                                      VendorError), 2.0)
    except RealtimeProviderError as exc:
        report.error("D_update", exc.code, exc.message)

    # ---- 阶段 E：奇数字节分片 ----------------------------------------------
    try:
        await upstream.send_audio(b"\x01\x02\x03")  # 3 字节奇数帧
        report.note("E_odd_chunk", "sent", "bytes=3")
        await send_frames(upstream, frames[:100])
        await wait_for(upstream, report, "E_odd_chunk",
                       (VendorUserTranscriptDone, VendorError), args.hold)
    except RealtimeProviderError as exc:
        report.error("E_odd_chunk", exc.code, exc.message)

    # ---- 阶段 F：空输入与取消 ----------------------------------------------
    try:
        report.note("F_cancel", "cancel_sent")
        await upstream.send_cancel()
        await asyncio.sleep(0.5)
        await send_frames(upstream, frames)
        event = await wait_for(upstream, report, "F_cancel",
                               (VendorTurnDone, VendorError), args.hold)
        if isinstance(event, VendorError) and event.fatal:
            report.error("F_cancel", event.code, "取消后致命错误")
    except RealtimeProviderError as exc:
        report.error("F_cancel", exc.code, exc.message)

    # ---- 收尾 --------------------------------------------------------------
    await upstream.end()
    await asyncio.sleep(0.5)
    await upstream.aclose()

    fatal_errors = [
        e for e in report.errors
        if e["code"] in {"NO_FINAL_TRANSCRIPT", "PROVIDER_READY_TIMEOUT"}
        or e["stage"] == "A_connect_ready"
    ]
    if fatal_errors:
        report.stages["verdict"] = f"FAIL: {len(fatal_errors)} 项核心能力未通过"
    else:
        report.stages["verdict"] = "PASS: 连接/多轮/定稿/取消/奇数分片均产生预期事件"
    return _write(args, report)


def _write(args: argparse.Namespace, report: ProbeReport) -> int:
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "probe": "senseaudio_realtime",
        "model": os.environ.get("SENSEAUDIO_MODEL", "senseaudio-realtime-1.0"),
        "errors": report.errors,
        "stages": report.stages,
        "verdict": report.stages.get("verdict", ""),
    }
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(report.stages.get("verdict", ""))
    print(f"报告已写入 {out_path}")
    return 0 if str(payload["verdict"]).startswith("PASS") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

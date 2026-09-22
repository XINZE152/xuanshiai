"""两个 P0 供应商问题的归因与修复实验。

实验 1（断流归因）：同一测试语句、不同上行收尾策略各跑 N 轮：
  - 基线：语音后送 1.2s 尾静音（现状实现）
  - commit：语音后不送静音，发 ``{"type":"commit"}`` 显式提交输入缓冲
  - 无尾静音：语音后直接停（不 commit）
统计每轮结果：完整轮次(turn.done) / 宽限可达(文本+音频齐但无 turn.done) /
fatal 断流，回答"断流是否由我方尾静音帧干扰供应商输入缓冲引起"。

实验 2（update 字段探测）：官方 update 被以 invalid_tts_speed(语速 0.0)
整条忽略；逐一探测未文档化的语速字段名（speed / tts_speed / speech_rate /
tts_setting.speed）。字段非法时供应商可能回 fatal bad_request 并断连，
每变体独立连接，无资损。

用法::

    python scripts/probe_senseaudio_variants.py --rounds 3 \
        --out reports/senseaudio_variants.json
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
    VendorTurnDone,
    VendorUserTranscriptDone,
)
from scripts.e2e_senseaudio_live import (  # noqa: E402
    INSTRUCTIONS,
    TEST_PHRASE_1,
    collect_until,
    tts_to_pcm16k,
)

COMMIT_EVENT = json.dumps({"type": "commit"})

UPDATE_FIELD_CANDIDATES: list[tuple[str, dict[str, Any]]] = [
    ("speed", {"instructions": INSTRUCTIONS, "speed": 1.0}),
    ("tts_speed", {"instructions": INSTRUCTIONS, "tts_speed": 1.0}),
    ("speech_rate", {"instructions": INSTRUCTIONS, "speech_rate": 0}),
    ("tts_setting.speed", {
        "instructions": INSTRUCTIONS,
        "tts_setting": {"speed": 1.0},
    }),
]


async def one_round(
    config: RealtimeProviderConfig,
    pcm: bytes,
    *,
    mode: str,
) -> dict[str, Any]:
    """跑一轮完整语音，返回结果分类与关键时延。"""
    upstream = SenseAudioUpstream(config, epoch=1)
    t0 = time.monotonic()
    outcome = "unknown"
    detail = ""
    reply_text_len = 0
    audio_bytes = 0
    got_turn_done = False
    try:
        await upstream.start(INSTRUCTIONS)
        frame = 16000 * 2 * 40 // 1000
        payload = pcm if mode == "no_silence_commit" else pcm + b"\x00" * int(16000 * 2 * 1.2)
        for i in range(0, len(payload), frame):
            await upstream.send_audio(payload[i : i + frame])
            await asyncio.sleep(40 / 1000)
        if mode == "no_silence_commit":
            await upstream._transport.send_text(COMMIT_EVENT)  # noqa: SLF001

        final, _ = await collect_until(
            upstream, _Void(), "T", (VendorUserTranscriptDone,), 15.0
        )
        if final is None:
            return {"outcome": "no_transcript", "detail": "无定稿", "ms": round((time.monotonic() - t0) * 1000)}
        sink: list[int] = []
        await collect_until(
            upstream, _Void(), "T",
            (VendorAssistantAudioStart, VendorTurnDone, VendorError), 30.0,
            audio_sink=sink,
        )
        text_done, _ = await collect_until(
            upstream, _Void(), "T",
            (VendorAssistantTextDone, VendorTurnDone, VendorError), 30.0,
            audio_sink=sink,
        )
        if isinstance(text_done, VendorAssistantTextDone):
            reply_text_len = len(text_done.text)  # type: ignore[union-attr]
        tail, _ = await collect_until(
            upstream, _Void(), "T",
            (VendorTurnDone, VendorError, VendorConnectionClosed), 50.0,
            audio_sink=sink,
        )
        audio_bytes = sum(sink)
        if isinstance(tail, VendorTurnDone):
            got_turn_done = True
            outcome = "full_turn"
        elif isinstance(tail, VendorError):
            outcome = "fatal_stall"
            detail = f"{tail.code}: {tail.message[:60]}"
        elif isinstance(tail, VendorConnectionClosed):
            outcome = "closed"
        else:
            # 文本+音频已齐但 50s 内无 turn.done —— 宽限可救的"漏发收尾"形态
            if reply_text_len > 0 and audio_bytes > 10000:
                outcome = "content_complete_no_turn_done"
            else:
                outcome = "stalled"
    except RealtimeProviderError as exc:
        outcome = "provider_error"
        detail = exc.code
    finally:
        await upstream.aclose()
    return {
        "outcome": outcome,
        "detail": detail,
        "turn_done": got_turn_done,
        "reply_len": reply_text_len,
        "audio_bytes": audio_bytes,
        "ms": round((time.monotonic() - t0) * 1000),
    }


class _Void:
    """collect_until 的空 report 适配。"""

    def note(self, *args: Any, **kwargs: Any) -> None:
        pass

    def mark(self, *args: Any, **kwargs: Any) -> None:
        pass

    def error(self, *args: Any, **kwargs: Any) -> None:
        pass


async def probe_update_field(
    config: RealtimeProviderConfig, payload: dict[str, Any]
) -> dict[str, Any]:
    """独立连接探测一个 update 字段变体。"""
    upstream = SenseAudioUpstream(config, epoch=1)
    try:
        await upstream.start(INSTRUCTIONS)
        assert upstream._transport is not None  # noqa: SLF001
        await upstream._transport.send_text(  # noqa: SLF001
            json.dumps({"type": "update", **payload}, ensure_ascii=False)
        )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 5.0
        seen: list[str] = []
        while loop.time() < deadline:
            event = await upstream._next_event(min(1.0, deadline - loop.time()))  # noqa: SLF001
            if event is None:
                break
            name = type(event).__name__
            seen.append(
                f"{name}:{getattr(event, 'code', '')}"
                f"{(' fatal=' + str(getattr(event, 'fatal'))) if isinstance(event, VendorError) else ''}"
            )
            if isinstance(event, VendorError) and event.fatal:
                break
            if isinstance(event, VendorConnectionClosed):
                break
        if any("invalid_tts_speed" in s for s in seen):
            outcome = "ignored_invalid_tts_speed"
        elif any(s.startswith("VendorError:bad_request") for s in seen):
            outcome = "rejected_bad_request"
        elif any(s.startswith("VendorError") for s in seen):
            outcome = "other_error"
        elif seen:
            outcome = "events:" + ",".join(seen)
        else:
            outcome = "silent_5s"
        return {"outcome": outcome, "events": seen}
    except RealtimeProviderError as exc:
        return {"outcome": f"provider_error:{exc.code}", "events": []}
    finally:
        await upstream.aclose()


def summarize(rounds: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for r in rounds:
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
    return json.dumps(counts, ensure_ascii=False)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--out", default="reports/senseaudio_variants.json")
    args = parser.parse_args()

    api_key = (
        settings.ai_senseaudio_api_key.get_secret_value()
        if settings.ai_senseaudio_api_key
        else ""
    )
    if not api_key:
        print("缺少 AI_SENSEAUDIO_API_KEY")
        return 2
    config = RealtimeProviderConfig(
        ws_url=settings.ai_senseaudio_ws_url,
        api_key=api_key,
        model=settings.ai_senseaudio_model,
        voice=settings.ai_senseaudio_voice,
        ready_timeout=15.0,
    )
    pcm = await tts_to_pcm16k(TEST_PHRASE_1)
    print(f"测试语音 {len(pcm)}B ≈ {len(pcm)/32000:.1f}s\n")

    report: dict[str, Any] = {"experiment1_stall": {}, "experiment2_update": {}}

    # ---- 实验 1：断流归因（上行收尾策略对照）--------------------------
    modes = [
        ("baseline_silence", "现状：语音后送 1.2s 尾静音"),
        ("no_silence_commit", "语音后发 commit 显式提交"),
        ("baseline_silence", "对照（复跑基线）"),
    ]
    for mode, desc in modes[: 2 if args.rounds > 2 else 3]:
        rounds = []
        print(f"== 变体 {mode}（{desc}）x{args.rounds}")
        for i in range(args.rounds):
            r = await one_round(config, pcm, mode=mode)
            rounds.append(r)
            print(f"   第{i+1}轮: {r['outcome']} {r.get('detail','')} "
                  f"reply={r.get('reply_len',0)}B audio={r.get('audio_bytes',0)}B {r['ms']}ms")
        report["experiment1_stall"][mode] = {
            "rounds": rounds,
            "summary": summarize(rounds),
        }
        print(f"   小计: {summarize(rounds)}\n")

    # ---- 实验 2：update 语速字段探测 ---------------------------------
    print("== 实验 2：update 语速字段探测")
    for name, payload in UPDATE_FIELD_CANDIDATES:
        r = await probe_update_field(config, payload)
        report["experiment2_update"][name] = r
        print(f"   {name}: {r['outcome']}")
        if r["outcome"] not in {"ignored_invalid_tts_speed", "rejected_bad_request",
                                "provider_error:PROVIDER_CONNECT_FAILED",
                                "provider_error:PROVIDER_CONNECT_TIMEOUT"}:
            print(f"      events={r['events']}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

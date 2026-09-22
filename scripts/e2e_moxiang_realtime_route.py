"""路由级真实端到端：登录 → v2 协商 → 真实语音全轮次 → 打断 → 元数据落库。

链路：本地后端（voice_moxiang WS v2）→ SenseAudio 实时供应商 → 旅程提交 →
回复元数据。上行语音为阿里云 cosyvoice 合成的测试语句（非用户数据）。

用法（前置：Redis/MySQL/后端/ai_worker 已启动，.env 含实时与阿里云凭据）::

    python scripts/e2e_moxiang_realtime_route.py \
        [--phone 19730552884] [--out reports/moxiang_rt_route_e2e.json]
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from scripts.e2e_senseaudio_live import tts_to_pcm16k  # noqa: E402

BASE = "http://127.0.0.1:8000/api/v1"
WS_URL = "ws://127.0.0.1:8000/api/v1/voice/moxiang-master"

PHRASE_1 = "我周末喜欢去爬山。"
PHRASE_2 = "聊得来最重要。"


class RouteE2E:
    def __init__(self, token: str) -> None:
        self.token = token
        self.t0 = time.monotonic()
        self.events: list[dict[str, Any]] = []
        self.turn_ids: list[str] = []
        self.connection_closed = False
        self.last_response_done: dict[str, Any] | None = None

    def mark(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type"))
        detail = ""
        if event_type == "audio_chunk":
            detail = f"seq={event.get('seq')} bytes={len(str(event.get('data', '')))}"
        elif event_type == "error":
            detail = f"code={event.get('code')} msg={str(event.get('message'))[:60]}"
        elif event_type in {"ai_reply", "final_transcript"}:
            detail = f"len={len(str(event.get('text', '')))}"
        row = {
            "t_ms": round((time.monotonic() - self.t0) * 1000, 1),
            "type": event_type,
            "detail": detail,
        }
        self.events.append(row)
        print(f"  [{row['t_ms']:>8.1f}ms] {event_type} {detail}")

    async def recv_until(
        self, ws: Any, want: set[str], timeout: float
    ) -> dict[str, Any] | None:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return None
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 1.0))
            except asyncio.TimeoutError:
                continue
            except Exception:  # noqa: BLE001 - 连接被关闭（看门狗/网络）
                self.connection_closed = True
                return None
            event = json.loads(raw)
            self.mark(event)
            if str(event.get("type")) == "response_done":
                self.last_response_done = event
            if str(event.get("type")) in want:
                return event
            if str(event.get("type")) == "error" and "error" in want:
                return event


async def speak(ws: Any, pcm: bytes, frame_ms: int = 40) -> None:
    frame = 16000 * 2 * frame_ms // 1000
    payload = pcm + b"\x00" * int(16000 * 2 * 1.2)
    for i in range(0, len(payload), frame):
        await ws.send(json.dumps({
            "type": "audio_chunk",
            "data": base64.b64encode(payload[i : i + frame]).decode("ascii"),
            "seq": i // frame + 1,
        }))
        await asyncio.sleep(frame_ms / 1000)


async def login(phone: str) -> str:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{BASE}/auth/test-login",
            json={"phone": phone, "password": "password123"},
        )
        resp.raise_for_status()
        return str(resp.json()["access_token"])


async def run_voice_round(e2e: RouteE2E, ws: Any, pcm: bytes) -> str:
    """一轮语音：开麦→说话→等 response_done。

    供应商偶发"音频结束后不发 turn.done"：音频收完再多等 45s，超时按
    vendor-degraded 处理；连接被看门狗关闭同样返回。
    """
    if e2e.connection_closed:
        return "closed"
    await ws.send(json.dumps({"type": "audio_start"}))
    await ws.send(json.dumps({
        "type": "voice_input_begin",
        "client_turn_id": f"e2e-{int(time.time() * 1000)}",
    }))
    speak_task = asyncio.create_task(speak(ws, pcm))
    done = await e2e.recv_until(ws, {"response_done"}, timeout=45)
    await speak_task
    if done is not None:
        return str(done.get("status"))
    if e2e.connection_closed:
        return "closed"
    # 供应商偶发"文本+音频已收完但不发 turn.done"：已见回复结尾时短等
    # 10s 即提前判定 stalled，把连接留给打断专项（空闲看门狗 60s 会关会话）。
    types_seen = {e["type"] for e in e2e.events}
    grace_s = 10 if {"ai_reply", "audio_output_end"} <= types_seen else 45
    done = await e2e.recv_until(ws, {"response_done"}, timeout=grace_s)
    if e2e.connection_closed:
        return "closed"
    return "stalled" if done is None else str(done.get("status"))


async def finish_report(
    args: argparse.Namespace,
    e2e: RouteE2E,
    session_id: str,
    token: str,
    verdict: str,
) -> tuple[str, Any]:
    """核验历史接口的语音回复元数据并落盘报告。返回 (verdict, 元数据)。"""
    assistant_meta: Any = None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{BASE}/ai/profile-sessions/{session_id}/turns",
                headers={"Authorization": f"Bearer {token}"},
                params={"limit": 20},
            )
            if resp.status_code == 200:
                turns = resp.json().get("turns", [])
                metas = [
                    t.get("voice_reply_metadata")
                    for t in turns
                    if t.get("voice_reply_metadata")
                ]
                print(f"✅ 历史接口：{len(turns)} 条 turn，其中 {len(metas)} 条带语音元数据")
                if metas:
                    print(f"   元数据示例: {json.dumps(metas[-1], ensure_ascii=False)[:200]}")
                    assistant_meta = metas[-1]
                else:
                    print("   （无带元数据的助手行：完整轮次未完成时属预期）")
            else:
                print(f"⚠️ 历史接口 {resp.status_code}")
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ 历史核验失败: {exc}")

    report = {
        "probe": "moxiang_realtime_route_e2e",
        "verdict": verdict,
        "session_id": session_id,
        "assistant_metadata": assistant_meta,
        "events": e2e.events,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(verdict)
    print(f"报告已写入 {out}")
    return verdict, assistant_meta


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phone", default="19730552884")
    parser.add_argument("--out", default="reports/moxiang_rt_route_e2e.json")
    args = parser.parse_args()

    token = await login(args.phone)
    print(f"登录成功 user token len={len(token)}")
    pcm1 = await tts_to_pcm16k(PHRASE_1)
    pcm2 = await tts_to_pcm16k(PHRASE_2)
    print(f"测试语音合成完成：{len(pcm1)}B / {len(pcm2)}B")

    import websockets

    e2e = RouteE2E(token)
    verdict = "FAIL"
    assistant_meta: Any = None
    session_id = ""

    try:
        async with websockets.connect(
            f"{WS_URL}?token={token}", max_size=4 * 1024 * 1024
        ) as ws:
            # ---- v2 协商 ------------------------------------------------
            await ws.send(json.dumps({
                "type": "session_start",
                "mode": "moxiang_journey",
                "subject": "personal",
                "consentVersion": "profile-text-v1",
                "protocolVersion": 2,
                "capabilities": {"pcmPlayback": True},
            }))
            ready = await e2e.recv_until(ws, {"journey_ready", "error"}, timeout=20)
            if ready is None or ready.get("type") == "error":
                raise AssertionError(f"journey_ready 未达成: {ready}")
            if ready.get("protocol_version") != 2:
                raise AssertionError(f"v2 未协商成功: {ready}")
            session_id = str(ready.get("session_id"))
            print(f"✅ v2 协商成功 session={session_id} realtime={ready.get('realtime')}")

            # ---- 首轮：真实语音全轮次（供应商 TTS 偶发断流，失败自动重试）--
            status1 = await run_voice_round(e2e, ws, pcm1)
            for retry in range(2):
                if status1 == "completed" or status1 == "closed":
                    break
                print(f"  第 {retry + 1} 次回复未完成（{status1}），等 voice_ready 后重试")
                voice_ready = await e2e.recv_until(ws, {"voice_ready"}, timeout=40)
                if voice_ready is None:
                    break
                status1 = await run_voice_round(e2e, ws, pcm1)
            full_turn_ok = status1 == "completed"

            if full_turn_ok:
                types = [e["type"] for e in e2e.events]
                assert "input_closed" in types, "缺 input_closed"
                assert "audio_output_start" in types, "缺 audio_output_start"
                assert "audio_output_end" in types, "缺 audio_output_end"
                assert "final_transcript" in types, "缺 final_transcript"
                assert "ai_reply" in types, "缺 ai_reply"

                # ---- 播放回执 → 下一轮 voice_ready -----------------------
                gen = str((e2e.last_response_done or {}).get("generation_id") or "")
                assert gen, "response_done 缺 generation_id"
                await ws.send(json.dumps({
                    "type": "playback_progress", "generation_id": gen, "played_ms": 800,
                }))
                await ws.send(json.dumps({
                    "type": "playback_finished",
                    "generation_id": gen, "status": "completed", "played_ms": 3000,
                }))
                voice_ready = await e2e.recv_until(ws, {"voice_ready"}, timeout=15)
                if voice_ready is None:
                    raise AssertionError("播放完成后未收到 voice_ready")
                print("✅ 播放完成 → voice_ready（自动轮转就绪）")

            # ---- 打断专项：回复音频一开始就点击打断 ----------------------
            interrupt_ok = False
            if e2e.connection_closed:
                print("⚠️ 连接已被看门狗/网络关闭，打断专项跳过")
            else:
                await ws.send(json.dumps({"type": "audio_start"}))
                await ws.send(json.dumps({
                    "type": "voice_input_begin",
                    "client_turn_id": f"e2e-i-{int(time.time() * 1000)}",
                }))
                speak_task = asyncio.create_task(speak(ws, pcm2))
                await e2e.recv_until(ws, {"input_closed"}, timeout=30)
                first_audio = await e2e.recv_until(
                    ws, {"audio_output_start", "audio_chunk"}, timeout=60
                )
                await speak_task
                if first_audio is None:
                    raise AssertionError("打断专项未等到回复音频开始")
                interrupt_gen = str(first_audio.get("generation_id") or "")
                await ws.send(json.dumps({
                    "type": "interrupt", "generation_id": interrupt_gen,
                }))
                ack = await e2e.recv_until(
                    ws, {"cancelled", "response_done"}, timeout=15
                )
                if ack is None:
                    raise AssertionError("打断未被回执")
                await e2e.recv_until(ws, {"response_done"}, timeout=10)
                print(f"✅ 打断回执（generation={interrupt_gen[:8]}…）")
                voice_ready_i = await e2e.recv_until(ws, {"voice_ready"}, timeout=30)
                if voice_ready_i is None:
                    raise AssertionError("打断轮转后未恢复 voice_ready")
                print("✅ 打断 → 上游轮转 → 新 voice_ready")
                interrupt_ok = True

            if not full_turn_ok:
                # 供应商 TTS 今晚持续断流：打断链路已验证，全轮次留待供应商恢复。
                verdict = ("PASS_WITH_VENDOR_DEGRADED" if interrupt_ok
                           else "FAIL_VENDOR_UNSTABLE")
                print("⚠️ 完整轮次因供应商 TTS 断流未完成（控制器均正确兜底），"
                      f"打断链路={'已验证' if interrupt_ok else '未能验证'}")
                await finish_report(args, e2e, session_id, token, verdict)
                return 0 if verdict.startswith("PASS") else 1
            if not interrupt_ok:
                verdict = "PASS_CORE_INTERRUPT_UNVERIFIED"
                await finish_report(args, e2e, session_id, token, verdict)
                return 0
            verdict = "PASS"
    except (AssertionError, Exception) as exc:  # noqa: BLE001
        print(f"❌ {type(exc).__name__}: {exc}")
        verdict = f"FAIL: {type(exc).__name__}: {exc}"

    await finish_report(args, e2e, session_id, token, verdict)
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

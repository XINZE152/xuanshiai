"""墨相师·WS 对话客户端。

复用既有的鉴权方式(``/auth/phone/login`` + ``/ai/consents/grant``),然后
打开 ``/api/v1/voice/moxiang-master`` WebSocket,顺序发送
``session_start`` + 每条 user ``text_message``,接收事件流。

收集到的所有事件会被记录到 :class:`ReplayResult`,供前端 trace / 评分器
使用。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
import websockets

logger = logging.getLogger(__name__)

# 单条 user 文本消息的最长等待时间(秒),超过则视为卡死。
TURN_TIMEOUT = 30.0
# session_start 等待时间(秒)。
SESSION_START_TIMEOUT = 15.0


@dataclass
class TurnEvent:
    """一轮 user 消息对应的所有 WS 事件。"""

    turn_no: int
    user_text: str
    client_turn_id: str
    events: list[dict[str, Any]] = field(default_factory=list)
    ai_reply_text: str = ""
    extraction_status: str | None = None
    task_id: str | None = None
    journey_percent: float | None = None
    journey_dimensions: dict[str, Any] = field(default_factory=dict)
    build_invite_id: str | None = None
    publish_ready: bool = False
    error: dict[str, Any] | None = None
    elapsed_ms: int = 0

    @property
    def passed(self) -> bool:
        return self.ai_reply_text != "" and self.error is None


@dataclass
class ReplayResult:
    """一次 WS 回放的完整事件轨迹。"""

    session_id: str | None
    subject: str
    journey_stage: str | None
    turns: list[TurnEvent] = field(default_factory=list)
    raw_frames: list[dict[str, Any]] = field(default_factory=list)
    started_at: float = 0.0
    finished_at: float = 0.0

    @property
    def elapsed_seconds(self) -> float:
        return round(self.finished_at - self.started_at, 2)

    def ai_replies(self) -> list[str]:
        return [t.ai_reply_text for t in self.turns]

    def to_frontend_trace(self, run_id: str) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "subject": self.subject,
            "journey_stage": self.journey_stage,
            "session_id": self.session_id,
            "elapsed_seconds": self.elapsed_seconds,
            "turns": [
                {
                    "turn": t.turn_no,
                    "user": t.user_text,
                    "ai": t.ai_reply_text,
                    "extraction_status": t.extraction_status,
                    "task_id": t.task_id,
                    "journey_percent": t.journey_percent,
                    "dimensions": t.journey_dimensions,
                    "build_invite_id": t.build_invite_id,
                    "publish_ready": t.publish_ready,
                    "ai_reply_time_ms": t.elapsed_ms,
                    "error": t.error,
                }
                for t in self.turns
            ],
        }


class MoxiangWSClient:
    """墨相师 WS 对话客户端。

    Args:
        base_url: 后端入口,默认 ``http://127.0.0.1:8000``。
        phone / code: 登录凭据。
        consent_version: 默认 ``profile-text-v1``。
        pre_issued_token: 可选的预签发 JWT(account1.json 内置)。
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        phone: str = "",
        code: str = "123456",
        consent_version: str = "profile-text-v1",
        pre_issued_token: str = "",
        pre_issued_user_id: int = 0,
        refresh_token: str = "",
        password: str = "",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.phone = phone
        self.code = code
        self.consent_version = consent_version
        self._token: str = ""
        self._user_id: int = 0
        self._pre_issued_token = pre_issued_token
        self._pre_issued_user_id = pre_issued_user_id
        self._refresh_token = refresh_token
        self._password = password

    async def login(self) -> str:
        """优先使用预签发 token,失败时回退 password / phone / refresh。"""
        if self._pre_issued_token:
            self._token = self._pre_issued_token
            self._user_id = self._pre_issued_user_id
            try:
                await self._fetch_user_id()
                if self._user_id > 0:
                    return self._token
            except Exception:
                self._token = ""
                self._user_id = 0

        # 第二优先级:test-login(开发/测试环境,需 password)
        if self._password:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{self.base_url}/api/v1/auth/test-login",
                    json={"phone": self.phone, "password": self._password},
                )
            if resp.status_code < 400:
                data = resp.json()
                self._token = data.get("access_token") or ""
                if self._token:
                    await self._fetch_user_id()
                    return self._token

        # 第三优先级:refresh_token
        if self._refresh_token:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{self.base_url}/api/v1/auth/refresh",
                    json={"refresh_token": self._refresh_token},
                )
            if resp.status_code < 400:
                data = resp.json()
                self._token = data.get("access_token") or ""
                if self._token:
                    await self._fetch_user_id()
                    return self._token

        # 最后回退手机号登录
        async def _try_login() -> dict:
            url = f"{self.base_url}/api/v1/auth/phone/login"
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    url,
                    json={"phone": self.phone, "code": self.code},
                )
            return {"status": resp.status_code, "body": resp.text}

        first = await _try_login()
        if first["status"] < 400:
            data = json.loads(first["body"])
            token = data.get("access_token") or data.get("token")
            if not token:
                raise RuntimeError(f"phone login response missing token: {data}")
            self._token = token
            await self._fetch_user_id()
            return token

        async with httpx.AsyncClient(timeout=10.0) as client:
            send_resp = await client.post(
                f"{self.base_url}/api/v1/auth/sms/send",
                json={"phone": self.phone},
            )
        if send_resp.status_code >= 400:
            raise RuntimeError(
                f"phone login failed and resend rejected: "
                f"{first['status']} {first['body'][:120]}; "
                f"sms/send={send_resp.status_code} {send_resp.text[:120]}"
            )
        second = await _try_login()
        if second["status"] >= 400:
            raise RuntimeError(
                f"phone login failed (after resend): {second['status']} {second['body'][:200]}"
            )
        data = json.loads(second["body"])
        token = data.get("access_token") or data.get("token")
        if not token:
            raise RuntimeError(f"phone login response missing token: {data}")
        self._token = token
        await self._fetch_user_id()
        return token

    async def _fetch_user_id(self) -> None:
        url = f"{self.base_url}/api/v1/auth/me"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {self._token}"})
        if resp.status_code >= 400:
            raise RuntimeError(f"me fetch failed: {resp.status_code} {resp.text[:200]}")
        data = resp.json()
        try:
            self._user_id = int(data.get("id") or data.get("user_id") or 0)
        except (TypeError, ValueError):
            self._user_id = 0

    async def grant_consent(self) -> None:
        """授予 ``profile_text_extract`` scope。

        先 GET ``/ai/consents`` 取得当前 ``privacy_revision``(后端校验),
        再 PUT 带正确 ``X-Expected-Privacy-Revision`` header。
        """
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Idempotency-Key": f"moxiang-loop-{int(time.time())}-{self._user_id}",
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            list_resp = await client.get(
                f"{self.base_url}/api/v1/ai/consents",
                headers=headers,
            )
        revision = "0"
        if list_resp.status_code < 400:
            try:
                data = list_resp.json()
                revision = str(
                    data.get("privacy_revision")
                    or data.get("policy_revision")
                    or "0"
                )
            except (ValueError, TypeError):
                revision = "0"

        url = f"{self.base_url}/api/v1/ai/consents/profile_text_extract"
        body = {
            "consent_version": self.consent_version,
            "policy_revision": "ai-policy-2026-08-07-v1",
        }
        headers["X-Expected-Privacy-Revision"] = revision
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.put(url, json=body, headers=headers)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"consent grant failed: {resp.status_code} {resp.text[:200]}"
            )

    async def replay(
        self,
        transcript: Any,
        *,
        reset_session: bool = True,
    ) -> ReplayResult:
        """跑一遍固定对话,返回完整事件流。

        ``reset_session=True`` 时,脚本应在调用前清空该 user 的画像状态。
        """
        await self.login()
        await self.grant_consent()

        if reset_session:
            await self._archive_active_session()

        ws_url = (
            f"{self.base_url.replace('http://', 'ws://').replace('https://', 'wss://')}"
            f"/api/v1/voice/moxiang-master?{urlencode({'token': self._token})}"
        )

        result = ReplayResult(
            session_id=None,
            subject=transcript.subject,
            journey_stage=None,
            started_at=time.monotonic(),
        )

        async with websockets.connect(ws_url, max_size=2**23) as ws:
            # session_start
            await ws.send(
                json.dumps(
                    {
                        "type": "session_start",
                        "mode": "moxiang_journey",
                        "subject": transcript.subject,
                        "consentVersion": self.consent_version,
                    },
                    ensure_ascii=False,
                )
            )
            # 等待 journey_ready
            await self._drain_until(ws, "journey_ready", result, SESSION_START_TIMEOUT)
            if result.journey_stage is None and result.session_id is None:
                # 兼容部分实现只返回 journey_ready.session_id
                pass

            # 顺序发送每条 user turn
            for idx, turn in enumerate(transcript.turns, start=1):
                if turn.get("role") != "user":
                    continue
                client_turn_id = f"loop-{int(time.time())}-{idx}"
                user_text = str(turn.get("text", "")).strip()
                if not user_text:
                    continue
                turn_event = TurnEvent(
                    turn_no=idx,
                    user_text=user_text,
                    client_turn_id=client_turn_id,
                )
                started = time.monotonic()
                await ws.send(
                    json.dumps(
                        {
                            "type": "text_message",
                            "clientTurnId": client_turn_id,
                            "text": user_text,
                        },
                        ensure_ascii=False,
                    )
                )
                await self._drain_turn(ws, turn_event, TURN_TIMEOUT)
                turn_event.elapsed_ms = int((time.monotonic() - started) * 1000)
                result.turns.append(turn_event)

        result.finished_at = time.monotonic()
        return result

    async def _archive_active_session(self) -> None:
        """把当前 active 会话归档,避免历史 turn 干扰。

        通过 ``POST /ai/profile-sessions/{id}/archive`` 调用;无 active 会话
        时容错。
        """
        url = f"{self.base_url}/api/v1/ai/moxiang/state"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                url, headers={"Authorization": f"Bearer {self._token}"}
            )
        if resp.status_code >= 400:
            return
        data = resp.json()
        # 兼容多种字段名
        session_id = (
            data.get("active_session_id")
            or data.get("personal_session_id")
            or data.get("session_id")
        )
        if not session_id:
            return
        archive_url = f"{self.base_url}/api/v1/ai/profile-sessions/{session_id}/archive"
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                archive_url,
                headers={"Authorization": f"Bearer {self._token}"},
            )

    async def _drain_until(
        self,
        ws: Any,
        target_type: str,
        result: ReplayResult,
        timeout: float,
    ) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                remaining = max(0.1, deadline - time.monotonic())
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except (asyncio.TimeoutError, TimeoutError):
                return
            try:
                frame = json.loads(raw)
            except (TypeError, ValueError):
                continue
            result.raw_frames.append({"at_session_start": True, **frame})
            if frame.get("type") == target_type:
                result.session_id = frame.get("session_id") or result.session_id
                result.journey_stage = frame.get("journey_stage") or result.journey_stage
                return
            if frame.get("type") == "error":
                logger.warning("ws_error_during_session_start: %s", frame)
                return

    async def _drain_turn(
        self,
        ws: Any,
        turn: TurnEvent,
        timeout: float,
    ) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = max(0.1, deadline - time.monotonic())
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except (asyncio.TimeoutError, TimeoutError):
                return
            try:
                frame = json.loads(raw)
            except (TypeError, ValueError):
                continue
            turn.events.append(frame)
            ftype = frame.get("type")
            if ftype == "ai_content":
                turn.ai_reply_text += str(frame.get("text") or "")
            elif ftype == "ai_reply":
                text = str(frame.get("text") or "")
                if not turn.ai_reply_text:
                    turn.ai_reply_text = text
                # opening=true 不算收尾,继续等
                if not frame.get("opening", False):
                    pass
            elif ftype == "extraction_status":
                turn.extraction_status = frame.get("status")
                turn.task_id = frame.get("task_id")
                if turn.extraction_status in ("completed", "failed"):
                    # extraction 已收尾,但 journey_progress 可能后到;继续等 1.5s
                    post_deadline = time.monotonic() + 1.5
                    while time.monotonic() < post_deadline:
                        try:
                            extra_raw = await asyncio.wait_for(
                                ws.recv(),
                                timeout=max(0.1, post_deadline - time.monotonic()),
                            )
                        except (asyncio.TimeoutError, TimeoutError):
                            return
                        try:
                            extra = json.loads(extra_raw)
                        except (TypeError, ValueError):
                            continue
                        turn.events.append(extra)
                        etype = extra.get("type")
                        if etype == "journey_progress":
                            turn.journey_percent = extra.get("overall_percent")
                            turn.journey_dimensions = extra.get("dimensions") or {}
                        elif etype == "build_invite":
                            turn.build_invite_id = extra.get("invite_id")
                        elif etype == "publish_ready":
                            turn.publish_ready = True
                        elif etype == "error":
                            turn.error = {
                                "code": extra.get("code"),
                                "message": extra.get("message"),
                            }
                    return
            elif ftype == "journey_progress":
                turn.journey_percent = frame.get("overall_percent")
                turn.journey_dimensions = frame.get("dimensions") or {}
            elif ftype == "build_invite":
                turn.build_invite_id = frame.get("invite_id")
            elif ftype == "publish_ready":
                turn.publish_ready = True
            elif ftype == "error":
                turn.error = {
                    "code": frame.get("code"),
                    "message": frame.get("message"),
                }


def write_transcript_jsonl(result: ReplayResult, path: Path) -> None:
    """把整次回放的事件流写入 JSONL(给前端 trace / 复核用)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for turn in result.turns:
            fh.write(
                json.dumps(
                    {
                        "turn": turn.turn_no,
                        "user": turn.user_text,
                        "client_turn_id": turn.client_turn_id,
                        "ai_reply": turn.ai_reply_text,
                        "extraction_status": turn.extraction_status,
                        "task_id": turn.task_id,
                        "journey_percent": turn.journey_percent,
                        "dimensions": turn.journey_dimensions,
                        "build_invite_id": turn.build_invite_id,
                        "publish_ready": turn.publish_ready,
                        "ai_reply_time_ms": turn.elapsed_ms,
                        "error": turn.error,
                    },
                    ensure_ascii=False,
                )
            )
            fh.write("\n")

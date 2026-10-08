"""连续语音的连接内确认边界，不持久化未确认转写。

现有 journey 幂等只保护已提交轮次，不能代表用户授权；这里仅保存一个
短期预览及有界确认回执，业务校验/审核/数据库幂等仍复用 journey。
"""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from app.services.ai.profile import AIInputError, normalize_profile_answer

TRANSCRIPT_TTL_SECONDS = 120
_KEY = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class TranscriptCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: StrictStr
    transcript_id: StrictStr = Field(min_length=1, max_length=128)
    client_turn_id: StrictStr = Field(min_length=1, max_length=128)
    session_id: StrictStr = Field(min_length=1, max_length=128)
    text: StrictStr | None = Field(default=None, max_length=2000)


def validate_client_turn_id(value: Any) -> str:
    if not isinstance(value, str) or not _KEY.fullmatch(value):
        raise AIInputError("client_turn_id 必须为 1–128 位字母、数字、横线或下划线")
    return value


def validate_turn_text(value: Any) -> str:
    if not isinstance(value, str):
        raise AIInputError("text 必须是字符串")
    return normalize_profile_answer(value)


@dataclass
class PendingTranscript:
    transcript_id: str
    client_turn_id: str
    session_id: str
    text: str
    expires_at: float

    def preview(self) -> dict[str, Any]:
        return {
            "type": "transcript_preview", "transcript_id": self.transcript_id,
            "client_turn_id": self.client_turn_id, "session_id": self.session_id,
            "text": self.text, "expires_in": TRANSCRIPT_TTL_SECONDS,
        }


class TranscriptBoundary:
    """每条已鉴权 WS 独占；断线直接丢弃，不允许重连确认旧预览。"""

    def __init__(self) -> None:
        self.pending: PendingTranscript | None = None
        self.receipts: dict[str, tuple[str, dict[str, Any]]] = {}
        self.used_keys: set[str] = set()

    def stage(self, text: str, client_turn_id: str, session_id: str) -> PendingTranscript:
        key = validate_client_turn_id(client_turn_id)
        if key in self.used_keys:
            raise AIInputError("本次录音标识已使用，请重新录音")
        if len(self.used_keys) >= 512:
            raise AIInputError("本次连接发言已达上限，请重新连接")
        self.used_keys.add(key)
        self.pending = PendingTranscript(
            uuid.uuid4().hex, key, session_id, validate_turn_text(text),
            time.monotonic() + TRANSCRIPT_TTL_SECONDS,
        )
        return self.pending

    def resolve(self, command: TranscriptCommand, session_id: str) -> PendingTranscript:
        pending = self.pending
        if (
            pending is None or pending.transcript_id != command.transcript_id
            or pending.client_turn_id != command.client_turn_id
            or pending.session_id != command.session_id or session_id != command.session_id
        ):
            raise AIInputError("TRANSCRIPT_STALE")
        if time.monotonic() >= pending.expires_at:
            self.pending = None
            raise AIInputError("TRANSCRIPT_EXPIRED")
        return pending

    def replay(self, command: TranscriptCommand, text: str, session_id: str) -> dict[str, Any] | None:
        saved = self.receipts.get(command.transcript_id)
        if saved is None:
            return None
        original, receipt = saved
        if (
            original != text
            or receipt.get("client_turn_id") != command.client_turn_id
            or receipt.get("session_id") != command.session_id
            or session_id != command.session_id
        ):
            raise AIInputError("TRANSCRIPT_CONFLICT")
        return receipt

    def complete(self, command: TranscriptCommand, text: str, receipt: dict[str, Any]) -> None:
        self.receipts[command.transcript_id] = (text, receipt)
        if len(self.receipts) > 128:
            self.receipts.pop(next(iter(self.receipts)))
        self.pending = None

    def clear(self) -> None:
        self.pending = None
        self.receipts.clear()
        self.used_keys.clear()

"""AIGateway.chat 只记元数据，不把消息原文送进审计。"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from app.services.ai.base import AITaskContext, ProviderError, ProviderErrorKind
from app.services.ai.gateway import AIGateway


class _Echo(BaseModel):
    reply: str


class _FakeProvider:
    def __init__(self, result: Any | Exception) -> None:
        self._result = result
        self.messages: list[dict[str, str]] | None = None

    async def chat(
        self, messages: list[dict[str, str]], *, json_mode: bool = False
    ) -> Any:
        self.messages = messages
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _context() -> AITaskContext:
    return AITaskContext(
        task_id="task-1",
        request_id="req-1",
        scene="legacy-chat",
        provider="fake",
        model="fake-model",
        prompt_version="legacy-chat-v1",
        schema_version="legacy-chat-v1",
    )


@pytest.mark.asyncio
async def test_chat_audit_records_scene_without_user_text(monkeypatch) -> None:
    captured: list[object] = []

    async def _capture(event: object) -> None:
        captured.append(event)

    monkeypatch.setattr(
        "app.services.ai.gateway.record_generation_audit", _capture
    )
    secret = "用户原文不应进审计"
    provider = _FakeProvider("好的")
    gateway = AIGateway(provider=provider)  # type: ignore[arg-type]
    outcome = await gateway.chat(
        _context(),
        [{"role": "user", "content": secret}],
    )

    assert outcome.result == "好的"
    assert captured
    event = captured[0]
    assert event.scene == "legacy-chat"
    assert event.prompt_version == "legacy-chat-v1"
    assert event.status == "succeeded"
    dumped = repr(event)
    assert secret not in dumped
    assert provider.messages is not None
    assert provider.messages[0]["content"] == secret


@pytest.mark.asyncio
async def test_chat_timeout_is_retryable(monkeypatch) -> None:
    async def _capture(event: object) -> None:
        return None

    monkeypatch.setattr(
        "app.services.ai.gateway.record_generation_audit", _capture
    )
    gateway = AIGateway(provider=_FakeProvider(TimeoutError()))  # type: ignore[arg-type]
    outcome = await gateway.chat(_context(), [{"role": "user", "content": "hi"}])
    assert outcome.result is None
    assert outcome.retryable is True
    assert outcome.error_code == "AI_TEMPORARILY_UNAVAILABLE"


@pytest.mark.asyncio
async def test_chat_schema_violation_is_not_retryable(monkeypatch) -> None:
    async def _capture(event: object) -> None:
        return None

    monkeypatch.setattr(
        "app.services.ai.gateway.record_generation_audit", _capture
    )
    gateway = AIGateway(provider=_FakeProvider("not-json"))  # type: ignore[arg-type]
    outcome = await gateway.chat(
        _context(),
        [{"role": "user", "content": "hi"}],
        response_type=_Echo,
    )
    assert outcome.result is None
    assert outcome.retryable is False
    assert outcome.error_code == "AI_INPUT_INVALID"


@pytest.mark.asyncio
async def test_chat_provider_schema_error_is_not_retryable(monkeypatch) -> None:
    async def _capture(event: object) -> None:
        return None

    monkeypatch.setattr(
        "app.services.ai.gateway.record_generation_audit", _capture
    )
    error = ProviderError(
        code="AI_INPUT_INVALID",
        message="bad",
        kind=ProviderErrorKind.NON_RETRYABLE,
    )
    gateway = AIGateway(provider=_FakeProvider(error))  # type: ignore[arg-type]
    outcome = await gateway.chat(_context(), [{"role": "user", "content": "hi"}])
    assert outcome.retryable is False
    assert outcome.error_code == "AI_INPUT_INVALID"

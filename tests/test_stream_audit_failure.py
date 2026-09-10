from __future__ import annotations

from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.base import AITaskContext
from app.services.ai.gateway import AIGateway
from app.services.voice.master_orchestrator import MoxiangMasterOrchestrator


def _orchestrator() -> MoxiangMasterOrchestrator:
    return MoxiangMasterOrchestrator(
        ai_gateway=MagicMock(),
        voice_gateway=MagicMock(),
    )


@pytest.mark.asyncio
async def test_master_provider_initialization_failure_is_audited_without_name_error() -> None:
    audit = AsyncMock()
    with patch(
        "app.services.voice.master_orchestrator.get_provider",
        side_effect=RuntimeError("provider unavailable"),
    ), patch(
        "app.services.voice.master_orchestrator.record_generation_audit",
        audit,
    ), patch(
        "app.services.voice.master_orchestrator.settings"
    ) as settings:
        settings.ai_provider = "broken"
        settings.ai_retention_policy_version = "policy"
        with pytest.raises(RuntimeError, match="provider unavailable"):
            async for _ in _orchestrator().stream_reply("hello", request_id="req"):
                pass

    event = audit.await_args.args[0]
    assert event.provider == "broken"
    assert event.model is None
    assert event.status == "failed"
    assert event.error_code == "RuntimeError"


@pytest.mark.asyncio
async def test_master_audit_failure_does_not_mask_provider_failure() -> None:
    async def fail_stream(*args, **kwargs) -> AsyncIterator[tuple[str, str]]:
        raise ValueError("provider failed")
        yield ("finish", "stop")

    provider = MagicMock()
    provider.stream_chat = fail_stream
    with patch(
        "app.services.voice.master_orchestrator.get_provider", return_value=provider
    ), patch(
        "app.services.voice.master_orchestrator.record_generation_audit",
        AsyncMock(side_effect=OSError("audit down")),
    ), patch(
        "app.services.voice.master_orchestrator.settings"
    ) as settings:
        settings.ai_provider = "mock"
        settings.ai_retention_policy_version = "policy"
        with pytest.raises(ValueError, match="provider failed"):
            async for _ in _orchestrator().stream_reply("hello"):
                pass


@pytest.mark.asyncio
async def test_master_early_close_is_not_a_success_and_closes_provider_stream() -> None:
    class _ClosableStream:
        def __init__(self) -> None:
            self.closed = False
            self.items = iter([("content", "partial")])

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self.items)
            except StopIteration:
                raise StopAsyncIteration

        async def aclose(self) -> None:
            self.closed = True

    stream = _ClosableStream()
    provider = MagicMock()
    provider.stream_chat.return_value = stream
    audit = AsyncMock()
    orchestrator = _orchestrator()
    with patch(
        "app.services.voice.master_orchestrator.get_provider", return_value=provider
    ), patch(
        "app.services.voice.master_orchestrator.record_generation_audit", audit
    ), patch(
        "app.services.voice.master_orchestrator.settings"
    ) as settings:
        settings.ai_provider = "mock"
        settings.ai_retention_policy_version = "policy"
        reply_stream = orchestrator.stream_reply("hello")
        assert await reply_stream.__anext__() == ("content", "partial")
        await reply_stream.aclose()

    assert stream.closed is True
    event = audit.await_args.args[0]
    assert event.status == "cancelled"
    assert event.error_code == "AI_CANCELLED"


@pytest.mark.asyncio
async def test_master_close_cancellation_preserves_provider_error_and_audits() -> None:
    class _BrokenClosableStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise ValueError("provider failed")

        async def aclose(self) -> None:
            raise __import__("asyncio").CancelledError()

    provider = MagicMock()
    provider.stream_chat.return_value = _BrokenClosableStream()
    audit = AsyncMock()
    with patch(
        "app.services.voice.master_orchestrator.get_provider", return_value=provider
    ), patch(
        "app.services.voice.master_orchestrator.record_generation_audit", audit
    ), patch(
        "app.services.voice.master_orchestrator.settings"
    ) as settings:
        settings.ai_provider = "mock"
        settings.ai_retention_policy_version = "policy"
        with pytest.raises(ValueError, match="provider failed"):
            async for _ in _orchestrator().stream_reply("hello"):
                pass

    event = audit.await_args.args[0]
    assert event.status == "failed"
    assert event.error_code == "ValueError"


class _SlowProvider:
    async def stream_chat(self, messages, *, json_mode=False):
        yield ("content", "first")
        await __import__("asyncio").sleep(0.05)
        yield ("finish", "stop")


@pytest.mark.asyncio
async def test_gateway_stream_timeout_is_not_a_success_audit() -> None:
    audit = AsyncMock()
    gateway = AIGateway(provider=_SlowProvider(), timeout_seconds=0.01)
    context = AITaskContext(
        task_id="task", request_id="req", scene="scene", provider="mock"
    )
    with patch("app.services.ai.gateway.record_generation_audit", audit):
        with pytest.raises(TimeoutError):
            async for _ in gateway.stream_chat(context, []):
                pass

    event = audit.await_args.args[0]
    assert event.status == "timeout"
    assert event.error_code == "AI_TIMEOUT"


@pytest.mark.asyncio
async def test_gateway_stream_provider_failure_is_failed_audit() -> None:
    class _FailingStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise ValueError("bad stream")

        async def aclose(self) -> None:
            self.closed = True

        closed = False

    class _FailingProvider:
        def __init__(self) -> None:
            self.stream = _FailingStream()

        def stream_chat(self, messages, *, json_mode=False):
            return self.stream

    audit = AsyncMock()
    provider = _FailingProvider()
    gateway = AIGateway(provider=provider, timeout_seconds=1)
    context = AITaskContext(
        task_id="task", request_id="req", scene="scene", provider="mock"
    )
    with patch("app.services.ai.gateway.record_generation_audit", audit):
        with pytest.raises(ValueError, match="bad stream"):
            async for _ in gateway.stream_chat(context, []):
                pass

    event = audit.await_args.args[0]
    assert event.status == "failed"
    assert event.error_code == "ValueError"
    assert provider.stream.closed is True


@pytest.mark.asyncio
async def test_gateway_stream_closes_provider_iterator_on_success() -> None:
    class _ClosableStream:
        def __init__(self) -> None:
            self.closed = False
            self.items = iter([("content", "ok"), ("finish", "stop")])

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self.items)
            except StopIteration:
                raise StopAsyncIteration

        async def aclose(self) -> None:
            self.closed = True

    stream = _ClosableStream()
    provider = MagicMock()
    provider.stream_chat.return_value = stream
    gateway = AIGateway(provider=provider, timeout_seconds=1)
    context = AITaskContext(
        task_id="task", request_id="req", scene="scene", provider="mock"
    )
    with patch("app.services.ai.gateway.record_generation_audit", AsyncMock()):
        items = [item async for item in gateway.stream_chat(context, [])]

    assert items[-1] == ("finish", "stop")
    assert stream.closed is True


@pytest.mark.asyncio
async def test_gateway_stream_cancellation_is_not_a_success_audit() -> None:
    class _BlockingProvider:
        async def stream_chat(self, messages, *, json_mode=False):
            await __import__("asyncio").sleep(10)
            yield ("finish", "stop")

    audit = AsyncMock()
    gateway = AIGateway(provider=_BlockingProvider(), timeout_seconds=30)
    context = AITaskContext(
        task_id="task", request_id="req", scene="scene", provider="mock"
    )

    async def consume() -> None:
        async for _ in gateway.stream_chat(context, []):
            pass

    with patch("app.services.ai.gateway.record_generation_audit", audit):
        task = __import__("asyncio").create_task(consume())
        await __import__("asyncio").sleep(0)
        task.cancel()
        with pytest.raises(__import__("asyncio").CancelledError):
            await task

    event = audit.await_args.args[0]
    assert event.status == "cancelled"
    assert event.error_code == "AI_CANCELLED"

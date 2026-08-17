from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core.config import Settings
from app.schemas.ai_avatar import AiAvatarMessageRequest, AiAvatarProfileResponse
from app.services import ai_avatar


def test_message_request_trims_and_limits_content() -> None:
    assert AiAvatarMessageRequest(content="  你   喜欢什么  ").content == "你 喜欢什么"
    with pytest.raises(ValidationError):
        AiAvatarMessageRequest(content="   ")
    with pytest.raises(ValidationError):
        AiAvatarMessageRequest(content="问" * 301)


def test_production_ai_provider_requires_https() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            environment="production",
            auto_init_db=False,
            sms_provider="disabled",
            wechat_provider="wechat",
            wechat_payment_mode="real",
            ai_provider="openai_compatible",
            ai_base_url="http://127.0.0.1:11434/v1",
            ai_model="local-model",
        )


def test_provider_reply_parser_rejects_unknown_payload() -> None:
    assert ai_avatar._extract_provider_reply(
        {"choices": [{"message": {"content": "  回答内容  "}}]}
    ) == "回答内容"
    with pytest.raises(ai_avatar.AiProviderError):
        ai_avatar._extract_provider_reply({"choices": []})


def test_naive_database_timestamp_is_treated_as_utc() -> None:
    assert ai_avatar._timestamp_ms(datetime(1970, 1, 1)) == 0  # noqa: DTZ001
    assert ai_avatar._timestamp_ms(datetime(1970, 1, 1, tzinfo=UTC)) == 0


@pytest.mark.asyncio
async def test_quota_refund_failure_does_not_mask_original_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_refund(_: str) -> None:
        raise HTTPException(503, detail="redis unavailable")

    monkeypatch.setattr(ai_avatar, "refund_daily", failing_refund)
    await ai_avatar._refund_quota_safely("ai-avatar:test")


@pytest.mark.asyncio
async def test_provider_receives_only_server_built_public_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("Authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Ta 喜欢徒步，这是 AI 回答。"}}]},
        )

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def client_factory(**kwargs: object) -> httpx.AsyncClient:
        return original_client(transport=transport, timeout=kwargs.get("timeout"))

    monkeypatch.setattr(ai_avatar.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(ai_avatar.settings, "ai_provider", "openai_compatible")
    monkeypatch.setattr(ai_avatar.settings, "ai_base_url", "https://provider.example/v1")
    monkeypatch.setattr(ai_avatar.settings, "ai_model", "test-model")
    monkeypatch.setattr(ai_avatar.settings, "ai_api_key", None)
    context = ai_avatar.AiAvatarContext(
        profile=AiAvatarProfileResponse(
            id=2,
            name="测试用户",
            interests=["徒步"],
            restricted=False,
        ),
        public_posts=(),
    )

    reply = await ai_avatar.call_ai_provider(context, [], "Ta 喜欢什么？")

    assert reply == "Ta 喜欢徒步，这是 AI 回答。"
    assert captured["authorization"] is None
    payload = captured["payload"]
    assert isinstance(payload, dict)
    system_prompt = payload["messages"][0]["content"]
    assert "测试用户" in system_prompt
    assert "徒步" in system_prompt
    assert "手机号" in system_prompt
    assert payload["stream"] is False


@pytest.mark.asyncio
async def test_disabled_provider_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai_avatar.settings, "ai_provider", "disabled")
    context = ai_avatar.AiAvatarContext(
        profile=AiAvatarProfileResponse(id=2, name="测试用户"),
        public_posts=(),
    )
    with pytest.raises(HTTPException) as exc_info:
        await ai_avatar.call_ai_provider(context, [], "你好")
    assert exc_info.value.status_code == 503


def test_ai_avatar_routes_and_tables_are_declared() -> None:
    from app.api.routes.ai_avatar import router

    paths = {route.path for route in router.routes}
    assert "/ai-avatars/{target_user_id}/profile" in paths
    assert "/ai-avatars/{target_user_id}/messages" in paths
    assert "/ai-avatars/{target_user_id}/conversations" in paths

    setup = Path("database_setup_marriage.py").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS `ai_avatar_conversation`" in setup
    assert "CREATE TABLE IF NOT EXISTS `ai_avatar_message`" in setup
    assert "fk_ai_avatar_message_conversation_id" in setup

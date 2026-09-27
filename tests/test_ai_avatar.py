"""AI 分身的公开记忆边界与 HTTP 契约测试。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core.config import Settings
from app.main import app
from app.schemas.ai_avatar import (
    AiAvatarMessageRequest,
    AiAvatarOwnerAnswerCreateRequest,
    AiAvatarOwnerAnswerRequest,
    AiAvatarProfileResponse,
    AvatarReplyRequest,
)
from app.services import ai_avatar
from app.services.ai.flags import AiFeature, AiFeatureDisabledError, require_ai_feature
from app.services.ai.memory.consumers import (
    SanitizedMemoryContext,
    SanitizedMemoryEntry,
)

def _public_context() -> SanitizedMemoryContext:
    return SanitizedMemoryContext(
        function_key="persona_context",
        purpose="session_context",
        owner_user_id=42,
        entries_by_subject=(
            (
                "personal",
                (
                    SanitizedMemoryEntry(
                        field_key="interest_tags",
                        value=["hiking"],
                        value_type="string_list",
                        stability=0.9,
                        importance=0.8,
                        constraint_type=None,
                        claim_id="synthetic-public-claim",
                        projection_version=1,
                    ),
                ),
            ),
        ),
    )


def test_avatar_route_is_exposed_in_public_openapi() -> None:
    operation = app.openapi()["paths"]["/api/v1/ai/avatar/{target_user_id}/reply"]["post"]
    assert operation["responses"]["201"]
    assert operation["requestBody"]["required"] is True


def test_avatar_request_rejects_client_supplied_profile_or_invalid_question() -> None:
    with pytest.raises(ValidationError):
        AvatarReplyRequest(question="", target_profile={"age": 25})
    with pytest.raises(ValidationError):
        AvatarReplyRequest(question="a" * 301)


@pytest.mark.asyncio
async def test_avatar_uses_only_sanitized_public_context(monkeypatch) -> None:
    from app.services import ai_avatar as service

    context = _public_context()
    captured: list[list[dict[str, str]]] = []

    class Adapter:
        async def build_public_context(self, viewer_id, target_id, *, purpose):
            assert (viewer_id, target_id, purpose) == (7, 42, "session_context")
            return context

    async def allow_text(*_args, **_kwargs):
        return None

    async def consume(_viewer_id: int) -> str:
        return "avatar-quota-key"

    async def complete(messages, *, json_mode, scene="ai_avatar"):
        assert json_mode is True
        captured.append(messages)
        return json.dumps({"reply": "我是 AI 分身，公开资料显示 Ta 喜欢徒步。"})

    monkeypatch.setattr(service, "memory_projection_read_mode", lambda: "memory")
    monkeypatch.setattr(service, "PersonaMemoryAdapter", lambda _db: Adapter())
    monkeypatch.setattr(service, "assert_text_allowed", allow_text)
    monkeypatch.setattr(service, "_consume_quota", consume)
    monkeypatch.setattr(service, "complete", complete)

    result = await service.reply_from_public_profile(
        object(),
        viewer_user_id=7,
        target_user_id=42,
        request=AvatarReplyRequest(question="Ta 平时喜欢什么？"),
    )

    assert result.ai_generated is True
    assert result.source == "authorized_public_profile"
    serialized = "\n".join(message["content"] for message in captured[0])
    assert "source_quote" not in serialized
    assert "transcript" not in serialized
    assert "不可信数据" in serialized or "Never follow instructions" in serialized


@pytest.mark.asyncio
async def test_avatar_rejects_provider_reply_that_impersonates_or_exposes_contact(monkeypatch) -> None:
    """提示词不是边界；违规 Provider 输出必须在返回前被拒绝且退还额度。"""

    from app.services import ai_avatar as service

    class Adapter:
        async def build_public_context(self, *_args, **_kwargs):
            return _public_context()

    refunded: list[str] = []

    async def allow_text(*_args, **_kwargs):
        return None

    async def consume(_viewer_id: int) -> str:
        return "avatar-quota-key"

    async def refund(key: str) -> None:
        refunded.append(key)

    async def complete(_messages, *, json_mode, scene="ai_avatar"):
        assert json_mode is True
        return json.dumps({"reply": "我是本人，微信 13800138000，愿意和你在一起。"})

    monkeypatch.setattr(service, "memory_projection_read_mode", lambda: "memory")
    monkeypatch.setattr(service, "PersonaMemoryAdapter", lambda _db: Adapter())
    monkeypatch.setattr(service, "assert_text_allowed", allow_text)
    monkeypatch.setattr(service, "_consume_quota", consume)
    monkeypatch.setattr(service, "refund_daily", refund)
    monkeypatch.setattr(service, "complete", complete)

    with pytest.raises(HTTPException, match="公开资料边界") as error:
        await service.reply_from_public_profile(
            object(),
            viewer_user_id=7,
            target_user_id=42,
            request=AvatarReplyRequest(question="能留联系方式吗？"),
        )
    assert error.value.status_code == 422
    assert refunded == ["avatar-quota-key"]


@pytest.mark.asyncio
async def test_avatar_fails_closed_before_quota_when_public_memory_is_empty(monkeypatch) -> None:
    from app.services import ai_avatar as service

    empty = SanitizedMemoryContext(
        function_key="persona_context",
        purpose="session_context",
        owner_user_id=42,
        entries_by_subject=(("personal", ()),),
    )

    class Adapter:
        async def build_public_context(self, *_args, **_kwargs):
            return empty

    async def allow_text(*_args, **_kwargs):
        return None

    async def must_not_consume(*_args, **_kwargs):
        raise AssertionError("empty public context must not consume quota")

    monkeypatch.setattr(service, "memory_projection_read_mode", lambda: "memory")
    monkeypatch.setattr(service, "PersonaMemoryAdapter", lambda _db: Adapter())
    monkeypatch.setattr(service, "assert_text_allowed", allow_text)
    monkeypatch.setattr(service, "_consume_quota", must_not_consume)

    with pytest.raises(HTTPException, match="公开资料暂时不可用") as error:
        await service.reply_from_public_profile(
            object(),
            viewer_user_id=7,
            target_user_id=42,
            request=AvatarReplyRequest(question="Ta 平时喜欢什么？"),
        )
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_avatar_rejects_non_memory_mode_before_reading_profile(monkeypatch) -> None:
    from app.services import ai_avatar as service

    monkeypatch.setattr(service, "memory_projection_read_mode", lambda: "shadow")

    # 默认 503 之二（记忆投影未开启）：状态码 503、detail 固定为
    # 「AI分身记忆服务尚未就绪」。
    with pytest.raises(HTTPException, match="记忆服务尚未就绪") as error:
        await service.reply_from_public_profile(
            object(),
            viewer_user_id=7,
            target_user_id=42,
            request=AvatarReplyRequest(question="Ta 平时喜欢什么？"),
        )
    assert error.value.status_code == 503
    assert error.value.detail == "AI分身记忆服务尚未就绪"


def test_message_request_trims_and_limits_content() -> None:
    assert AiAvatarMessageRequest(content="  你   喜欢什么  ").content == "你 喜欢什么"
    with pytest.raises(ValidationError):
        AiAvatarMessageRequest(content="   ")
    with pytest.raises(ValidationError):
        AiAvatarMessageRequest(content="问" * 301)


def test_owner_answer_requests_reject_blank_text() -> None:
    with pytest.raises(ValidationError):
        AiAvatarOwnerAnswerRequest(answer="   ")
    with pytest.raises(ValidationError):
        AiAvatarOwnerAnswerCreateRequest(question="   ", answer="有效回答")
    payload = AiAvatarOwnerAnswerCreateRequest(question="  你的兴趣？ ", answer="  阅读和散步 ")
    assert payload.question == "你的兴趣？"
    assert payload.answer == "阅读和散步"


def _production_settings_kwargs(**overrides):
    """生产环境最小合规夹具：先满足与 AI 无关的生产门禁（直播/短信/微信
    mock 禁令、SECRET_KEY、DEBUG/DOCS），让断言能精确落在 AI 门禁分支。"""
    kwargs = dict(
        _env_file=None,
        environment="production",
        auto_init_db=False,
        live_provider="tencent",
        sms_provider="disabled",
        wechat_provider="wechat",
        wechat_payment_mode="real",
        secret_key="p" * 40,
        debug=False,
        docs_enabled=False,
    )
    kwargs.update(overrides)
    return kwargs


def test_production_ai_provider_requires_https() -> None:
    """仅 avatar 开启（legacy 十项开关全关）时，provider 专项检查被跳过，
    非 HTTPS base_url 必须精确命中 :449-455 的 HTTPS 分支。"""
    with pytest.raises(ValidationError, match="HTTPS"):
        Settings(
            **_production_settings_kwargs(
                ai_policy_approved=True,
                ai_provider_approved=True,
                ai_retention_policy_version="v1",
                ai_avatar_provider="openai_compatible",
                ai_avatar_base_url="http://127.0.0.1:11434/v1",
                ai_avatar_model="local-model",
            )
        )


def test_production_avatar_only_without_approvals_fails_closed() -> None:
    """负向审批门：production + 仅 avatar 开 + 无三道审批 → 启动即拒。"""
    with pytest.raises(ValidationError, match="生产环境启用 AI 功能必须同时满足"):
        Settings(
            **_production_settings_kwargs(
                ai_avatar_provider="openai_compatible",
                ai_avatar_base_url="https://avatar.example/v1",
                ai_avatar_model="avatar-model",
            )
        )


def test_production_avatar_only_with_full_gates_constructs_and_keeps_mock_legacy_provider() -> None:
    """正向锚：production + 仅 avatar 开 + 三道审批齐 + 合规 SECRET_KEY +
    DEBUG/DOCS 关 → 构造成功；legacy ai_provider 保持默认 mock 不被误拦。"""
    settings = Settings(
        **_production_settings_kwargs(
            ai_policy_approved=True,
            ai_provider_approved=True,
            ai_retention_policy_version="v1",
            ai_avatar_provider="openai_compatible",
            ai_avatar_base_url="https://avatar.example/v1",
            ai_avatar_model="avatar-model",
        )
    )
    assert settings.ai_avatar_provider == "openai_compatible"
    assert settings.ai_provider == "mock"
    assert settings.ai_enabled is False


def test_production_legacy_ai_enabled_still_rejects_mock_provider() -> None:
    """legacy 不放松：production + ai_enabled=True + ai_provider=mock 仍拒。"""
    with pytest.raises(ValidationError, match="生产环境禁止使用 mock AI Provider"):
        Settings(
            **_production_settings_kwargs(
                ai_policy_approved=True,
                ai_provider_approved=True,
                ai_retention_policy_version="v1",
                ai_enabled=True,
                ai_provider="mock",
            )
        )


def test_production_legacy_deepseek_key_check_order_preserved() -> None:
    """legacy 顺序：production + ai_enabled=True + deepseek 无 key 仍先拒 key。"""
    with pytest.raises(
        ValidationError, match="生产环境启用 DeepSeek AI 必须配置 AI_DEEPSEEK_API_KEY"
    ):
        Settings(
            **_production_settings_kwargs(
                ai_policy_approved=True,
                ai_provider_approved=True,
                ai_retention_policy_version="v1",
                ai_enabled=True,
                ai_provider="deepseek",
            )
        )


def test_production_modern_stack_profile_enabled_still_rejects_mock_provider() -> None:
    """现代栈卫兵宽度锚：仅开 ai_profile_enabled（十项卫兵内）+ mock 仍拒，
    防卫兵再度收窄把启动拦截退化成运行时 503。"""
    with pytest.raises(ValidationError, match="生产环境禁止使用 mock AI Provider"):
        Settings(
            **_production_settings_kwargs(
                ai_policy_approved=True,
                ai_provider_approved=True,
                ai_retention_policy_version="v1",
                ai_profile_enabled=True,
                ai_provider="mock",
            )
        )


@pytest.mark.asyncio
async def test_avatar_runtime_gate_requires_master_and_production_approvals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """runtime ⑥：master 开（+审批齐）→ require_ai_feature(AVATAR) 通过；
    master 关或生产审批缺失 → AiFeatureDisabledError。"""
    dev_settings = Settings(
        _env_file=None,
        environment="testing",
        ai_master_enabled=True,
        ai_avatar_provider="openai_compatible",
        ai_avatar_base_url="https://avatar.example/v1",
        ai_avatar_model="avatar-model",
    )
    require_ai_feature(AiFeature.AVATAR, dev_settings)  # 不抛即通过

    master_off = Settings(
        _env_file=None,
        environment="testing",
        ai_master_enabled=False,
        ai_avatar_provider="openai_compatible",
        ai_avatar_base_url="https://avatar.example/v1",
        ai_avatar_model="avatar-model",
    )
    with pytest.raises(AiFeatureDisabledError):
        require_ai_feature(AiFeature.AVATAR, master_off)

    # master 开 → 十项卫兵生效，生产构造须同时满足 legacy provider 专项
    # 检查（deepseek 需配 key），这正是启动 fail-closed 的预期行为。
    production_ok = Settings(
        **_production_settings_kwargs(
            ai_policy_approved=True,
            ai_provider_approved=True,
            ai_retention_policy_version="v1",
            ai_master_enabled=True,
            ai_provider="deepseek",
            ai_deepseek_api_key="k" * 40,
            ai_avatar_provider="openai_compatible",
            ai_avatar_base_url="https://avatar.example/v1",
            ai_avatar_model="avatar-model",
        )
    )
    require_ai_feature(AiFeature.AVATAR, production_ok)  # 不抛即通过

    # 运行时复查层：启动后审批被撤销（无法通过启动校验的态势只能这样模拟）
    # 也必须在运行时 fail closed。
    with monkeypatch.context() as m:
        m.setattr(production_ok, "ai_policy_approved", False)
        with pytest.raises(AiFeatureDisabledError):
            require_ai_feature(AiFeature.AVATAR, production_ok)


def test_ai_avatar_defaults_do_not_inherit_general_ai_provider() -> None:
    settings = Settings(
        _env_file=None,
        environment="testing",
        ai_provider="deepseek",
        ai_deepseek_base_url="https://legacy.example/v1",
        ai_deepseek_model="legacy-model",
    )

    assert settings.ai_avatar_provider == "disabled"
    assert settings.ai_avatar_base_url is None
    assert settings.ai_avatar_model is None


def test_provider_reply_parser_rejects_unknown_payload() -> None:
    assert ai_avatar._extract_provider_reply(
        {"choices": [{"message": {"content": "  回答内容  "}}]}
    ) == "回答内容"
    with pytest.raises(ai_avatar.AiProviderError):
        ai_avatar._extract_provider_reply({"choices": []})


def test_naive_database_timestamp_is_treated_as_utc() -> None:
    assert ai_avatar._timestamp_ms(datetime(1970, 1, 1)) == 0  # noqa: DTZ001
    assert ai_avatar._timestamp_ms(datetime(1970, 1, 1, tzinfo=UTC)) == 0


def test_conversation_schema_compatibility_uses_existing_status_contract() -> None:
    assert ai_avatar._conversation_status_value({"status": "tinyint"}) == 1
    assert ai_avatar._conversation_status_value({"status": "varchar(16)"}) == "active"


def test_owner_answers_are_explicit_and_sorted_into_conversation() -> None:
    profile = AiAvatarProfileResponse(id=2, name="娴嬭瘯鐢ㄦ埛", avatar="avatar")
    rows = [
        {
            "id": 1,
            "role": "user",
            "content": "闂",
            "category": "general",
            "created_at": datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        },
        {
            "id": 2,
            "role": "assistant",
            "content": "AI 鍥炵瓟",
            "category": "general",
            "created_at": datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        },
    ]
    messages = ai_avatar._map_rows(
        rows,
        profile,
        [
            {
                "id": 7,
                "answer": "本人补充",
                "category": "general",
                "answered_at": datetime(2026, 1, 1, 0, 2, tzinfo=UTC),
            }
        ],
    )
    assert [message.source for message in messages] == [
        "system",
        "user",
        "real-ai",
        "owner-answer",
    ]
    assert messages[-1].id == -7


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
    # call_ai_provider 现在先过 require_ai_feature(AVATAR)：master 总闸必须
    # 显式打开，测试不得依赖本地 .env 的开发配置。
    monkeypatch.setattr(ai_avatar.settings, "ai_master_enabled", True)
    monkeypatch.setattr(ai_avatar.settings, "ai_avatar_provider", "openai_compatible")
    monkeypatch.setattr(ai_avatar.settings, "ai_avatar_base_url", "https://provider.example/v1")
    monkeypatch.setattr(ai_avatar.settings, "ai_avatar_model", "test-model")
    monkeypatch.setattr(ai_avatar.settings, "ai_avatar_api_key", None)
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
    """默认 503 之一（AI 门禁）：provider disabled 在 require_ai_feature 即被
    拦下，状态码 503、detail 固定为「AI服务未启用」。"""
    monkeypatch.setattr(ai_avatar.settings, "ai_avatar_provider", "disabled")
    context = ai_avatar.AiAvatarContext(
        profile=AiAvatarProfileResponse(id=2, name="测试用户"),
        public_posts=(),
    )
    with pytest.raises(HTTPException) as exc_info:
        await ai_avatar.call_ai_provider(context, [], "你好")
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "AI服务未启用"


@pytest.mark.asyncio
async def test_master_switch_off_fails_closed_with_stable_503_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """默认 503 之一（AI 门禁）的 master 关触发形态：状态码 503、detail
    固定为「AI服务未启用」，与 provider 未配置/生产审批未通过共用文案。"""
    monkeypatch.setattr(ai_avatar.settings, "ai_master_enabled", False)
    monkeypatch.setattr(ai_avatar.settings, "ai_avatar_provider", "openai_compatible")
    context = ai_avatar.AiAvatarContext(
        profile=AiAvatarProfileResponse(id=2, name="测试用户"),
        public_posts=(),
    )
    with pytest.raises(HTTPException) as exc_info:
        await ai_avatar.call_ai_provider(context, [], "你好")
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "AI服务未启用"


def test_ai_avatar_routes_and_tables_are_declared() -> None:
    from app.api.routes.ai_avatar import router

    paths = {route.path for route in router.routes}
    assert "/ai-avatars/me/dashboard" in paths
    assert "/ai-avatars/me/questions" in paths
    assert "/ai-avatars/me/questions/{question_id}/answer" in paths
    assert "/ai-avatars/me/questions/{question_id}" in paths
    assert "/ai-avatars/me/answers/{answer_id}" in paths
    assert "/ai-avatars/{target_user_id}/profile" in paths
    assert "/ai-avatars/{target_user_id}/messages" in paths
    assert "/ai-avatars/{target_user_id}/conversations" in paths

    setup = Path("database_setup_marriage.py").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS `ai_avatar_conversation`" in setup
    assert "CREATE TABLE IF NOT EXISTS `ai_avatar_message`" in setup
    assert "CREATE TABLE IF NOT EXISTS `ai_avatar_owner_qa`" in setup
    assert "uk_ai_avatar_owner_question" in setup
    assert "fk_ai_avatar_message_conversation_id" in setup
    assert 'ref_table="ai_avatar_conversation"' in setup
    assert 'on_delete="SET NULL"' in setup


def test_ai_avatar_message_exposes_optional_bounded_idempotency_key() -> None:
    from app.main import app

    operation = app.openapi()["paths"]["/api/v1/ai-avatars/{target_user_id}/messages"]["post"]
    header = next(
        item for item in operation["parameters"] if item["name"] == "Idempotency-Key"
    )
    assert header["in"] == "header"
    assert header["required"] is False
    schema = next(
        item
        for item in header["schema"].get("anyOf", [header["schema"]])
        if item.get("type") == "string"
    )
    assert schema["minLength"] == 1
    assert schema["maxLength"] == 128

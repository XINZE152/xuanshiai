"""AI mock 降级可观测化回归测试。

``ai_provider.complete`` 在 ``ai_enabled=false`` 时的回退判定是唯一实现：
``not settings.ai_enabled and settings.is_test_mode`` 且
``ai_allow_mock_fallback=true`` 才回退本地 mock，否则 503「AI服务未启用」。
``ai_advisor``（model_name=mock-fallback）与 ``ai_assistant``（polish 留痕）
引用同一布尔式，不得另行发明判定。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.services import ai_provider

_GENERIC_MESSAGES = [
    {"role": "system", "content": "你是婚恋沟通助手。"},
    {"role": "user", "content": "帮我看看怎么回复。"},
]


def _disable_ai(monkeypatch: pytest.MonkeyPatch, *, allow: bool) -> None:
    monkeypatch.setattr(ai_provider.settings, "ai_enabled", False)
    monkeypatch.setattr(ai_provider.settings, "environment", "testing")
    monkeypatch.setattr(ai_provider.settings, "ai_allow_mock_fallback", allow)


@pytest.mark.asyncio
async def test_ai_allow_mock_fallback_false_fails_closed_with_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """flag=false：dev/testing 的 mock 回退改为 503「AI服务未启用」。"""
    _disable_ai(monkeypatch, allow=False)
    with pytest.raises(HTTPException) as exc_info:
        await ai_provider.complete(_GENERIC_MESSAGES, json_mode=False)
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "AI服务未启用"


@pytest.mark.asyncio
async def test_default_flag_keeps_existing_mock_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """默认 flag=true：既有 dev/testing mock 契约零改动。"""
    _disable_ai(monkeypatch, allow=True)
    result = await ai_provider.complete(_GENERIC_MESSAGES, json_mode=False)
    assert result == "我可以帮你梳理这段聊天，并给出更具体的沟通建议。"


@pytest.mark.asyncio
async def test_mock_fallback_emits_warning_with_scene_and_request_id(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """回退发生时必须输出含 scene/request_id 的 warning，不静默降级。"""
    _disable_ai(monkeypatch, allow=True)
    with caplog.at_level("WARNING", logger="app.services.ai_provider"):
        await ai_provider.complete(
            _GENERIC_MESSAGES,
            json_mode=False,
            request_id="req-mock-observability",
            scene="advisor",
        )
    warnings = [record for record in caplog.records if record.levelname == "WARNING"]
    assert any(
        "ai_mock_fallback" in record.getMessage()
        and "scene=advisor" in record.getMessage()
        and "req-mock-observability" in record.getMessage()
        for record in warnings
    )


@pytest.mark.asyncio
async def test_production_environment_never_falls_back_to_mock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非 test_mode（production/staging）不受 flag 影响，一律 503。"""
    monkeypatch.setattr(ai_provider.settings, "ai_enabled", False)
    monkeypatch.setattr(ai_provider.settings, "environment", "production")
    monkeypatch.setattr(ai_provider.settings, "ai_allow_mock_fallback", True)
    with pytest.raises(HTTPException) as exc_info:
        await ai_provider.complete(_GENERIC_MESSAGES, json_mode=False)
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "AI服务未启用"


def _source(relative_path: str) -> str:
    return (Path(__file__).resolve().parents[1] / relative_path).read_text(encoding="utf-8")


def test_mock_fallback_boolean_is_shared_across_all_three_sites() -> None:
    """三处审计/留痕点必须与 ai_provider.complete() 的唯一判定式同一：
    not ai_enabled and is_test_mode and ai_allow_mock_fallback，缺一即漂移
    （advisor 会写错 model_name 审计，polish 会谎报未发生的降级）。"""
    factors = (
        "not settings.ai_enabled",
        "settings.is_test_mode",
        "settings.ai_allow_mock_fallback",
    )
    provider_src = _source("app/services/ai_provider.py")
    assert "if settings.is_test_mode and settings.ai_allow_mock_fallback:" in provider_src

    advisor_src = _source("app/services/ai_advisor.py")
    advisor_match = re.search(r"mock_fallback = \((.*?)\)", advisor_src, re.DOTALL)
    assert advisor_match, "ai_advisor.py 的 mock_fallback 判定式缺失或已改变形态"
    for factor in factors:
        assert factor in advisor_match.group(1), f"ai_advisor.py 缺少因子: {factor}"

    assistant_src = _source("app/services/ai_assistant.py")
    assistant_match = re.search(
        r"if \(\n(.*?)\):\n        logger\.warning\(\n            \"ai_mock_fallback",
        assistant_src,
        re.DOTALL,
    )
    assert assistant_match, "ai_assistant.py 的 polish 留痕判定式缺失或已改变形态"
    for factor in factors:
        assert factor in assistant_match.group(1), f"ai_assistant.py 缺少因子: {factor}"

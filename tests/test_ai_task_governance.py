"""第三批：任务三类登记、未登记拒绝、legacy 栈治理。"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.services.ai.flags import AiFeature, is_ai_feature_enabled
from app.services.ai.tasks import TaskKind, get_task_registration, is_registered_task_type


def test_advisor_feature_maps_to_ai_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ai_master_enabled", True)
    monkeypatch.setattr(settings, "ai_enabled", True)
    assert is_ai_feature_enabled(AiFeature.ADVISOR, settings) is True
    monkeypatch.setattr(settings, "ai_enabled", False)
    assert is_ai_feature_enabled(AiFeature.ADVISOR, settings) is False
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "ai_master_enabled", False)
    assert is_ai_feature_enabled(AiFeature.ADVISOR, settings) is False


def test_unregistered_task_type_is_rejected_by_registry() -> None:
    assert is_registered_task_type("profile_extract") is True
    assert is_registered_task_type("cleanup") is True
    assert is_registered_task_type("profile_restore") is True
    assert is_registered_task_type("this_type_must_never_exist") is False
    assert get_task_registration("this_type_must_never_exist") is None


def test_cleanup_is_governance_and_has_no_generation_feature() -> None:
    registration = get_task_registration("cleanup")
    assert registration is not None
    assert registration.kind is TaskKind.GOVERNANCE
    assert registration.feature is None


@pytest.mark.asyncio
async def test_legacy_complete_uses_mock_when_disabled_in_test_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.ai_provider import complete

    monkeypatch.setattr(settings, "ai_enabled", False)
    raw = await complete(
        [{"role": "user", "content": "ADVISOR_ADVICE\nScenario: reply\nTone: natural"}],
        json_mode=True,
        scene="advisor",
    )
    assert "suggestions" in raw


@pytest.mark.asyncio
async def test_legacy_complete_requires_advisor_feature_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import ai_provider as provider

    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "ai_master_enabled", False)
    monkeypatch.setattr(settings, "ai_api_key", None)
    with pytest.raises(HTTPException) as exc_info:
        await provider.complete([{"role": "user", "content": "hi"}])
    assert exc_info.value.status_code == 503

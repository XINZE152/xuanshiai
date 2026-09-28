"""AI OpenAPI and task registration contracts."""

from __future__ import annotations

from app.main import app
from app.services.ai.tasks import get_task_registration


def test_compatibility_get_declares_sync_and_async_responses() -> None:
    operation = app.openapi()["paths"]["/api/v1/ai/compatibility/{target_user_id}"]["get"]
    responses = operation["responses"]

    assert "200" in responses
    assert "202" in responses
    assert "404" in responses
    assert "503" in responses
    assert responses["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/CompatibilitySnapshotRead"
    )
    assert responses["202"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/CompatibilitySnapshotRecomputeRead"
    )
    assert responses["404"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/AiErrorDetail"
    )


def test_registered_ai_tasks_have_explicit_feature_gates() -> None:
    expected = {
        "profile_extract": "PROFILE",
        "search_parse": "SEARCH",
        "compatibility": "COMPATIBILITY_SHADOW",
        "compatibility_llm": "COMPATIBILITY_SHADOW",
        "recommend_rebuild": "RECOMMEND",
        "voice_transcribe": "VOICE",
    }

    for task_type, feature_name in expected.items():
        registration = get_task_registration(task_type)
        assert registration is not None
        assert registration.feature.value.upper() == feature_name

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


def test_ai_capabilities_schema_declares_compatibility_field() -> None:
    """`compatibility` 是 2026-11-07 BE-01 新增字段：前端据此在点击前隐藏合拍度入口。

    字段集合按精确等值断言，新增/删除能力字段都必须显式改这里（契约变更留痕）。
    """
    openapi = app.openapi()
    operation = openapi["paths"]["/api/v1/ai/capabilities"]["get"]
    schema_ref = operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert schema_ref.endswith("/AiCapabilitiesResponse")

    component = openapi["components"]["schemas"]["AiCapabilitiesResponse"]
    assert set(component["properties"]) == {
        "master",
        "profile",
        "search",
        "compatibility",
        "voice",
        "advisor",
        "avatar",
    }
    assert "compatibility" in component["required"]


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

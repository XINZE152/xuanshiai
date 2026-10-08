from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.routing import APIRoute

from app.api.routes import ai_moxiang, ai_profile
from app.schemas.ai_profile import (
    ContinuousBuildRequest,
    ContinuousConfirmRequest,
    ContinuousDimensionState,
    ContinuousStateResponse,
    ContinuousSubjectState,
    ContinuousTurnRead,
    ContinuousTurnsResponse,
    ProfilePreviewDetailResponse,
    ProfilePreviewField,
    ProfilePreviewResponse,
    ProfilePublishAccepted,
)


DOC = Path(__file__).parents[1] / "docs/api/AI画像.md"


def _route(router, path: str, method: str) -> APIRoute:
    for route in router.routes:
        if isinstance(route, APIRoute) and route.path == path and method.upper() in route.methods:
            return route
    raise AssertionError(f"missing route {method.upper()} {path}")


def test_continuous_routes_and_response_models_are_registered() -> None:
    state = _route(ai_moxiang.router, "/moxiang/continuous/state", "GET")
    turns = _route(ai_moxiang.router, "/moxiang/continuous/turns", "GET")
    build = _route(ai_moxiang.router, "/moxiang/continuous/build", "POST")
    preview_create = _route(ai_profile.router, "/profile-drafts/{draft_id}/preview", "POST")
    preview_read = _route(ai_profile.router, "/profile-previews/{preview_id}", "GET")
    preview_confirm = _route(ai_profile.router, "/profile-previews/{preview_id}/confirm", "POST")

    assert state.response_model is ContinuousStateResponse
    assert turns.response_model is ContinuousTurnsResponse
    assert build.response_model is ContinuousStateResponse
    assert preview_create.response_model is ProfilePreviewResponse
    assert preview_read.response_model is ProfilePreviewDetailResponse
    assert preview_confirm.response_model is ProfilePublishAccepted

    assert {p.name for p in turns.dependant.query_params} == {"limit", "before_id"}
    assert {p.name for p in build.dependant.query_params} == {"subject"}
    assert {p.name for p in build.dependant.body_params} == {"body"}
    assert {p.name for p in build.dependant.header_params} == {"idempotency_key"}
    assert {p.name for p in preview_confirm.dependant.body_params} == {"body"}
    assert {p.name for p in preview_confirm.dependant.header_params} == {"idempotency_key"}


def test_continuous_schema_nested_fields_are_explicit() -> None:
    assert set(ContinuousStateResponse.model_fields) == {
        "flow_version",
        "consent_granted",
        "session_id",
        "personal",
        "ideal_partner",
    }
    assert set(ContinuousSubjectState.model_fields) == {
        "subject",
        "status",
        "overall_percent",
        "dimensions",
        "draft_id",
        "expected_revision",
        "preview_id",
        "task_id",
        "published_revision_id",
        "has_updates",
        "last_error",
    }
    assert set(ContinuousDimensionState.model_fields) == {"percent", "evidence_count"}
    assert set(ContinuousTurnsResponse.model_fields) == {"turns", "next_before_id"}
    assert set(ContinuousTurnRead.model_fields) == {
        "turn_id",
        "turn_no",
        "role",
        "answer_text",
        "client_turn_id",
        "created_at",
    }
    assert set(ProfilePreviewField.model_fields) == {
        "field_key",
        "field_kind",
        "category",
        "content",
        "display_value",
        "value_json",
        "change",
        "previous_display_value",
    }
    assert set(ProfilePreviewResponse.model_fields) == {
        "preview_id",
        "draft_id",
        "expected_revision",
        "subject",
        "status",
        "content",
        "task_id",
        "flow_version",
        "generation_status",
        "fields",
        "boundary_changed",
    }
    assert set(ProfilePreviewDetailResponse.model_fields) == {
        *ProfilePreviewResponse.model_fields,
        "last_error",
        "created_at",
        "updated_at",
    }
    assert set(ProfilePublishAccepted.model_fields) == {
        "task_id",
        "status",
        "stage",
        "poll_after_ms",
        "expires_at",
        "replayed",
        "revision_id",
        "revision_no",
        "subject",
        "field_count",
        "narrative_task_id",
    }
    assert set(ContinuousBuildRequest.model_fields) == {"refresh"}
    assert set(ContinuousConfirmRequest.model_fields) == {"expected_revision"}

def test_continuous_openapi_paths_and_models_are_exposed_without_database() -> None:
    isolated_app = FastAPI()
    isolated_app.include_router(ai_moxiang.router, prefix="/api/v1/ai")
    isolated_app.include_router(ai_profile.router, prefix="/api/v1/ai")
    from app.api.routes import ai_consents

    isolated_app.include_router(ai_consents.router, prefix="/api/v1/ai")
    spec = isolated_app.openapi()
    paths = spec["paths"]
    expected = {
        ("/api/v1/ai/moxiang/continuous/state", "get", "200", "ContinuousStateResponse"),
        ("/api/v1/ai/moxiang/continuous/turns", "get", "200", "ContinuousTurnsResponse"),
        ("/api/v1/ai/moxiang/continuous/build", "post", "200", "ContinuousStateResponse"),
        ("/api/v1/ai/profile-drafts/{draft_id}/preview", "post", "202", "ProfilePreviewResponse"),
        ("/api/v1/ai/profile-previews/{preview_id}", "get", "200", "ProfilePreviewDetailResponse"),
        ("/api/v1/ai/profile-previews/{preview_id}/confirm", "post", "202", "ProfilePublishAccepted"),
        ("/api/v1/ai/consents/{scope}", "delete", "202", "AiConsentOperationResponse"),
    }
    for path, method, status, model in expected:
        schema = paths[path][method]["responses"][status]["content"]["application/json"]["schema"]
        assert schema["$ref"].endswith(f"/{model}")

    assert paths["/api/v1/ai/moxiang/continuous/turns"]["get"]["parameters"]
    assert paths["/api/v1/ai/moxiang/continuous/build"]["post"]["requestBody"]["content"]
    assert paths["/api/v1/ai/profile-previews/{preview_id}/confirm"]["post"]["requestBody"]["required"] is True
    assert {
        "operation_id",
        "scope",
        "operation",
        "status",
        "consent",
        "cleanup_task_id",
        "privacy_revision",
    } == set(spec["components"]["schemas"]["AiConsentOperationResponse"]["properties"])



def test_continuous_http_document_covers_routes_security_and_migration() -> None:
    text = DOC.read_text(encoding="utf-8")
    required = [
        "POST /api/v1/ai/moxiang/continuous/build?subject=personal",
        "GET /api/v1/ai/moxiang/continuous/state",
        "GET /api/v1/ai/moxiang/continuous/turns",
        "POST /api/v1/ai/profile-drafts/{draft_id}/preview",
        "GET /api/v1/ai/profile-previews/{preview_id}",
        "POST /api/v1/ai/profile-previews/{preview_id}/confirm",
        "Authorization: Bearer <access_token>",
        "Content-Type: application/json",
        "Idempotency-Key",
        '"retryable"',
        '"retry_after_ms"',
        "CONTINUOUS_CONFIRM_REQUIRED",
        "PREVIEW_REQUIRED",
        "AI_CONSENT_REQUIRED",
        "X-Expected-Privacy-Revision",
        "撤权",
        "迁移顺序",
    ]
    for marker in required:
        assert marker in text


def test_continuous_build_body_has_real_default_and_invalid_example() -> None:
    field = ContinuousBuildRequest.model_fields["refresh"]
    assert field.default is False
    assert "third_person" in DOC.read_text(encoding="utf-8")

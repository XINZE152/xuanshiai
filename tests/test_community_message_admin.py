from datetime import datetime

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.dependencies import CurrentMatchmakerAdmin
from app.main import app
from app.schemas.matchmaker_admin import MatchmakerAdminAccount
from app.services.message_admin import _message_scope, _redact_content
from app.api.routes.community_admin import _require_global_write


client = TestClient(app)


def _admin(scope: str, permissions: set[str] | None = None) -> CurrentMatchmakerAdmin:
    return CurrentMatchmakerAdmin(
        account=MatchmakerAdminAccount(
            id=1,
            username="admin",
            display_name="Admin",
            matchmaker_user_id=7,
            data_scope=scope,
            organization_id=10,
            status=1,
            last_login_at=datetime(2026, 8, 24),
        ),
        session_id=1,
        permissions=frozenset(permissions or set()),
    )


def test_community_and_message_admin_routes_are_registered() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert set(paths["/api/v1/admin/community/topics"]) == {"get", "post"}
    assert set(paths["/api/v1/admin/community/banners"]) == {"get", "post"}
    assert "patch" in paths["/api/v1/admin/community/topics/{topic_id}"]
    assert "patch" in paths["/api/v1/admin/community/banners/{banner_id}"]
    assert "get" in paths["/api/v1/admin/messages"]
    assert "patch" in paths["/api/v1/admin/messages/{message_id}/moderation"]
    assert "post" in paths["/api/v1/admin/messages/announcements"]


def test_new_admin_routes_require_authentication() -> None:
    assert client.get("/api/v1/admin/community/topics").status_code == 401
    assert client.get("/api/v1/admin/messages").status_code == 401


def test_community_global_write_guard() -> None:
    _require_global_write(_admin("ALL"))
    _require_global_write(_admin("SELF", {"*"}))
    with pytest.raises(HTTPException) as exc:
        _require_global_write(_admin("ORGANIZATION", {"community.moderate"}))
    assert exc.value.status_code == 403


def test_message_scope_is_parameterized() -> None:
    params: dict[str, object] = {}
    clause = _message_scope(_admin("SELF"), params)
    assert ":scope_matchmaker_id" in clause
    assert params == {"scope_matchmaker_id": 7}
    params = {}
    clause = _message_scope(_admin("ORGANIZATION"), params)
    assert ":scope_organization_id" in clause
    assert params == {"scope_organization_id": 10}
    assert _message_scope(_admin("ALL"), {}) == "1=1"


def test_message_content_redacts_phone_numbers() -> None:
    assert _redact_content("call 13812345678 now") == "call 1********** now"
    assert _redact_content(None) is None

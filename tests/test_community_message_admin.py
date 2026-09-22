from datetime import datetime

import pytest
import inspect
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


def test_message_moderation_uses_data_scope_filter() -> None:
    from app.services import message_admin

    source = inspect.getsource(message_admin.moderate_admin_message)
    assert "_message_scope(admin, params)" in source
    assert "AND \"\"\" + scope" in source


@pytest.mark.asyncio
async def test_message_moderation_hides_out_of_scope_message() -> None:
    from app.services.message_admin import moderate_admin_message

    class Result:
        def mappings(self):
            return self

        def first(self):
            return None

    class DB:
        async def execute(self, statement, params=None):
            sql = str(statement)
            assert "scope_assignment.matchmaker_id=:scope_matchmaker_id" in sql
            assert params == {"id": 99, "scope_matchmaker_id": 7}
            return Result()

    with pytest.raises(HTTPException) as exc:
        await moderate_admin_message(DB(), _admin("SELF"), 99, "recall", "越权测试")
    assert exc.value.status_code == 404


def test_banner_admin_checks_overlapping_active_window() -> None:
    from app.api.routes import community_admin

    source = inspect.getsource(community_admin.create_banner)
    update_source = inspect.getsource(community_admin.update_banner)
    assert "同一位置和时间段已有生效中的 Banner" in source
    assert "同一位置和时间段已有生效中的 Banner" in update_source

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.api.dependencies import _matchmaker_admin_permission
from app.api.routes.matchmaker_admin import _matchmaker_scope_filter
from app.schemas.matchmaker_staff_admin import (
    MatchmakerPermissionsUpdate,
    MatchmakerStaffCreate,
)


client = TestClient(app)


def test_matchmaker_statistics_routes_require_read_permission() -> None:
    from starlette.requests import Request

    for path in ("/api/v1/admin/dashboard/stats", "/api/v1/admin/matchmaker/statistics"):
        request = Request({"type": "http", "method": "GET", "path": path, "headers": []})
        assert _matchmaker_admin_permission(request) == "matchmaker.read"


def test_matchmaker_statistics_scope_uses_existing_scope_condition() -> None:
    from types import SimpleNamespace

    current = SimpleNamespace(
        account=SimpleNamespace(data_scope="SELF", matchmaker_user_id=9),
        permissions=frozenset({"matchmaker.read"}),
        scope_condition=lambda **kwargs: "a.user_id = :scope_user_id",
    )
    params: dict[str, object] = {}
    assert _matchmaker_scope_filter(current, params, "a.user_id", "stats") == "a.user_id = :scope_user_id"


def test_matchmaker_staff_admin_routes_are_registered() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    expected = {
        "/api/v1/admin/matchmakers",
        "/api/v1/admin/matchmakers/{matchmaker_id}",
        "/api/v1/admin/matchmakers/{matchmaker_id}/lock",
        "/api/v1/admin/matchmakers/{matchmaker_id}/visibility",
        "/api/v1/admin/matchmakers/{matchmaker_id}/permissions",
        "/api/v1/admin/matchmakers/{matchmaker_id}/work-report",
        "/api/v1/admin/matchmakers/{matchmaker_id}/report",
        "/api/v1/admin/matchmakers/{matchmaker_id}/poster",
        "/api/v1/admin/matchmakers/{matchmaker_id}/platform-token",
        "/api/v1/admin/matchmakers/tutorial",
        "/api/v1/admin/menus/tree",
        "/api/v1/admin/dict/commission-levels",
        "/api/v1/admin/dict/stores",
        "/api/v1/admin/common/upload",
    }
    assert expected <= set(paths)
    assert client.get("/api/v1/admin/matchmakers").status_code == 401


def test_matchmaker_staff_admin_contracts_validate_sensitive_fields() -> None:
    request = MatchmakerStaffCreate(
        user_id=42,
        lookup="张三",
        lookup_by="nickname",
        display_name="张三",
        phone="13800138000",
    )
    assert request.user_id == 42
    assert request.lookup == "张三"
    with pytest.raises(ValidationError):
        MatchmakerStaffCreate(display_name="张三", phone="invalid-phone")
    assert MatchmakerPermissionsUpdate(menuIds=[1, 2]).menu_ids == [1, 2]

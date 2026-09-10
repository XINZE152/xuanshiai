import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.matchmaker_staff_admin import (
    MatchmakerPermissionsUpdate,
    MatchmakerStaffCreate,
)


client = TestClient(app)


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

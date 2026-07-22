import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.matchmaker import MatchmakerServiceRequestCreate, MatchmakerServiceRequestUpdate


client = TestClient(app)


def test_matchmaker_service_request_schema_validates_business_rules() -> None:
    request = MatchmakerServiceRequestCreate(matchmaker_id=1, requirement="希望寻找认真稳定的婚恋关系")
    assert request.matchmaker_id == 1
    with pytest.raises(ValidationError):
        MatchmakerServiceRequestCreate(matchmaker_id=1, requirement="太短")
    with pytest.raises(ValidationError):
        MatchmakerServiceRequestUpdate(status=2)
    assert MatchmakerServiceRequestUpdate(status=1).status == 1


def test_matchmaker_routes_are_registered_and_require_authentication() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/matchmakers" in paths
    assert "/api/v1/matchmakers/{matchmaker_id}" in paths
    assert "/api/v1/matchmaker/service-requests" in paths
    assert "/api/v1/matchmaker/service-requests/mine" in paths
    assert "/api/v1/matchmaker/service-requests/assigned" in paths
    assert "/api/v1/admin/matchmaker/service-requests" in paths
    assert client.post(
        "/api/v1/matchmaker/service-requests",
        json={"matchmaker_id": 1, "requirement": "希望寻找认真稳定的婚恋关系"},
    ).status_code == 401


def test_matchmaker_public_list_does_not_require_authentication() -> None:
    operation = client.get("/openapi.json").json()["paths"]["/api/v1/matchmakers"]["get"]
    security = operation.get("security", [])
    assert security == []

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.discovery import ApplicationCreateRequest, DiscoveryFilters, DiscoverySearch


client = TestClient(app)


def test_discovery_filters_validate_ranges_and_page_size() -> None:
    with pytest.raises(ValidationError):
        DiscoveryFilters(age_min=40, age_max=20)
    with pytest.raises(ValidationError):
        DiscoveryFilters(page_size=21)


def test_application_message_has_a_bounded_length() -> None:
    with pytest.raises(ValidationError):
        ApplicationCreateRequest(message="x" * 256)


def test_discovery_search_requires_nickname_or_tag() -> None:
    with pytest.raises(ValidationError):
        DiscoverySearch()
    query = DiscoverySearch(nickname="  小明  ")
    assert query.nickname == "小明"


def test_discovery_routes_are_registered_and_require_authentication() -> None:
    openapi = client.get("/openapi.json").json()
    paths = openapi["paths"]
    assert "/api/v1/discovery/recommendations" in paths
    assert "/api/v1/discovery/search" in paths
    assert "/api/v1/discovery/filters/saved" in paths
    assert "/api/v1/users/{user_id}/profile" in paths

    response = client.get("/api/v1/discovery/recommendations")
    assert response.status_code == 401

    response = client.get("/api/v1/discovery/search?tag=旅行")
    assert response.status_code == 401


def test_filter_options_is_public() -> None:
    response = client.get("/api/v1/discovery/filter-options")
    assert response.status_code == 200
    assert response.json()["genders"]

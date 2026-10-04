"""D-2 回归：红娘评价接口挂载与详情聚合。"""

from __future__ import annotations

import pathlib

from app.main import app

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_rating_routes_registered() -> None:
    paths = app.openapi()["paths"]
    assert "/api/v1/matchmaker/services/{service_id}/rating" in paths
    assert "/api/v1/matchmakers/{matchmaker_id}/ratings" in paths


def test_rating_create_is_authenticated_and_returns_201() -> None:
    spec = app.openapi()["paths"]["/api/v1/matchmaker/services/{service_id}/rating"]["post"]
    assert "201" in spec["responses"]
    assert spec.get("security")


def test_rating_list_is_public_read_only() -> None:
    spec = app.openapi()["paths"]["/api/v1/matchmakers/{matchmaker_id}/ratings"]["get"]
    assert not spec.get("security")


def test_matchmaker_detail_aggregates_rating_score_and_count() -> None:
    """详情接口必须回填平均分与评价数，避免前端二次请求。"""
    source = (ROOT / "app/services/matchmaker.py").read_text(encoding="utf-8")
    assert "COALESCE(rating_stats.rating_score, 0) AS rating_score" in source
    assert "COALESCE(rating_stats.rating_count, 0) AS rating_count" in source


def test_rating_service_is_wired_into_routes() -> None:
    source = (ROOT / "app/api/routes/matchmaker.py").read_text(encoding="utf-8")
    assert "create_matchmaker_rating(db, current, service_id, body)" in source
    assert "list_matchmaker_ratings(db, matchmaker_id, page, page_size)" in source

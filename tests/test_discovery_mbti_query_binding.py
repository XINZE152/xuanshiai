"""推荐/广场 `mbti` 列表查询参数绑定回归（DISCOVERY-MBTI-QUERY-20261114）。

此前路由用 ``DiscoveryFilters = Depends()`` 绑定查询模型，FastAPI 在该写法下
不会把重复查询键聚合进列表字段，``mbti=ENTJ&mbti=INTP`` 被解析为 ``None``，
MBTI 筛选被静默丢弃（不同 MBTI 返回同一份无筛选结果）。改为
``Annotated[DiscoveryFilters, Query()]`` 后，本文件锁定：

1. 推荐/广场都能把重复键 ``mbti`` 解析为数组并传入 ``get_discovery_page``;
2. 单个 ``mbti``、缺省 ``mbti`` 行为正确;
3. 标量筛选（年龄/认证）仍正常绑定;
4. 非法 MBTI 值仍按 422 拒绝。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_browsable_user
from app.api.routes import discovery as discovery_routes
from app.db.session import get_db
from app.main import app
from app.schemas.discovery import DiscoveryPage


@pytest.fixture
def captured(monkeypatch):
    mock = AsyncMock(
        return_value=DiscoveryPage(
            items=[], page=1, page_size=20, total=0, has_more=False
        )
    )
    monkeypatch.setattr(discovery_routes, "get_discovery_page", mock)
    app.dependency_overrides[get_browsable_user] = lambda: SimpleNamespace(id=1)
    app.dependency_overrides[get_db] = lambda: None
    yield mock
    app.dependency_overrides.pop(get_browsable_user, None)
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def client():
    return TestClient(app)


def _filters(mock: AsyncMock):
    return mock.call_args.args[2]


def test_recommendations_binds_mbti_repeat_keys(client, captured) -> None:
    resp = client.get("/api/v1/discovery/recommendations?mbti=ENTJ&mbti=INTP")
    assert resp.status_code == 200
    assert _filters(captured).mbti == ["ENTJ", "INTP"]
    assert captured.call_args.kwargs["plaza"] is False


def test_recommendations_binds_single_mbti(client, captured) -> None:
    resp = client.get("/api/v1/discovery/recommendations?mbti=INFP")
    assert resp.status_code == 200
    assert _filters(captured).mbti == ["INFP"]


def test_recommendations_without_mbti_is_none(client, captured) -> None:
    resp = client.get("/api/v1/discovery/recommendations?page=1")
    assert resp.status_code == 200
    assert _filters(captured).mbti is None


def test_plaza_binds_mbti_repeat_keys(client, captured) -> None:
    resp = client.get("/api/v1/discovery/plaza?mbti=ENTJ&mbti=INTP")
    assert resp.status_code == 200
    assert _filters(captured).mbti == ["ENTJ", "INTP"]
    assert captured.call_args.kwargs["plaza"] is True


def test_recommendations_scalar_filters_still_bind(client, captured) -> None:
    resp = client.get(
        "/api/v1/discovery/recommendations?age_min=25&age_max=35&certified_only=true"
    )
    assert resp.status_code == 200
    filters = _filters(captured)
    assert filters.age_min == 25
    assert filters.age_max == 35
    assert filters.certified_only is True


def test_recommendations_invalid_mbti_rejected(client, captured) -> None:
    resp = client.get("/api/v1/discovery/recommendations?mbti=XXXX")
    assert resp.status_code == 422
    captured.assert_not_called()
"""浏览记录 / 访客列表不得套用推荐流的资料完整度门禁（2026-11-02，Q21）。

背景：``candidate_visibility`` 的 ``score >= 100`` 是**推荐曝光**的门槛；把它
沿用到底 HISTORY/VISITORS 会导致「会员开通了，访客列表还是空」。本文件锁定三件事：

1. HISTORY/VISITORS 场景构造 predicate 时可以关掉完整度门禁；
2. 关掉后封禁/隐私条件仍在（不能顺手把别的门也放开）；
3. ``browse_history`` / ``visitors`` 两个入口**同时**给 predicate 与
   ``_target_rows`` 传 ``skip_completion_gate=True``（只改一处会出现
   「列表有记录但卡片被丢掉」）。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import discovery as discovery_routes
from app.schemas.discovery import BrowseHistoryPage
from app.services import discovery
from app.services.candidate_visibility import (
    CandidateVisibilityService,
    ViewerContext,
    VisibilityScene,
)


class _Result:
    """最小结果集替身：兼容 mappings().all()/first()/scalar()/fetchall()。"""

    def __init__(self, rows: list[dict[str, Any]] | None = None, scalar: Any = 0) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def mappings(self) -> _Result:
        return self

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return list(self._rows)

    def scalar(self) -> Any:
        return self._scalar


class _Db:
    async def execute(self, *_args: object, **_kwargs: object) -> _Result:
        return _Result()

    async def commit(self) -> None:  # pragma: no cover - 本用例不触发
        return None


class _Predicate:
    """predicate 的鸭子类型替身，只暴露调用方实际读取的两个属性。"""

    def __init__(self) -> None:
        self.clause = "1=1"
        self.params: dict[str, Any] = {}


def test_history_scene_can_skip_completion_gate() -> None:
    service = CandidateVisibilityService()
    viewer = ViewerContext(user_id=1, realname_status=2, is_vip=True)

    gated = service.predicate(viewer, VisibilityScene.HISTORY)
    ungated = service.predicate(viewer, VisibilityScene.HISTORY, skip_completion_gate=True)

    assert "COALESCE(c.score" in gated.clause, "默认仍应保留完整度门禁"
    assert "COALESCE(c.score" not in ungated.clause, "skip 后不应再出现完整度门禁"
    # 放开完整度不等于放开安全门：封禁关系必须仍在。
    assert "user_block" in ungated.clause


@pytest.mark.asyncio
async def test_browse_history_passes_skip_gate_to_both_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, dict[str, Any]] = {}

    async def fake_scene(
        _db: Any, viewer_id: int, scene: VisibilityScene, **kwargs: Any
    ) -> tuple[dict[str, Any], bool, _Predicate]:
        captured["scene"] = kwargs
        return {"user_id": viewer_id}, True, _Predicate()

    async def fake_targets(
        _db: Any, _viewer_id: int, _ids: list[int], **kwargs: Any
    ) -> dict[int, dict[str, Any]]:
        captured["targets"] = kwargs
        return {}

    monkeypatch.setattr(discovery, "_scene_visibility", fake_scene)
    monkeypatch.setattr(discovery, "_target_rows", fake_targets)

    await discovery.browse_history(_Db(), 5, 1, 20)

    assert captured["scene"]["skip_completion_gate"] is True
    assert captured["targets"]["skip_completion_gate"] is True


@pytest.mark.asyncio
async def test_visitors_passes_skip_gate_to_both_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, dict[str, Any]] = {}

    async def fake_scene(
        _db: Any, viewer_id: int, scene: VisibilityScene, **kwargs: Any
    ) -> tuple[dict[str, Any], bool, _Predicate]:
        captured["scene"] = kwargs
        return {"user_id": viewer_id}, True, _Predicate()

    async def fake_targets(
        _db: Any, _viewer_id: int, _ids: list[int], **kwargs: Any
    ) -> dict[int, dict[str, Any]]:
        captured["targets"] = kwargs
        return {}

    monkeypatch.setattr(discovery, "_scene_visibility", fake_scene)
    monkeypatch.setattr(discovery, "_target_rows", fake_targets)

    # 2026-11-20 起非会员也查列表（返回脱敏占位），两条路径都要带 skip_completion_gate。
    await discovery.visitors(_Db(), 5, 1, 20)

    assert captured["scene"]["skip_completion_gate"] is True
    assert captured["targets"]["skip_completion_gate"] is True


@pytest.fixture
def footprint_db(monkeypatch):
    # 在内存中执行真实的分页 SQL；仅替换 MySQL 的 CURDATE 和名片加载。
    with sqlite3.connect(":memory:") as conn:
        conn.row_factory = sqlite3.Row
        conn.create_function("CURDATE", 0, lambda: "2026-11-20")
        conn.executescript("""
            CREATE TABLE users (id INTEGER, status INTEGER);
            CREATE TABLE user_privacy (user_id INTEGER);
            CREATE TABLE user_profile_completion (user_id INTEGER);
            CREATE TABLE user_favorite (user_id INTEGER, target_user_id INTEGER, type INTEGER);
            CREATE TABLE user_browse_history (user_id INTEGER, target_user_id INTEGER,
                created_at TEXT, is_anonymous INTEGER);
            INSERT INTO users VALUES (5, 1), (10, 1), (11, 1), (12, 1), (13, 1), (14, 0), (15, 1);
            INSERT INTO user_favorite VALUES (5, 10, 1), (5, 10, 2), (5, 11, 1),
                (5, 12, 2), (15, 12, 1), (13, 5, 1), (5, 14, 1), (5, 15, 1);
            INSERT INTO user_browse_history VALUES
                (5, 10, '2026-11-20 08:00:00', 0), (5, 10, '2026-11-20 09:00:00', 0),
                (5, 11, '2026-11-20 09:00:00', 1), (5, 12, '2026-11-20 10:00:00', 0),
                (5, 13, '2026-11-20 11:00:00', 0), (5, 14, '2026-11-20 12:00:00', 0),
                (5, 15, '2026-11-19 09:00:00', 0),
                (10, 5, '2026-11-20 08:00:00', NULL), (10, 5, '2026-11-20 09:00:00', 1),
                (11, 5, '2026-11-20 08:00:00', 0), (12, 5, '2026-11-20 12:00:00', 1),
                (14, 5, '2026-11-20 10:00:00', 0);
        """)

        class Db:
            vip = False

            async def execute(self, statement, params):
                cursor = conn.execute(str(statement), params)
                if str(statement).startswith("SELECT COUNT"):
                    return _Result(scalar=cursor.fetchone()[0])
                rows = [dict(row) for row in cursor.fetchall()]
                for row in rows:
                    row["viewed_at"] = datetime.fromisoformat(row["viewed_at"])
                return _Result(rows)

        async def scene(db, viewer_id, _scene, **_kwargs):
            return {"user_id": viewer_id}, db.vip, SimpleNamespace(clause="u.status = 1", params={})

        async def targets(_db, _viewer_id, ids, **_kwargs):
            return {target: {"user_id": target, "nickname": f"访客{target}"} for target in ids}

        monkeypatch.setattr(discovery, "_scene_visibility", scene)
        monkeypatch.setattr(discovery, "_target_rows", targets)
        monkeypatch.setattr(discovery, "_attach_card_qa_media", AsyncMock())
        yield Db()


@pytest.mark.asyncio
@pytest.mark.parametrize(("filter_name", "expected"), [("all", [13, 12, 11, 10]), ("liked", [11, 10]), ("unliked", [13, 12])])
async def test_browse_history_filters_before_pagination(footprint_db, filter_name, expected):
    for index, expected_id in enumerate(expected, 1):
        page = await discovery.browse_history(footprint_db, 5, index, 1, filter=filter_name)
        assert page.total == len(expected)
        assert [item.target.user_id for item in page.items] == [expected_id]
    empty = await discovery.browse_history(footprint_db, 5, len(expected) + 1, 1, filter=filter_name)
    assert empty.items == []
    assert empty.total == len(expected)


@pytest.mark.asyncio
async def test_vip_history_keeps_previous_day(footprint_db):
    footprint_db.vip = True
    page = await discovery.browse_history(footprint_db, 5, 1, 20, filter="liked")
    assert page.total == 3
    assert [item.target.user_id for item in page.items] == [11, 10, 15]


@pytest.mark.asyncio
async def test_visitors_exclude_anonymous_and_keep_stable_pages(footprint_db):
    for vip in (False, True):
        footprint_db.vip = vip
        for index, visitor_id in enumerate((11, 10), 1):
            page = await discovery.visitors(footprint_db, 5, index, 1)
            assert page.count == 2
            assert page.has_more == (index == 1)
            assert page.can_view_details is vip
            assert page.masked is not vip
            assert len(page.items) == 1
            assert page.items[0].target.user_id == (visitor_id if vip else -1)
            assert page.items[0].viewed_at == datetime(2026, 11, 20, 8)
            if not vip:
                assert page.items[0].target.detail_locked
        empty = await discovery.visitors(footprint_db, 5, 3, 1)
        assert empty.count == 2 and empty.items == [] and not empty.has_more


def test_history_route_validates_filter_and_keeps_default(monkeypatch):
    app = FastAPI()
    app.include_router(discovery_routes.router, prefix="/api/v1")
    app.dependency_overrides[discovery_routes.get_current_user] = lambda: SimpleNamespace(id=5)
    app.dependency_overrides[discovery_routes.get_db] = lambda: None
    service = AsyncMock(return_value=BrowseHistoryPage(items=[], page=2, page_size=1, total=0))
    monkeypatch.setattr(discovery_routes, "browse_history", service)
    with TestClient(app) as client:
        url = "/api/v1/discovery/browse-history"
        for query, expected in (({}, "all"), ({"filter": "liked"}, "liked"), ({"filter": "unliked"}, "unliked")):
            response = client.get(url, params={"page": 2, "page_size": 1, **query})
            assert response.status_code == 200
            assert response.json() == {"items": [], "page": 2, "page_size": 1, "total": 0}
            service.assert_awaited_with(None, 5, 2, 1, filter=expected)
        service.reset_mock()
        response = client.get(url, params={"filter": "favorite"})
        assert response.status_code == 422
        assert response.json()["detail"][0]["loc"] == ["query", "filter"]
        service.assert_not_awaited()


def test_recommendation_scene_keeps_gate_by_default() -> None:
    """回归：推荐/搜索/详情链路不能被这次改动顺手放开。"""
    service = CandidateVisibilityService()
    viewer = ViewerContext(user_id=1, realname_status=2, is_vip=False)
    for scene in (VisibilityScene.DISCOVERY, VisibilityScene.SEARCH, VisibilityScene.PROFILE):
        assert "COALESCE(c.score" in service.predicate(viewer, scene).clause

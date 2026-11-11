"""Q15 动态按人查（mode=user）单元契约。

全部为不依赖数据库的内存/假库契约：子句生成、路由参数校验、select/count
过滤链对称性。可见性边界（公开尊重 show_posts、好友双向匹配、私密仅本人、
双向拉黑、审核与删除过滤）由共享子句保证，这里断言 mode=user 完整复用，
不另起查询体系。真机双账号矩阵验收见方案文档 33 §3.1（外部依赖）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.dependencies import CurrentUser, get_current_user
from app.api.routes import community as community_routes
from app.db.session import get_db
from app.main import app
from app.schemas.community import CommunityPostPage
from app.services import community as community_service

client = TestClient(app)


def _feed_clauses(mode: str, **kwargs: Any) -> tuple[str, dict[str, Any]]:
    return community_service._feed_clauses(
        mode,
        city=None,
        city_code=None,
        topic_id=None,
        filter_key=None,
        me=None,
        **kwargs,
    )


def test_feed_clauses_user_mode_filters_by_target() -> None:
    where, params = _feed_clauses("user", target_user_id=42)
    assert where == " AND p.user_id = :target_user_id"
    assert params == {"target_user_id": 42}


def test_feed_clauses_user_mode_requires_target() -> None:
    with pytest.raises(HTTPException) as exc:
        _feed_clauses("user", target_user_id=None)
    assert exc.value.status_code == 422


def test_feed_clauses_other_modes_unchanged() -> None:
    where, params = _feed_clauses("mine")
    assert where == " AND p.user_id = :user_id"
    assert params == {}
    where, params = _feed_clauses("latest")
    assert where == ""
    assert params == {}


class _RowsResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _RowsResult:
        return self

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)


class _ScalarResult:
    def __init__(self, value: int) -> None:
        self._value = value

    def scalar(self) -> int:
        return self._value


class CapturingSession:
    """记录 list_posts 发出的 select/count SQL 与参数，不连数据库。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        sql = " ".join(str(statement).split())
        self.calls.append((sql, dict(params or {})))
        if sql.startswith("SELECT COUNT(*)"):
            return _ScalarResult(0)
        return _RowsResult([])


@pytest.mark.asyncio
async def test_list_posts_user_mode_applies_full_filter_chain() -> None:
    db = CapturingSession()
    page = await community_service.list_posts(db, 7, mode="user", target_user_id=42)

    assert page.items == []
    assert page.total == 0
    select_calls = [(sql, p) for sql, p in db.calls if sql.startswith("SELECT p.id")]
    count_calls = [(sql, p) for sql, p in db.calls if sql.startswith("SELECT COUNT(*)")]
    assert len(select_calls) == 1
    assert len(count_calls) == 1

    visibility = " ".join(community_service._post_visibility_clause().split())
    for sql, params in (*select_calls, *count_calls):
        # 可见性/拉黑/封禁/审核/删除过滤链与全站动态流完全一致
        assert visibility in sql
        assert "FROM user_block b" in sql
        assert "b.user_id = :user_id AND b.target_user_id = p.user_id" in sql
        assert "b.user_id = p.user_id AND b.target_user_id = :user_id" in sql
        assert "user_restriction ban" in sql
        assert "ban.restriction_type = 'TOTAL_BAN'" in sql
        assert "p.status = 1" in sql
        assert "COALESCE(p.moderation_status, 1) = 1" in sql
        assert "p.deleted_at IS NULL" in sql
        assert "AND p.user_id = :target_user_id" in sql
        assert params["target_user_id"] == 42
        assert params["user_id"] == 7


@pytest.mark.asyncio
async def test_list_posts_user_mode_unknown_target_returns_empty_page() -> None:
    """目标用户不存在/无可见动态时返回空页，不泄露用户存在性。"""
    db = CapturingSession()
    page = await community_service.list_posts(db, 7, mode="user", target_user_id=999999)
    assert page.items == []
    assert page.total == 0
    assert page.page == 1


@pytest.mark.asyncio
async def test_list_posts_user_mode_requires_target() -> None:
    with pytest.raises(HTTPException) as exc:
        await community_service.list_posts(CapturingSession(), 7, mode="user")
    assert exc.value.status_code == 422


def _override_feed_dependencies(monkeypatch: pytest.MonkeyPatch, calls: list[dict[str, Any]]) -> None:
    async def fake_current_user() -> CurrentUser:
        return CurrentUser(
            id=7,
            session_id=9,
            phone="13800000000",
            status=1,
            realname_status=2,
        )

    async def fake_db():
        yield object()

    async def fake_list_posts(*_args: object, **kwargs: object) -> CommunityPostPage:
        calls.append(dict(kwargs))
        return CommunityPostPage(items=[], page=1, page_size=20, total=0)

    monkeypatch.setattr(community_routes, "list_posts", fake_list_posts)
    app.dependency_overrides[get_current_user] = fake_current_user
    app.dependency_overrides[get_db] = fake_db


def test_feed_route_user_mode_requires_target_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    _override_feed_dependencies(monkeypatch, calls)
    try:
        response = client.get("/api/v1/community/posts", params={"mode": "user"})
        assert response.status_code == 422
        assert calls == []
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)


def test_feed_route_user_mode_passes_target(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    _override_feed_dependencies(monkeypatch, calls)
    try:
        response = client.get(
            "/api/v1/community/posts",
            params={"mode": "user", "target_user_id": 42, "page": 2, "page_size": 10},
        )
        assert response.status_code == 200
        assert len(calls) == 1
        assert calls[0]["mode"] == "user"
        assert calls[0]["target_user_id"] == 42
        assert calls[0]["page"] == 2
        assert calls[0]["page_size"] == 10
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)


def test_feed_route_rejects_target_on_other_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    _override_feed_dependencies(monkeypatch, calls)
    try:
        for mode in ("latest", "mine", "following"):
            response = client.get(
                "/api/v1/community/posts",
                params={"mode": mode, "target_user_id": 42},
            )
            assert response.status_code == 422
        assert calls == []
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)


def test_feed_route_target_user_id_must_be_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    _override_feed_dependencies(monkeypatch, calls)
    try:
        response = client.get(
            "/api/v1/community/posts",
            params={"mode": "user", "target_user_id": 0},
        )
        assert response.status_code == 422
        assert calls == []
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)


def test_feed_openapi_exposes_user_mode_and_target_param() -> None:
    parameters = app.openapi()["paths"]["/api/v1/community/posts"]["get"]["parameters"]
    mode = next(item for item in parameters if item["name"] == "mode")
    assert "user" in str(mode)
    target = next(item for item in parameters if item["name"] == "target_user_id")
    assert target["in"] == "query"


def test_community_docs_cover_user_mode() -> None:
    docs = Path("docs/api/社区.md").read_text(encoding="utf-8")
    feed = docs.split("### 2.2", 1)[1].split("### 2.3", 1)[0]
    assert "`user`" in feed
    assert "target_user_id" in feed
    assert "mode=user" in feed
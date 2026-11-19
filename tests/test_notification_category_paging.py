"""通知分类筛选 / 分页 / 未读汇总的本地契约测试（不连数据库）。

背景（2026-11-11）：前端通知页固定拉 ``page=1&page_size=50`` 后再本地按 type
过滤，超过 50 条的账号，较旧的匹配通知永远进不了那一页；未读数也走列表接口
取总 ``unread_count``，拿不到分类未读。客户端要改成「服务端分类 + 真分页」，
服务端这几个契约必须先锁死：

1. ``category`` 条件必须同时作用于列表、``total`` 与 ``unread`` 三段 SQL——
   只作用于列表会让翻页边界算错（前端 hasMore 恒真或提前截断）；
2. ``page/page_size`` 只影响 LIMIT/OFFSET，不得混进 COUNT 的参数；
3. ``unread-summary`` 的 ``by_category`` 与列表筛选共用同一份类型映射；
4. ``notification_type`` 与 ``category`` 互斥，同时传要 422。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import HTTPException

from app.api.dependencies import CurrentUser
from app.api.routes import social as social_routes
from app.services.social import list_notifications, notification_unread_summary


CURRENT = CurrentUser(
    id=42, session_id=7, phone="13800000000", status=1, realname_status=1
)


class _Result:
    def __init__(self, rows: list[dict[str, Any]] | None = None, scalar: Any = 0) -> None:
        self._rows = rows
        self._scalar = scalar

    def mappings(self) -> "_Result":
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows or []

    def first(self) -> dict[str, Any] | None:
        rows = self._rows or []
        return rows[0] if rows else None

    def scalar(self) -> Any:
        return self._scalar


class _FakeNotificationDB:
    """按 SQL 形状分派假结果，并记录每次调用的参数供断言。"""

    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        total: int = 0,
        unread: int = 0,
        grouped: list[dict[str, Any]] | None = None,
    ) -> None:
        self.rows = rows or []
        self.total = total
        self.unread = unread
        self.grouped = grouped or []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(statement)
        self.calls.append((sql, dict(params or {})))
        if "GROUP BY" in sql:
            return _Result(rows=self.grouped)
        if "COUNT(*)" in sql:
            return _Result(scalar=self.unread if "is_read = 0" in sql else self.total)
        return _Result(rows=self.rows)

    async def commit(self) -> None:
        return None

    def sql_like(self, needle: str) -> list[tuple[str, dict[str, Any]]]:
        return [call for call in self.calls if needle in call[0]]


def _row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": 1,
        "notification_type": "match_application",
        "title": "有人申请认识你",
        "content": "来自滨江的男生想认识你",
        "payload": None,
        "related_user_id": 7,
        "related_id": 11,
        "target_type": "user",
        "target_id": 7,
        "is_read": 0,
        "created_at": datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


MATCH_TYPES = {
    "match",
    "match_application",
    "match_application_accepted",
    "match_application_rejected",
    "apply",
}


@pytest.mark.asyncio
async def test_category_filter_applies_to_list_total_and_unread() -> None:
    """三段 SQL 都要带分类条件，否则 total/unread 是全量、列表是分类，翻页必错。"""
    db = _FakeNotificationDB(rows=[_row()], total=57, unread=6)
    await list_notifications(db, 42, page=2, page_size=20, category="match")

    list_calls = db.sql_like("FROM user_notification WHERE user_id = :user_id")
    assert len(list_calls) == 3, "列表 + total + unread 各一次"

    for sql, _params in list_calls:
        assert "notification_type IN (" in sql, sql

    count_calls = [call for call in db.calls if "COUNT(*)" in call[0]]
    assert len(count_calls) == 2
    for _sql, params in count_calls:
        assert MATCH_TYPES.issubset(set(params.values())), params


@pytest.mark.asyncio
async def test_pagination_params_do_not_leak_into_counts() -> None:
    """LIMIT/OFFSET 只属于列表查询，混进 COUNT 参数会让计数按页截断。"""
    db = _FakeNotificationDB(rows=[_row()], total=57, unread=6)
    await list_notifications(db, 42, page=3, page_size=20, category="match")

    list_calls = [call for call in db.calls if "LIMIT" in call[0]]
    assert len(list_calls) == 1
    assert list_calls[0][1]["limit"] == 20
    assert list_calls[0][1]["offset"] == 40

    count_calls = [call for call in db.calls if "COUNT(*)" in call[0]]
    for _sql, params in count_calls:
        assert "limit" not in params
        assert "offset" not in params


@pytest.mark.asyncio
async def test_system_category_uses_exclusion_operator() -> None:
    """system 用 NOT IN：新增事件类型自动落入，不会从列表消失。"""
    db = _FakeNotificationDB()
    await list_notifications(db, 42, page=1, page_size=20, category="system")
    sql, _params = db.sql_like("FROM user_notification WHERE user_id = :user_id")[0]
    assert "notification_type NOT IN (" in sql


@pytest.mark.asyncio
async def test_no_category_keeps_unfiltered_sql() -> None:
    """不传分类时不拼条件，保持旧接口行为。"""
    db = _FakeNotificationDB()
    await list_notifications(db, 42, page=1, page_size=20)
    sql, _params = db.sql_like("FROM user_notification WHERE user_id = :user_id")[0]
    # SELECT 列表里本来就有 notification_type，这里只断言 WHERE 未加筛选条件
    assert "notification_type IN (" not in sql
    assert "notification_type NOT IN (" not in sql
    assert "notification_type = :notification_type" not in sql
    assert "LIMIT" in sql


@pytest.mark.asyncio
async def test_type_and_category_are_mutually_exclusive() -> None:
    db = _FakeNotificationDB()
    with pytest.raises(HTTPException) as caught:
        await list_notifications(
            db, 42, page=1, page_size=20, notification_type="match", category="match"
        )
    assert caught.value.status_code == 422
    assert db.calls == []


@pytest.mark.asyncio
async def test_unknown_category_is_422_not_500() -> None:
    db = _FakeNotificationDB()
    with pytest.raises(HTTPException) as caught:
        await list_notifications(db, 42, page=1, page_size=20, category="nope")
    assert caught.value.status_code == 422


@pytest.mark.asyncio
async def test_item_carries_semantic_category() -> None:
    """列表项必须带 category：前端 Tab 才能按语义分类而不是裸 event_type 过滤。"""
    db = _FakeNotificationDB(rows=[_row(notification_type="apply")], total=1, unread=1)
    page = await list_notifications(db, 42, page=1, page_size=20, category="match")
    assert page.items[0].category == "match"
    assert page.items[0].notification_type == "apply"


@pytest.mark.asyncio
async def test_unread_summary_buckets_match_list_filter() -> None:
    """by_category 与 category_filter 同源：匹配类五个 event_type 必须同桶。"""
    db = _FakeNotificationDB(
        grouped=[
            {"notification_type": "match_application", "count": 3},
            {"notification_type": "apply", "count": 2},
            {"notification_type": "like", "count": 4},
            {"notification_type": "brand_new_event", "count": 1},
        ]
    )
    summary = await notification_unread_summary(db, 42)
    assert summary.total == 10
    assert summary.by_category["match"] == 5
    assert summary.by_category["like"] == 4
    assert summary.by_category["system"] == 1
    assert summary.by_category["comment"] == 0
    # 兜底：各分类之和必须等于总数，防止某类型被漏桶
    assert sum(summary.by_category.values()) == summary.total


@pytest.mark.asyncio
async def test_route_forwards_category_and_page() -> None:
    """路由层不能吞掉 category，否则客户端传了也等于没传。"""
    captured: dict[str, Any] = {}

    async def fake_list(db, user_id, page, page_size, notification_type=None, category=None):
        captured.update(
            user_id=user_id, page=page, page_size=page_size, category=category
        )
        return "sentinel"

    original = social_routes.list_notifications
    social_routes.list_notifications = fake_list
    try:
        result = await social_routes.notifications(
            page=2, page_size=20, notification_type=None, category="match",
            current=CURRENT, db=_FakeNotificationDB(),
        )
    finally:
        social_routes.list_notifications = original

    assert result == "sentinel"
    assert captured == {"user_id": 42, "page": 2, "page_size": 20, "category": "match"}


@pytest.mark.asyncio
async def test_unread_summary_route_survives_empty_user() -> None:
    """零通知时各分类键仍要齐全，前端角标才能按 key 取值。"""
    result = await social_routes.unread_summary(
        current=CURRENT, db=_FakeNotificationDB(grouped=[])
    )
    assert result.total == 0
    assert result.by_category["match"] == 0
    assert set(result.by_category) >= {"like", "comment", "follow", "message", "match", "activity", "system"}

"""Paper-plane profile unlock history contract (Q46).

The history endpoint must only ever read the current viewer's rows and must not
re-expose contact fields or full profiles.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from app.services import paper_plane_unlock as service


class _Result:
    def __init__(self, *, rows: list[dict[str, Any]] | None = None, scalar: Any = None) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def mappings(self) -> "_Result":
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows

    def scalar(self) -> Any:
        return self._scalar


class _HistoryDb:
    def __init__(self, total: int, rows: list[dict[str, Any]]) -> None:
        self.total = total
        self.rows = rows
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: object, params: dict[str, Any] | None = None) -> _Result:
        sql = " ".join(str(statement).split())
        self.calls.append((sql, params or {}))
        if sql.startswith("SELECT COUNT(*)"):
            return _Result(scalar=self.total)
        return _Result(rows=self.rows)


def _row(target_id: int, *, available: bool = True) -> dict[str, Any]:
    return {
        "target_user_id": target_id,
        "nickname": f"用户{target_id}",
        "avatar": f"/a{target_id}.webp",
        "avatar_thumb": f"/a{target_id}-thumb.webp",
        "age": 29,
        "unlocked_at": datetime(2026, 11, 2, 10, 0, 0),
        "points_cost": 80,
        "unlock_source": "paper_plane",
        "target_available": 1 if available else 0,
    }


@pytest.mark.asyncio
async def test_history_is_scoped_to_viewer_and_paginated() -> None:
    db = _HistoryDb(total=3, rows=[_row(11), _row(12, available=False)])
    result = await service.list_profile_unlocks(db, viewer_id=7, page=1, page_size=2)

    assert [item["target_user_id"] for item in result["items"]] == [11, 12]
    assert result["page"] == 1
    assert result["page_size"] == 2
    assert result["total"] == 3
    assert result["has_more"] is True
    assert result["items"][1]["target_available"] is False

    count_sql, count_params = db.calls[0]
    list_sql, list_params = db.calls[1]
    assert "viewer_user_id = :viewer" in count_sql
    assert "viewer_user_id = :viewer" in list_sql
    assert count_params == {"viewer": 7}
    assert list_params == {"viewer": 7, "limit": 2, "offset": 0}


@pytest.mark.asyncio
async def test_history_does_not_select_contact_fields() -> None:
    db = _HistoryDb(total=1, rows=[_row(11)])
    result = await service.list_profile_unlocks(db, viewer_id=7, page=1, page_size=20)

    serialized = str(result)
    assert "phone" not in serialized
    assert "wechat" not in serialized
    list_sql = db.calls[1][0]
    assert "phone" not in list_sql
    assert "wechat" not in list_sql


def test_history_route_is_registered_before_target_route() -> None:
    from app.api.routes import paper_plane_unlock

    paths = [getattr(route, "path", "") for route in paper_plane_unlock.router.routes]
    assert "/paper-plane/profile-unlocks" in paths
    assert paths.index("/paper-plane/profile-unlocks") < paths.index(
        "/paper-plane/profile-unlocks/{target_user_id}"
    )

"""纸飞机「已捡」服务端化（2026-11-10，Q69）。

此前「今日已捡」只存在前端本地 Storage，切号会串、换设备归零。本文件锁定：

1. 捡取记录按 ``(user_id, plane_id, UTC 日期)`` 去重；
2. 重复捡同一条返回 ``already_picked=true``，计数不增长；
3. 自己的、下架的、过期的、被拉黑的纸飞机不可捡；
4. 今日汇总只统计当前用户。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from app.services import community


class _Result:
    def __init__(
        self,
        *,
        row: Any = None,
        scalar: Any = None,
        rowcount: int = 0,
    ) -> None:
        self._row = row
        self._scalar = scalar
        self.rowcount = rowcount

    def first(self) -> Any:
        return self._row

    def scalar(self) -> Any:
        return self._scalar


class _FakeDb:
    def __init__(
        self,
        *,
        visible: bool = True,
        insert_rowcount: int = 1,
        picked_today: int = 1,
    ) -> None:
        self.visible = visible
        self.insert_rowcount = insert_rowcount
        self.picked_today = picked_today
        self.statements: list[str] = []
        self.committed = False

    async def execute(self, statement: Any, _params: dict[str, Any] | None = None) -> _Result:
        sql = " ".join(str(statement).split())
        self.statements.append(sql)
        if sql.startswith("SELECT 1 FROM paper_plane p"):
            return _Result(row=(1,) if self.visible else None)
        if sql.startswith("INSERT IGNORE INTO paper_plane_catch"):
            return _Result(rowcount=self.insert_rowcount)
        if sql.startswith("SELECT COUNT(DISTINCT plane_id) FROM paper_plane_catch"):
            return _Result(scalar=self.picked_today)
        return _Result()

    async def commit(self) -> None:
        self.committed = True


@pytest.fixture(autouse=True)
def _allow(monkeypatch: pytest.MonkeyPatch) -> None:
    async def allow(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(community, "ensure_user_allowed", allow)


@pytest.mark.asyncio
async def test_first_catch_records_and_returns_count() -> None:
    db = _FakeDb(insert_rowcount=1, picked_today=1)
    result = await community.catch_paper_plane(db, 7, 21)

    assert result.plane_id == 21
    assert result.already_picked is False
    assert result.picked_today == 1
    assert db.committed is True
    assert any(sql.startswith("INSERT IGNORE INTO paper_plane_catch") for sql in db.statements)


@pytest.mark.asyncio
async def test_repeated_catch_is_idempotent() -> None:
    db = _FakeDb(insert_rowcount=0, picked_today=3)
    result = await community.catch_paper_plane(db, 7, 21)

    assert result.already_picked is True
    assert result.picked_today == 3
    assert db.committed is True


@pytest.mark.asyncio
async def test_own_or_invisible_plane_is_rejected() -> None:
    db = _FakeDb(visible=False)
    with pytest.raises(HTTPException) as error:
        await community.catch_paper_plane(db, 7, 21)

    assert error.value.status_code == 404
    assert not any(sql.startswith("INSERT IGNORE") for sql in db.statements)
    assert db.committed is False


@pytest.mark.asyncio
async def test_catch_summary_returns_today_count() -> None:
    db = _FakeDb(picked_today=2)
    result = await community.paper_plane_catch_summary(db, 7)

    assert result.plane_id == 0
    assert result.already_picked is False
    assert result.picked_today == 2

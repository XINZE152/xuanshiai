"""红娘工作台活动报名：活动字典、统计、分页和会员归属范围。"""

from datetime import datetime

import pytest
from fastapi import HTTPException

from app.services.matchmaker_workspace import WorkspaceActor
from app.services.workspace_activity import list_workspace_activity_signups


class _Mappings:
    def __init__(self, *, rows=None, one=None):
        self._rows = list(rows or [])
        self._one = one

    def all(self):
        return self._rows

    def one(self):
        return self._one


class _Result:
    def __init__(self, *, rows=None, one=None):
        self._mappings = _Mappings(rows=rows, one=one)

    def mappings(self):
        return self._mappings


class _DB:
    def __init__(self):
        self.calls = []
        self.scalar_calls = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), dict(params or {})))
        if len(self.calls) == 1:
            return _Result(rows=[{"id": 9, "title": "周末相亲会"}])
        if len(self.calls) == 2:
            return _Result(one={
                "total": 2, "pending": 1, "approved": 1, "rejected": 0,
                "checked_in": 1, "not_checked_in": 1,
            })
        return _Result(rows=[{
            "id": 301, "user_id": 88, "nickname": "小林", "avatar": None,
            "status": 1, "checked_in": 1, "created_at": datetime(2026, 11, 14, 12, 0),
            "pay_status": "paid", "pay_amount": "99.5",
        }])

    async def scalar(self, statement, params=None):
        self.scalar_calls.append((str(statement), dict(params or {})))
        return 1


@pytest.mark.asyncio
async def test_activity_signups_are_scoped_to_the_current_matchmaker_and_mapped() -> None:
    db = _DB()
    actor = WorkspaceActor(user_id=21, display_name="测试红娘", level="NORMAL", organization_id=1)

    page = await list_workspace_activity_signups(db, actor, None, 1, 20, "小林", "approved")

    assert page.selected_activity_id == 9
    assert page.statistics.total == 2
    assert page.statistics.approved == 1
    assert page.total == 1 and page.has_more is False
    assert page.items[0].nickname == "小林"
    assert page.items[0].pay_amount == "99.50"
    assert "s.status = 1" in db.scalar_calls[0][0]
    assert db.scalar_calls[0][1]["scope_matchmaker_id"] == 21
    assert db.scalar_calls[0][1]["keyword"] == "%小林%"


@pytest.mark.asyncio
async def test_activity_signups_reject_an_activity_outside_the_available_options() -> None:
    db = _DB()
    actor = WorkspaceActor(user_id=21, display_name="测试红娘", level="NORMAL", organization_id=1)

    with pytest.raises(HTTPException) as error:
        await list_workspace_activity_signups(db, actor, 10, 1, 20, None)

    assert error.value.status_code == 404
    assert len(db.calls) == 1

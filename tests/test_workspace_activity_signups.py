"""红娘工作台活动报名：活动字典、统计、分页和会员归属范围。"""

from datetime import datetime

import pytest
from fastapi import HTTPException

from app.services.matchmaker_workspace import WorkspaceActor
from app.services.workspace_activity import check_in_workspace_signup, list_workspace_activity_signups


class _Mappings:
    def __init__(self, *, rows=None, one=None):
        self._rows = list(rows or [])
        self._one = one

    def all(self):
        return self._rows

    def one(self):
        return self._one

    def first(self):
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


class CheckInDB:
    def __init__(self, row):
        self.row = row
        self.calls = []
        self.commits = 0

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), dict(params or {})))
        return _Result(one=self.row)

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_check_in_is_scoped_and_audited():
    db = CheckInDB({'id': 10, 'status': 1, 'checked_in': 0})
    actor = WorkspaceActor(user_id=21, display_name='红娘', level='NORMAL', organization_id=1)
    result = await check_in_workspace_signup(db, actor, 10)
    assert result.checked_in is True
    assert db.calls[0][1]['scope_matchmaker_id'] == 21
    assert 'FOR UPDATE' in db.calls[0][0]
    assert 'UPDATE activity_signup SET checked_in = 1' in db.calls[1][0]
    assert 'business_audit_log' in db.calls[2][0]
    assert db.commits == 1


@pytest.mark.asyncio
async def test_check_in_repeated_is_idempotent():
    db = CheckInDB({'id': 10, 'status': 1, 'checked_in': 1})
    actor = WorkspaceActor(user_id=21, display_name='红娘', level='NORMAL', organization_id=1)
    assert (await check_in_workspace_signup(db, actor, 10)).checked_in
    assert len(db.calls) == 1 and db.commits == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('row,code', [(None, 404), ({'id': 10, 'status': 3, 'checked_in': 0}, 409)])
async def test_check_in_rejects_outside_scope_or_invalid_signup(row, code):
    db = CheckInDB(row)
    actor = WorkspaceActor(user_id=21, display_name='红娘', level='NORMAL', organization_id=1)
    with pytest.raises(HTTPException) as error:
        await check_in_workspace_signup(db, actor, 10)
    assert error.value.status_code == code
    assert len(db.calls) == 1 and db.commits == 0

"""P1 回归（2026-11-14）：C 端约见申请两个后端缺陷。

1. ``list_my_meeting_requests`` 此前不 JOIN ``users``，C 端拿到的
   ``user_nickname`` / ``target_nickname`` / ``matchmaker_name`` 恒为 null，
   聊天页卡片对象行只能兜底显示「对方」；
2. ``update_meeting_request`` 对 ``DECLINED`` / ``CLOSED`` 一律通知发起方，
   发起方自己撤回时会收到一条「约见申请未通过」的语义反转通知。
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.dependencies import CurrentUser
from app.schemas.meeting import MeetingStatusUpdate
from app.services import meeting as meetings


def result(row=None):
    return SimpleNamespace(
        first=lambda: SimpleNamespace(**row) if row else None,
        mappings=lambda: SimpleNamespace(first=lambda: row, one=lambda: row, all=lambda: [row] if row else []),
        scalar=lambda: None, lastrowid=1,
    )


def database(row=None):
    return SimpleNamespace(execute=AsyncMock(return_value=result(row)), scalar=AsyncMock(return_value=None), commit=AsyncMock())


INITIATOR = CurrentUser(id=62, session_id=1, phone="13800006101", status=1, realname_status=2)
TARGET = CurrentUser(id=63, session_id=1, phone="13800006102", status=1, realname_status=2)


@pytest.mark.asyncio
async def test_list_my_meeting_requests_joins_nicknames():
    row = {"id": 90, "user_id": 62, "target_user_id": 63, "matchmaker_id": 21,
           "service_id": None, "organization_id": 1, "status": "SUBMITTED", "note": "希望约会",
           "created_at": datetime(2026, 11, 3), "updated_at": datetime(2026, 11, 3),
           "user_nickname": "发起方", "target_nickname": "被邀方", "matchmaker_name": "红娘老师"}
    db = database(row)
    await meetings.list_my_meeting_requests(db, INITIATOR)
    sql = str(db.execute.await_args.args[0])
    assert "LEFT JOIN users u ON u.id = r.user_id" in sql
    assert "LEFT JOIN users t ON t.id = r.target_user_id" in sql
    assert "LEFT JOIN users m ON m.id = r.matchmaker_id" in sql
    responses = [call for call in db.execute.await_args_list]
    assert responses, "should issue exactly one query"


@pytest.mark.asyncio
async def test_list_my_meeting_requests_maps_nicknames_to_response():
    row = {"id": 90, "user_id": 62, "target_user_id": 63, "matchmaker_id": 21,
           "service_id": None, "organization_id": 1, "status": "SUBMITTED", "note": "希望约会",
           "created_at": datetime(2026, 11, 3), "updated_at": datetime(2026, 11, 3),
           "user_nickname": "发起方", "target_nickname": "被邀方", "matchmaker_name": "红娘老师"}
    db = database(row)
    items = await meetings.list_my_meeting_requests(db, INITIATOR)
    assert items[0].user_nickname == "发起方"
    assert items[0].target_nickname == "被邀方"
    assert items[0].matchmaker_name == "红娘老师"


def _update_row():
    return {"id": 90, "user_id": 62, "target_user_id": 63, "matchmaker_id": 21,
            "service_id": None, "organization_id": 1, "status": "SUBMITTED", "note": "希望约会",
            "created_at": datetime(2026, 11, 3), "updated_at": datetime(2026, 11, 3)}


async def _run_update(monkeypatch, current, status, reason=None):
    row = _update_row()
    db = database(row)
    notifications: list[dict] = []

    async def capture_notification(db_arg, **kwargs):
        notifications.append(kwargs)
        return True

    monkeypatch.setattr(meetings, "emit_notification", capture_notification)
    body = MeetingStatusUpdate(status=status, reason=reason) if reason is not None else MeetingStatusUpdate(status=status)
    await meetings.update_meeting_request(db, current, 90, body)
    return db, notifications


@pytest.mark.asyncio
async def test_declined_notifies_initiator_with_reason(monkeypatch):
    _, notifications = await _run_update(monkeypatch, TARGET, "DECLINED", "还不想见面")
    assert len(notifications) == 1
    assert notifications[0]["recipient_user_id"] == 62
    assert notifications[0]["title"] == "约见申请未通过"
    assert notifications[0]["content"] == "还不想见面"


@pytest.mark.asyncio
async def test_closed_by_initiator_notifies_target_not_self(monkeypatch):
    db, notifications = await _run_update(monkeypatch, INITIATOR, "CLOSED", "发起方主动撤回申请")
    assert len(notifications) == 1
    assert notifications[0]["recipient_user_id"] == 63, "撤回通知必须发给对方，而不是撤回者本人"
    assert notifications[0]["actor_user_id"] == 62
    assert notifications[0]["title"] == "约见申请已撤回"
    assert "撤回" in notifications[0]["content"]
    refund = [call for call in db.execute.await_args_list if "available_count = available_count + 1" in str(call.args[0])]
    assert refund, "CLOSED 应退还约见配额"


@pytest.mark.asyncio
async def test_closed_by_target_notifies_initiator(monkeypatch):
    _, notifications = await _run_update(monkeypatch, TARGET, "CLOSED", "对方关闭了约见申请")
    assert len(notifications) == 1
    assert notifications[0]["recipient_user_id"] == 62
    assert notifications[0]["title"] == "约见申请已关闭"


@pytest.mark.asyncio
async def test_accepted_notifies_initiator(monkeypatch):
    _, notifications = await _run_update(monkeypatch, TARGET, "ACCEPTED")
    assert len(notifications) == 1
    assert notifications[0]["recipient_user_id"] == 62
    assert notifications[0]["title"] == "约见申请已通过"

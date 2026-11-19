"""DATE-MGMT-20261103: ownership, consent, scheduling and completion barriers."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.dependencies import CurrentUser
from app.schemas.matchmaker_workspace import (
    WorkspaceMeetingDirectCreate, WorkspaceMeetingRecordStatusUpdate, WorkspaceMeetingScheduleCreate,
    WorkspaceMeetingStatusUpdate,
)
from app.schemas.meeting import MatchmakerMeetingRequestCreate, MeetingRequestCreate, MeetingStatusUpdate
from app.services import meeting as meetings
from app.services import matchmaker_workspace as workspace


ACTOR = workspace.WorkspaceActor(user_id=21, display_name="测试红娘", level="NORMAL", organization_id=1)


def result(row=None, scalar=None, lastrowid=1):
    return SimpleNamespace(
        first=lambda: SimpleNamespace(**row) if row else None,
        mappings=lambda: SimpleNamespace(first=lambda: row, one=lambda: row, all=lambda: [row] if row else []),
        scalar=lambda: scalar, lastrowid=lastrowid,
    )


def database(row=None):
    return SimpleNamespace(execute=AsyncMock(return_value=result(row)), scalar=AsyncMock(return_value=None), commit=AsyncMock())


def direct(**changes):
    return WorkspaceMeetingDirectCreate(**{
        "applicant_user_id": 62, "target_user_id": 63,
        "scheduled_at": "2030-11-04T18:00:00+08:00", "location": "测试咖啡馆",
        "contacts_confirmed": True, "applicant_agreed": True, "target_agreed": True, **changes,
    })


@pytest.mark.parametrize("field", ["contacts_confirmed", "applicant_agreed", "target_agreed"])
def test_manual_meeting_requires_every_confirmation(field):
    with pytest.raises(ValidationError):
        direct(**{field: False})


@pytest.mark.parametrize("content", [None, "", "   "])
def test_completed_requires_nonblank_follow_up(content):
    with pytest.raises(ValidationError):
        WorkspaceMeetingRecordStatusUpdate(status="COMPLETED", follow_up_content=content)


def test_schedule_normalizes_china_time_and_rejects_blank_location():
    assert direct().scheduled_at == datetime(2030, 11, 4, 18)
    with pytest.raises(ValidationError):
        direct(location=" ")
    with pytest.raises(ValidationError):
        direct(applicant_user_id=63)
    with pytest.raises(ValidationError):
        direct(scheduled_at="2000-01-01T12:00:00+08:00")


@pytest.mark.asyncio
async def test_user_application_records_applicants_current_matchmaker(monkeypatch):
    final = {"id": 90, "user_id": 62, "target_user_id": 63, "matchmaker_id": 21,
             "organization_id": 1, "service_id": None, "status": "SUBMITTED", "note": "希望约会",
             "created_at": datetime(2026, 11, 3), "updated_at": datetime(2026, 11, 3)}
    def execute(sql, params):
        query = str(sql)
        if "available_count FROM matchmaker_service_quota" in query:
            return result({"id": 4, "available_count": 1})
        if "SELECT matchmaker_id, organization_id FROM resource_assignment" in query:
            return result({"matchmaker_id": 21, "organization_id": 1})
        if "SELECT id FROM users" in query:
            return result(scalar=63)
        if "SELECT id, user_id" in query:
            return result(final)
        return result(lastrowid=90)
    db = database()
    db.execute.side_effect = execute
    monkeypatch.setattr(meetings, "ensure_users_can_interact", AsyncMock())
    monkeypatch.setattr(meetings, "emit_notification", AsyncMock())
    user = CurrentUser(id=62, session_id=1, phone="13800006101", status=1, realname_status=2)
    response = await meetings.create_meeting_request(db, user, MeetingRequestCreate(target_user_id=63, note="希望约会"))
    inserts = [call for call in db.execute.await_args_list if "INSERT INTO meeting_request" in str(call.args[0])]
    assert inserts[0].args[1]["matchmaker_id"] == 21
    assert inserts[0].args[1]["organization_id"] == 1
    assert response.matchmaker_id == 21
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_applicant_cannot_accept_own_invitation():
    db = database({"user_id": 62, "target_user_id": 63, "status": "SUBMITTED"})
    user = CurrentUser(id=62, session_id=1, phone="13800006101", status=1, realname_status=2)
    with pytest.raises(HTTPException) as exc:
        await meetings.update_meeting_request(db, user, 1, MeetingStatusUpdate(status="ACCEPTED"))
    assert exc.value.status_code == 403
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", [21, 22])
async def test_service_request_uses_current_applicant_ownership(monkeypatch, owner):
    final = {"id": 90, "user_id": 62, "target_user_id": 63, "matchmaker_id": 21,
             "organization_id": 1, "service_id": 7, "status": "SUBMITTED", "note": "希望约会",
             "created_at": datetime(2026, 11, 3), "updated_at": datetime(2026, 11, 3)}
    def execute(sql, params):
        query = str(sql)
        if "FROM matchmaker_service\n" in query:
            return result({"id": 7, "user_id": 62, "status": 1})
        if "available_count FROM matchmaker_service_quota" in query:
            return result({"id": 4, "available_count": 1})
        if "SELECT id FROM users" in query:
            return result(scalar=63)
        if "service_id, organization_id, status, note" in query:
            return result(final)
        return result(lastrowid=90)
    db = database()
    db.execute.side_effect = execute
    monkeypatch.setattr(meetings, "ensure_users_can_interact", AsyncMock())
    monkeypatch.setattr(meetings, "applicant_assignment", AsyncMock(return_value={"matchmaker_id": owner, "organization_id": 1}))
    user = CurrentUser(id=21, session_id=1, phone="13800006101", status=1, realname_status=2)
    body = MatchmakerMeetingRequestCreate(service_id=7, target_user_id=63, note="希望约会")
    if owner != 21:
        with pytest.raises(HTTPException) as exc:
            await meetings.create_matchmaker_meeting_request(db, user, body)
        assert exc.value.status_code == 409
        assert not any("UPDATE matchmaker_service_quota" in str(call.args[0]) for call in db.execute.await_args_list)
    else:
        await meetings.create_matchmaker_meeting_request(db, user, body)
        insert = next(call for call in db.execute.await_args_list if "INSERT INTO meeting_request" in str(call.args[0]))
        assert insert.args[1]["organization_id"] == 1
        assert insert.args[1]["matchmaker_id"] == 21


@pytest.mark.asyncio
async def test_manual_meeting_rejects_applicant_owned_by_another_matchmaker(monkeypatch):
    db = database()
    monkeypatch.setattr(workspace, "applicant_assignment", AsyncMock(return_value={"matchmaker_id": 22}))
    with pytest.raises(HTTPException) as exc:
        await workspace.create_workspace_meeting(db, ACTOR, direct())
    assert exc.value.status_code == 404
    assert not db.execute.await_args_list


@pytest.mark.asyncio
async def test_manual_meeting_passes_consents_to_persistent_schedule(monkeypatch):
    db = database({"id": 62})
    monkeypatch.setattr(workspace, "applicant_assignment", AsyncMock(return_value={"matchmaker_id": 21, "organization_id": 1}))
    monkeypatch.setattr(workspace, "_require_scoped_member", AsyncMock())
    monkeypatch.setattr(workspace, "_ensure_meeting_users_can_interact", AsyncMock())
    schedule = AsyncMock(return_value=SimpleNamespace(id=1))
    monkeypatch.setattr(workspace, "schedule_workspace_meeting", schedule)
    await workspace.create_workspace_meeting(db, ACTOR, direct())
    request = next(call for call in db.execute.await_args_list if "INSERT INTO meeting_request" in str(call.args[0]))
    assert request.args[1]["owner"] == 21
    assert "'ACCEPTED'" in str(request.args[0])
    schedule.assert_awaited_once()


@pytest.mark.asyncio
async def test_unaccepted_request_cannot_be_scheduled():
    db = database({"id": 1, "status": "SUBMITTED"})
    with pytest.raises(HTTPException) as exc:
        await workspace.schedule_workspace_meeting(db, ACTOR, 1, WorkspaceMeetingScheduleCreate(scheduled_at="2030-11-04T18:00:00+08:00", location="咖啡馆"))
    assert exc.value.status_code == 409
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_schedule_rejected_under_request_lock():
    db = database({"id": 1, "status": "ACCEPTED"})
    db.scalar.return_value = 1
    with pytest.raises(HTTPException) as exc:
        await workspace.schedule_workspace_meeting(db, ACTOR, 1, direct())
    assert exc.value.status_code == 409
    assert "FOR UPDATE" in str(db.execute.await_args.args[0])
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_completed_writes_follow_up_before_status_in_one_transaction():
    db = database({"id": 1, "status": "SCHEDULED", "scheduled_at": datetime(2000, 1, 1), "user_id": 62})
    await workspace.update_workspace_meeting_record_status(db, ACTOR, 1, WorkspaceMeetingRecordStatusUpdate(status="COMPLETED", follow_up_content=" 双方已见面，后续继续了解 "))
    calls = db.execute.await_args_list
    follow_index = next(i for i, call in enumerate(calls) if "INSERT INTO member_follow_up" in str(call.args[0]))
    status_index = next(i for i, call in enumerate(calls) if "UPDATE meeting_record SET status" in str(call.args[0]))
    assert follow_index < status_index
    assert calls[follow_index].args[1]["content"] == "双方已见面，后续继续了解"
    assert calls[follow_index].args[1]["user_id"] == 62
    assert "FOR UPDATE" in str(calls[0].args[0])
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_follow_up_write_failure_cannot_complete_meeting():
    db = database()
    def execute(sql, params):
        if "INSERT INTO member_follow_up" in str(sql):
            raise RuntimeError("database write failed")
        return result({"id": 1, "status": "SCHEDULED", "scheduled_at": datetime(2000, 1, 1), "user_id": 62})
    db.execute.side_effect = execute
    with pytest.raises(RuntimeError):
        await workspace.update_workspace_meeting_record_status(db, ACTOR, 1, WorkspaceMeetingRecordStatusUpdate(status="COMPLETED", follow_up_content="已完成跟进"))
    assert not any("UPDATE meeting_record" in str(call.args[0]) for call in db.execute.await_args_list)
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["CANCELLED", "NO_SHOW"])
async def test_failed_meeting_cannot_be_completed(status):
    db = database({"id": 1, "status": status, "scheduled_at": datetime(2000, 1, 1), "user_id": 62})
    with pytest.raises(HTTPException) as exc:
        await workspace.update_workspace_meeting_record_status(db, ACTOR, 1, WorkspaceMeetingRecordStatusUpdate(status="COMPLETED", follow_up_content="已跟进"))
    assert exc.value.status_code == 409
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_future_meeting_cannot_be_completed():
    db = database({"id": 1, "status": "SCHEDULED", "scheduled_at": datetime.now() + timedelta(days=2), "user_id": 62})
    with pytest.raises(HTTPException) as exc:
        await workspace.update_workspace_meeting_record_status(db, ACTOR, 1, WorkspaceMeetingRecordStatusUpdate(status="COMPLETED", follow_up_content="已跟进"))
    assert exc.value.status_code == 409
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_repeated_completion_does_not_duplicate_follow_up():
    db = database({"id": 1, "status": "COMPLETED", "scheduled_at": datetime(2000, 1, 1), "user_id": 62})
    db.scalar.return_value = "原有跟进"
    await workspace.update_workspace_meeting_record_status(db, ACTOR, 1, WorkspaceMeetingRecordStatusUpdate(status="COMPLETED", follow_up_content="重复提交"))
    assert not any("INSERT" in str(call.args[0]) for call in db.execute.await_args_list)


@pytest.mark.asyncio
async def test_matchmaker_cannot_mark_accepted_without_both_consents():
    db = database({"id": 1, "status": "SUBMITTED"})
    with pytest.raises(HTTPException) as exc:
        await workspace.update_workspace_meeting_status(db, ACTOR, 1, "ACCEPTED", WorkspaceMeetingStatusUpdate(status="ACCEPTED"))
    assert exc.value.status_code == 409
    db.commit.assert_not_awaited()


def test_legacy_ownership_fallback_is_scoped_to_applicant():
    params = {}
    clause = workspace._meeting_scope_predicate(ACTOR, "mr", params)
    assert "ra.user_id = mr.user_id" in clause
    assert "ORDER BY ra.id DESC LIMIT 1" in clause
    assert params == {"scope_matchmaker_id": 21}

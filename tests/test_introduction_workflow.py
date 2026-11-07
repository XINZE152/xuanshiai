"""牵线阶段与会员回应分离；逐条、逐红娘审计电话查看并校验状态流转。"""
from datetime import datetime

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.matchmaker_workspace import WorkspaceIntroductionCreate, WorkspaceIntroductionStatusUpdate
from app.services.matchmaker_workspace import (
    WorkspaceActor, _introduction_from_row, update_workspace_introduction_status,
    view_workspace_introduction_contact,
)


class Result:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def mappings(self):
        return self

    def scalars(self):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows


class DB:
    def __init__(self, status="PENDING", accessible=True):
        self.record = dict(id=12, status=status, from_user_id=1, to_user_id=2,
                           created_at=datetime(2026, 11, 2), updated_at=datetime(2026, 11, 2))
        self.members = {1: dict(id=1, nickname="申请人", real_name=None, phone="13800000001"),
                        2: dict(id=2, nickname="被牵线方", real_name=None, phone="13800000002")}
        self.audit = []
        self.sql = []
        self.accessible = accessible
        self.commits = 0

    async def execute(self, statement, params=None):
        sql, params = str(statement), dict(params or {})
        self.sql.append((sql, params))
        if "FROM matchmaker_introduction mi" in sql:
            assert "FOR UPDATE" in sql
            assert "mi.matchmaker_id" in sql or "mi.organization_id" in sql
            return Result([dict(self.record)] if self.accessible and params["id"] == 12 else [])
        if "FROM users u WHERE" in sql:
            member = self.members.get(params["member_id"])
            return Result([member] if member else [])
        if "INSERT INTO business_audit_log" in sql:
            self.audit.append(params)
            return Result()
        if "SELECT DISTINCT action FROM business_audit_log" in sql:
            assert "MAX(reset_log.id)" in sql
            reset = max((i for i, a in enumerate(self.audit) if a["action"].endswith("response.rejected") and a["resource_id"] == params["id"]), default=-1)
            return Result(a["action"] for i, a in enumerate(self.audit) if i > reset and a["actor_id"] == params["actor_id"] and a["resource_id"] == params["id"])
        if "UPDATE matchmaker_introduction" in sql:
            self.record["status"] = params.get("status", "IN_PROGRESS")
            self.record["failure_reason"] = params.get("reason")
            return Result()
        raise AssertionError(sql)

    async def commit(self):
        self.commits += 1


def actor(user_id=21):
    return WorkspaceActor(user_id=user_id, level="NORMAL", organization_id=3, display_name="红娘")


async def view_both(db, who=None):
    who = who or actor()
    await view_workspace_introduction_contact(db, who, 12, "from")
    return await view_workspace_introduction_contact(db, who, 12, "to", True)


@pytest.mark.asyncio
async def test_viewing_both_phones_requires_explicit_confirmation_and_retries_are_safe():
    db = DB()
    first = await view_workspace_introduction_contact(db, actor(), 12, "from")
    assert first.status == "PENDING" and first.from_phone_viewed and not first.to_phone_viewed
    assert first.contact.phone == "13800000001" and first.contact.wechat is None
    second = await view_workspace_introduction_contact(db, actor(), 12, "to")
    assert second.status == "PENDING" and second.from_phone_viewed and second.to_phone_viewed
    assert not any(a["action"].endswith("introduction.status") for a in db.audit)
    assert (await view_both(db)).status == "IN_PROGRESS"
    assert (await view_workspace_introduction_contact(db, actor(), 12, "to", True)).status == "IN_PROGRESS"
    transitions = [a for a in db.audit if a["action"].endswith("introduction.status")]
    assert len(transitions) == 1
    assert not any("138000" in str(a) for a in db.audit)


@pytest.mark.asyncio
async def test_missing_phone_does_not_count_as_viewed():
    db = DB()
    db.members[2]["phone"] = None
    await view_workspace_introduction_contact(db, actor(), 12, "from")
    result = await view_workspace_introduction_contact(db, actor(), 12, "to")
    assert result.status == "PENDING" and not result.to_phone_viewed
    assert result.contact.phone is None
    with pytest.raises(HTTPException) as error:
        await view_workspace_introduction_contact(db, actor(), 12, "to", True)
    assert error.value.status_code == 409
    assert db.record["status"] == "PENDING"


@pytest.mark.asyncio
async def test_viewing_different_members_or_by_different_actors_cannot_complete_pair():
    db = DB()
    await view_workspace_introduction_contact(db, actor(21), 12, "from")
    result = await view_workspace_introduction_contact(db, actor(22), 12, "to")
    assert result.status == "PENDING" and not result.from_phone_viewed
    with pytest.raises(HTTPException) as error:
        await view_workspace_introduction_contact(db, actor(), 999, "to")
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_outside_scope_and_deleted_member_do_not_reveal_or_advance():
    db = DB(accessible=False)
    with pytest.raises(HTTPException) as error:
        await view_workspace_introduction_contact(db, actor(), 12, "from")
    assert error.value.status_code == 404 and not db.audit
    db.accessible = True
    del db.members[1]
    with pytest.raises(HTTPException):
        await view_workspace_introduction_contact(db, actor(), 12, "from")
    assert not db.audit and db.record["status"] == "PENDING"


@pytest.mark.asyncio
async def test_rejection_invalidates_previous_round_phone_views():
    db = DB()
    await view_both(db)
    db.record["status"] = "PENDING"
    db.audit.append(dict(actor_id=2, resource_id=12, action="matchmaker_workspace.introduction.response.rejected"))
    result = await view_workspace_introduction_contact(db, actor(), 12, "from")
    assert result.status == "PENDING" and not result.to_phone_viewed
    assert (await view_workspace_introduction_contact(db, actor(), 12, "to")).status == "PENDING"
    assert (await view_workspace_introduction_contact(db, actor(), 12, "to", True)).status == "IN_PROGRESS"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED", "IN_PROGRESS"])
async def test_pending_cannot_manually_skip_phone_views(status):
    db = DB()
    with pytest.raises(HTTPException) as error:
        await update_workspace_introduction_status(db, actor(), 12, status, "态度不合适", True)
    assert error.value.status_code == 409 and db.record["status"] == "PENDING"


@pytest.mark.asyncio
async def test_progress_requires_contact_confirmation_and_same_actor_phone_views():
    db = DB()
    await view_both(db)
    with pytest.raises(HTTPException) as error:
        await update_workspace_introduction_status(db, actor(), 12, "SUCCEEDED", None)
    assert error.value.status_code == 409
    with pytest.raises(HTTPException):
        await update_workspace_introduction_status(db, actor(22), 12, "SUCCEEDED", None, True)
    await update_workspace_introduction_status(db, actor(), 12, "SUCCEEDED", None, True)
    assert db.record["status"] == "SUCCEEDED"
    assert any(a["action"].endswith("contact_confirmed") for a in db.audit)
    count = len(db.audit)
    await update_workspace_introduction_status(db, actor(), 12, "SUCCEEDED", None, True)
    assert len(db.audit) == count
    with pytest.raises(HTTPException):
        await update_workspace_introduction_status(db, actor(), 12, "PENDING", None)


@pytest.mark.asyncio
async def test_failure_requires_reason_and_terminal_phone_reads_do_not_reopen():
    db = DB()
    await view_both(db)
    with pytest.raises(HTTPException) as error:
        await update_workspace_introduction_status(db, actor(), 12, "FAILED", " ", True)
    assert error.value.status_code == 422
    await update_workspace_introduction_status(db, actor(), 12, "FAILED", "对方表示不合适", True)
    assert db.record["failure_reason"] == "对方表示不合适"
    assert (await view_workspace_introduction_contact(db, actor(), 12, "to")).status == "FAILED"


def test_new_introductions_are_pending_and_legacy_terminal_create_is_rejected():
    assert WorkspaceIntroductionCreate(from_user_id=1, to_user_id=2).result == "PENDING"
    with pytest.raises(ValidationError):
        WorkspaceIntroductionCreate(from_user_id=1, to_user_id=2, result="SUCCEEDED")
    assert WorkspaceIntroductionStatusUpdate(status="SUCCEEDED").contact_confirmed is False


@pytest.mark.parametrize("application_status,response", [(None, "UNANSWERED"), (0, "UNANSWERED"), (1, "ACCEPTED"), (2, "REJECTED"), (3, "UNANSWERED")])
def test_application_response_is_independent_of_introduction_stage(application_status, response):
    row = dict(id=12, status="PENDING", failure_reason="暂不考虑", note="discovery-apply:501",
               created_at=datetime(2026, 11, 2), updated_at=datetime(2026, 11, 2),
               from_user_id=1, from_nickname="甲", from_avatar=None, from_gender=1,
               to_user_id=2, to_nickname="乙", to_avatar=None, to_gender=2, application_status=application_status)
    result = _introduction_from_row(row)
    assert result.application_response == response and result.status == "PENDING"
    assert result.response_reason == ("暂不考虑" if application_status == 2 else None)


def test_http_contact_view_and_result_contract(monkeypatch):
    from fastapi.testclient import TestClient
    from app.api.dependencies import CurrentUser, get_current_user
    from app.api.routes import matchmaker_workspace as routes
    from app.db.session import get_db
    from app.main import app

    db = DB()
    client = TestClient(app)
    url = "/api/v1/matchmaker/workbench/introductions/12"
    assert client.post(url + "/contact-views", json={"side": "from"}).status_code == 401

    async def fake_db():
        yield db

    async def fake_user():
        return CurrentUser(id=21, session_id=1, phone=None, status=1, realname_status=2)

    async def workspace(_db, _current):
        return actor()

    monkeypatch.setattr(routes, "require_workspace", workspace)
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_current_user] = fake_user
    try:
        assert client.post(url + "/contact-views", json={"side": "other"}).status_code == 422
        assert client.post(url.replace("12", "0") + "/contact-views", json={"side": "from"}).status_code == 422
        first = client.post(url + "/contact-views", json={"side": "from"})
        assert first.status_code == 200 and first.json()["status"] == "PENDING"
        second = client.post(url + "/contact-views", json={"side": "to"})
        assert second.status_code == 200 and second.json()["status"] == "PENDING"
        confirmed = client.post(url + "/contact-views", json={"side": "to", "contacts_obtained": True})
        assert confirmed.status_code == 200 and confirmed.json()["status"] == "IN_PROGRESS"
        assert client.patch(url + "/status", json={"status": "SUCCEEDED"}).status_code == 409
        response = client.patch(url + "/status", json={"status": "SUCCEEDED", "contact_confirmed": True})
        assert response.status_code == 204 and response.content == b""

        async def denied(_db, _current):
            raise HTTPException(403, detail="无红娘工作台权限")

        monkeypatch.setattr(routes, "require_workspace", denied)
        assert client.post(url + "/contact-views", json={"side": "from"}).status_code == 403
        schemas = app.openapi()["components"]["schemas"]
        assert schemas["WorkspaceIntroductionPage"]["properties"]["workflow_version"]["const"] == 2
        assert "contact_confirmed" in schemas["WorkspaceIntroductionStatusUpdate"]["properties"]
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)

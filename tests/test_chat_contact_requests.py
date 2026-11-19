import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from app.schemas.social import ChatSessionRequestCreate
from app.services import social


class Result:
    lastrowid = 91

    def __init__(self, value=None):
        self.value = value

    def scalar(self):
        return self.value

    def mappings(self):
        return self

    def one(self):
        return self.value

    def first(self):
        return self.value

    def all(self):
        return [self.value] if self.value is not None else []


class ContactDB:
    def __init__(self, phone="13800000000"):
        self.phone = phone
        self.row = None

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "SELECT phone FROM users" in sql:
            return Result(self.phone)
        if "SELECT id FROM chat_session_request" in sql:
            return Result(91 if self.row and self.row["status"] == "PENDING" else None)
        if "INSERT INTO chat_session_request" in sql:
            self.row = dict(id=91, session_id=7, requester_id=1, responder_id=2,
                            request_type=params["request_type"], payload=params["payload"],
                            status="PENDING", expire_at=params["expire_at"],
                            responded_at=None, created_at=datetime.now(UTC).replace(tzinfo=None))
            return Result()
        if "UPDATE chat_session_request SET status = :status" in sql:
            self.row["status"] = params["status"]
        return Result(self.row)

    async def commit(self):
        pass


@pytest.fixture(autouse=True)
def isolate_session_and_notifications(monkeypatch):
    async def session(db, user_id, session_id):
        if user_id not in (1, 2) or session_id != 7:
            raise HTTPException(403, detail="无权访问会话")
        return {}, 2 if user_id == 1 else 1

    async def notify(*args, **kwargs):
        pass

    monkeypatch.setattr(social, "_session", session)
    monkeypatch.setattr(social, "emit_notification", notify)


@pytest.mark.asyncio
async def test_wechat_contact_is_hidden_until_recipient_accepts():
    db = ContactDB()
    created = await social.create_chat_session_request(db, 1, 7, ChatSessionRequestCreate(
        request_type="WECHAT", payload={"contact_value": " test_wechat ", "extra_secret": "drop"}))
    assert created.status == "PENDING" and created.payload is None
    assert json.loads(db.row["payload"]) == {"contact_value": "test_wechat"}
    for viewer in (1, 2):
        assert (await social.list_chat_session_requests(db, viewer, 7))[0].payload is None
    with pytest.raises(HTTPException) as error:
        await social.respond_chat_session_request(db, 1, 91, "ACCEPT")
    assert error.value.status_code == 403
    accepted = await social.respond_chat_session_request(db, 2, 91, "ACCEPT")
    assert accepted.payload == {"contact_value": "test_wechat"}
    for viewer in (1, 2):
        assert (await social.list_chat_session_requests(db, viewer, 7))[0].payload == accepted.payload
    with pytest.raises(HTTPException) as error:
        await social.list_chat_session_requests(db, 3, 7)
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_phone_uses_authenticated_accounts_bound_number():
    db = ContactDB()
    created = await social.create_chat_session_request(db, 1, 7, ChatSessionRequestCreate(
        request_type="PHONE", payload={"contact_value": "13999999999"}))
    assert created.payload is None
    accepted = await social.respond_chat_session_request(db, 2, 91, "ACCEPT")
    assert accepted.payload == {"contact_value": db.phone}


@pytest.mark.asyncio
@pytest.mark.parametrize("phone", [None, "", "invalid"])
async def test_phone_requires_bound_number(phone):
    db = ContactDB(phone)
    with pytest.raises(HTTPException) as error:
        await social.create_chat_session_request(db, 1, 7, ChatSessionRequestCreate(request_type="PHONE"))
    assert error.value.status_code == 422 and db.row is None


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [None, {}, {"contact_value": "bad"}, {"contact_value": 123456}])
async def test_invalid_wechat_is_not_stored(payload):
    db = ContactDB()
    with pytest.raises(HTTPException) as error:
        await social.create_chat_session_request(db, 1, 7, ChatSessionRequestCreate(request_type="WECHAT", payload=payload))
    assert error.value.status_code == 422 and db.row is None


@pytest.mark.asyncio
@pytest.mark.parametrize("action,actor,status", [("REJECT", 2, "REJECTED"), ("WITHDRAW", 1, "WITHDRAWN")])
async def test_rejection_and_withdrawal_never_return_contact(action, actor, status):
    db = ContactDB()
    await social.create_chat_session_request(db, 1, 7, ChatSessionRequestCreate(request_type="PHONE"))
    result = await social.respond_chat_session_request(db, actor, 91, action)
    assert result.status == status and result.payload is None


@pytest.mark.asyncio
async def test_duplicate_or_expired_request_cannot_be_accepted():
    db = ContactDB()
    request = ChatSessionRequestCreate(request_type="PHONE")
    await social.create_chat_session_request(db, 1, 7, request)
    with pytest.raises(HTTPException) as error:
        await social.create_chat_session_request(db, 1, 7, request)
    assert error.value.status_code == 409
    db.row["expire_at"] = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
    with pytest.raises(HTTPException) as error:
        await social.respond_chat_session_request(db, 2, 91, "ACCEPT")
    assert error.value.status_code == 409
    assert social._request_response(db.row).payload is None


def test_non_contact_request_payload_is_unchanged():
    row = dict(id=1, session_id=7, requester_id=1, responder_id=2,
               request_type="ANSWER_EXCHANGE", payload={"question": "hello"}, status="PENDING",
               expire_at=None, responded_at=None, created_at=datetime.now(UTC))
    assert social._request_response(row).payload == row["payload"]

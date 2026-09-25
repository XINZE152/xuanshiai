from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.schemas.social import ChatMessageCreate
from app.services import social as social_service


INDEX_NAME = "uq_chat_message_sender_session_client_message"


def _duplicate_error(error_code: int = 1062, index_name: str = INDEX_NAME) -> IntegrityError:
    class DriverError(Exception):
        def __init__(self) -> None:
            self.errno = error_code
            super().__init__(f"Duplicate entry for key '{index_name}'")

    return IntegrityError("INSERT INTO chat_message", {}, DriverError())


class FakeResult:
    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        *,
        lastrowid: int | None = None,
        scalar_value: Any = None,
    ) -> None:
        self.rows = rows or []
        self.lastrowid = lastrowid
        self.scalar_value = scalar_value

    def mappings(self) -> FakeResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None

    def one(self) -> dict[str, Any]:
        assert len(self.rows) == 1
        return self.rows[0]

    def all(self) -> list[dict[str, Any]]:
        return self.rows

    def scalar(self) -> Any:
        return self.scalar_value


class FakeSession:
    def __init__(
        self,
        trace: list[str],
        *,
        conflict_on_insert: bool = False,
        competitor_payload: dict[str, Any] | None = None,
        insert_error: IntegrityError | None = None,
    ) -> None:
        self.trace = trace
        self.messages: dict[int, dict[str, Any]] = {}
        self.next_id = 1
        self.insert_attempts = 0
        self.session_updates = 0
        self.commit_count = 0
        self.rollback_count = 0
        self.conflict_on_insert = conflict_on_insert
        self.conflict_triggered = False
        self.competitor_payload = competitor_payload or {}
        self.insert_error = insert_error

    def _message_from_insert(self, params: dict[str, Any], *, competitor: bool = False) -> dict[str, Any]:
        row = {
            "id": self.next_id,
            "session_id": params["session_id"],
            "from_user_id": params["from_id"],
            "to_user_id": params["to_id"],
            "type": params["type"],
            "content": params["content"],
            "media_url": params["media_url"],
            "client_message_id": params["client_message_id"],
            "is_read": 0,
            "revoked_at": None,
            "created_at": datetime(2026, 9, 23, 10, 0, 0),
        }
        if competitor:
            row.update(self.competitor_payload)
        self.messages[self.next_id] = row
        self.next_id += 1
        return row

    async def execute(self, statement: object, params: dict[str, Any] | None = None) -> FakeResult:
        sql = " ".join(str(statement).split())
        params = params or {}
        if "client_message_id = :client_message_id" in sql:
            self.trace.append("lookup")
            row = next(
                (
                    item
                    for item in self.messages.values()
                    if item["from_user_id"] == params["user_id"]
                    and item["session_id"] == params["session_id"]
                    and item["client_message_id"] == params["client_message_id"]
                ),
                None,
            )
            return FakeResult([row] if row else [])

        if "INSERT INTO chat_message" in sql:
            self.trace.append("insert")
            self.insert_attempts += 1
            if self.insert_error is not None:
                raise self.insert_error
            if self.conflict_on_insert and not self.conflict_triggered:
                self.conflict_triggered = True
                row = self._message_from_insert(params, competitor=True)
                raise _duplicate_error()
            if params["client_message_id"] is not None and any(
                row["from_user_id"] == params["from_id"]
                and row["session_id"] == params["session_id"]
                and row["client_message_id"] == params["client_message_id"]
                for row in self.messages.values()
            ):
                raise _duplicate_error()
            row = self._message_from_insert(params)
            return FakeResult(lastrowid=row["id"])

        if "UPDATE chat_session SET last_message" in sql:
            self.trace.append("session_update")
            self.session_updates += 1
            return FakeResult()

        if "SELECT revoked_at FROM chat_message" in sql:
            row = self.messages.get(params["id"])
            return FakeResult(scalar_value=row["revoked_at"] if row else None)

        if "UPDATE chat_message SET revoked_at" in sql:
            row = self.messages.get(params["id"])
            if row:
                row["revoked_at"] = datetime(2026, 9, 23, 10, 1, 0)
            return FakeResult()

        if "WHERE id = :id AND from_user_id = :user_id" in sql:
            row = self.messages.get(params["id"])
            if row and row["from_user_id"] == params["user_id"]:
                return FakeResult([row])
            return FakeResult()

        if "FROM chat_message WHERE id = :id" in sql:
            row = self.messages.get(params["id"])
            return FakeResult([row] if row else [])

        if "FROM chat_message WHERE session_id = :session_id" in sql:
            rows = sorted(self.messages.values(), key=lambda item: item["id"], reverse=True)
            return FakeResult(rows)

        return FakeResult()

    async def commit(self) -> None:
        self.trace.append("commit")
        self.commit_count += 1

    async def rollback(self) -> None:
        self.trace.append("rollback")
        self.rollback_count += 1


@pytest.fixture

def social_fakes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    trace: list[str] = []

    async def ensure_allowed(_db: object, _user_id: int, _restriction: str) -> None:
        trace.append("restriction")

    async def session(_db: object, user_id: int, _session_id: int) -> tuple[dict[str, int], int]:
        trace.append("session")
        target_id = user_id + 1000
        return {"user1_id": user_id, "user2_id": target_id}, target_id

    async def ensure_privacy(_db: object, _sender_id: int, _recipient_id: int) -> None:
        trace.append("privacy")

    async def notify(_db: object, **_kwargs: Any) -> None:
        trace.append("notification")

    monkeypatch.setattr(social_service, "ensure_user_allowed", ensure_allowed)
    monkeypatch.setattr(social_service, "_session", session)
    monkeypatch.setattr(social_service, "_ensure_message_privacy", ensure_privacy)
    monkeypatch.setattr(social_service, "emit_notification", notify)
    return trace


def test_chat_message_id_is_optional_trimmed_and_limited() -> None:
    assert ChatMessageCreate(content="hello").client_message_id is None
    assert ChatMessageCreate(content="hello", client_message_id="  client-1 \t").client_message_id == "client-1"
    assert ChatMessageCreate(content="hello", client_message_id="x" * 128).client_message_id == "x" * 128

    with pytest.raises(ValidationError):
        ChatMessageCreate(content="hello", client_message_id=" \t ")
    with pytest.raises(ValidationError):
        ChatMessageCreate(content="hello", client_message_id="x" * 129)


@pytest.mark.asyncio
async def test_first_send_and_same_payload_replay_have_no_duplicate_effects(
    social_fakes: list[str],
) -> None:
    db = FakeSession(social_fakes)
    request = ChatMessageCreate(content="hello", client_message_id=" client-1 ")

    created = await social_service.send_message(db, 7, 20, request)
    replayed = await social_service.send_message(db, 7, 20, request)

    assert created.id == replayed.id
    assert created.created_at == replayed.created_at
    assert replayed.client_message_id == "client-1"
    assert db.insert_attempts == 1
    assert db.session_updates == 1
    assert db.commit_count == 1
    assert social_fakes.count("notification") == 1


@pytest.mark.asyncio
async def test_same_id_with_different_payload_returns_409(social_fakes: list[str]) -> None:
    db = FakeSession(social_fakes)
    await social_service.send_message(db, 7, 20, ChatMessageCreate(content="hello", client_message_id="key"))

    with pytest.raises(HTTPException) as exc:
        await social_service.send_message(
            db, 7, 20, ChatMessageCreate(content="changed", client_message_id="key")
        )

    assert exc.value.status_code == 409
    assert "client_message_id" in exc.value.detail
    assert db.insert_attempts == 1
    assert db.session_updates == 1
    assert social_fakes.count("notification") == 1


@pytest.mark.asyncio
async def test_same_key_isolated_by_sender_and_session(social_fakes: list[str]) -> None:
    db = FakeSession(social_fakes)
    request = ChatMessageCreate(content="hello", client_message_id="reusable-key")

    first = await social_service.send_message(db, 7, 20, request)
    other_sender = await social_service.send_message(db, 8, 20, request)
    other_session = await social_service.send_message(db, 7, 21, request)

    assert len({first.id, other_sender.id, other_session.id}) == 3
    assert db.insert_attempts == 3
    assert db.session_updates == 3
    assert social_fakes.count("notification") == 3


@pytest.mark.asyncio
async def test_legacy_client_without_id_keeps_non_idempotent_behavior(
    social_fakes: list[str],
) -> None:
    db = FakeSession(social_fakes)
    request = ChatMessageCreate(content="hello")

    first = await social_service.send_message(db, 7, 20, request)
    second = await social_service.send_message(db, 7, 20, request)

    assert first.id != second.id
    assert first.client_message_id is None
    assert second.client_message_id is None
    assert db.insert_attempts == 2
    assert db.session_updates == 2
    assert social_fakes.count("notification") == 2


@pytest.mark.asyncio
async def test_all_permission_checks_run_before_replay_lookup(social_fakes: list[str]) -> None:
    db = FakeSession(social_fakes)
    request = ChatMessageCreate(content="hello", client_message_id="key")
    await social_service.send_message(db, 7, 20, request)
    social_fakes.clear()

    await social_service.send_message(db, 7, 20, request)

    assert social_fakes[:4] == ["restriction", "session", "privacy", "lookup"]
    assert "insert" not in social_fakes
    assert "session_update" not in social_fakes
    assert "notification" not in social_fakes


@pytest.mark.asyncio
async def test_unique_conflict_recovers_to_the_committed_message(social_fakes: list[str]) -> None:
    db = FakeSession(social_fakes, conflict_on_insert=True)

    response = await social_service.send_message(
        db, 7, 20, ChatMessageCreate(content="hello", client_message_id="key")
    )

    assert response.id == 1
    assert response.client_message_id == "key"
    assert db.rollback_count == 1
    assert db.session_updates == 0
    assert db.commit_count == 0
    assert social_fakes.count("notification") == 0
    assert social_fakes[-2:] == ["rollback", "lookup"]


@pytest.mark.asyncio
async def test_unique_conflict_recovery_checks_payload(social_fakes: list[str]) -> None:
    db = FakeSession(
        social_fakes,
        conflict_on_insert=True,
        competitor_payload={"content": "another payload"},
    )

    with pytest.raises(HTTPException) as exc:
        await social_service.send_message(
            db, 7, 20, ChatMessageCreate(content="hello", client_message_id="key")
        )

    assert exc.value.status_code == 409
    assert db.rollback_count == 1
    assert db.session_updates == 0
    assert social_fakes.count("notification") == 0


@pytest.mark.asyncio
async def test_unrelated_integrity_error_is_not_swallowed(social_fakes: list[str]) -> None:
    db = FakeSession(social_fakes, insert_error=_duplicate_error(1452, "fk_chat_message_session_id"))

    with pytest.raises(IntegrityError):
        await social_service.send_message(
            db, 7, 20, ChatMessageCreate(content="hello", client_message_id="key")
        )

    assert db.rollback_count == 0
    assert db.session_updates == 0
    assert social_fakes.count("notification") == 0


@pytest.mark.asyncio
async def test_history_cursor_and_recall_responses_project_client_message_id(
    social_fakes: list[str],
) -> None:
    db = FakeSession(social_fakes)
    created = await social_service.send_message(
        db, 7, 20, ChatMessageCreate(content="hello", client_message_id="key")
    )

    history = await social_service.list_messages(db, 7, 20, page=1, page_size=20)
    cursor_page = await social_service.list_messages_cursor(db, 7, 20, cursor=None, page_size=20)
    recalled = await social_service.recall_message(db, 7, created.id)

    assert history[0].client_message_id == "key"
    assert cursor_page.items[0].client_message_id == "key"
    assert recalled.client_message_id == "key"

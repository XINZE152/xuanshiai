"""Q63: hide-one-message-for-me contract."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from app.api.routes import social as social_routes
from app.services import social as social_service


class _Result:
    def __init__(self, *, rows: list[dict[str, Any]] | None = None, rowcount: int = 0, scalar: Any = None) -> None:
        self._rows = rows or []
        self.rowcount = rowcount
        self._scalar = scalar

    def mappings(self) -> "_Result":
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict[str, Any]]:
        return self._rows

    def scalar(self) -> Any:
        return self._scalar


class _HideDb:
    def __init__(self, *, rowcount: int = 1) -> None:
        self.rowcount = rowcount
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.commits = 0

    async def execute(self, statement: object, params: dict[str, Any] | None = None) -> _Result:
        sql = " ".join(str(statement).split())
        self.calls.append((sql, params or {}))
        if sql.startswith("SELECT id, session_id, to_user_id, is_read"):
            return _Result(rows=[{"id": 7, "session_id": 3, "to_user_id": 7, "is_read": 0}])
        if sql.startswith("INSERT IGNORE INTO chat_message_hidden"):
            return _Result(rowcount=self.rowcount)
        return _Result()

    async def commit(self) -> None:
        self.commits += 1


class _ListDb:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: object, params: dict[str, Any] | None = None) -> _Result:
        self.calls.append((" ".join(str(statement).split()), params or {}))
        return _Result()


@pytest.mark.asyncio
async def test_hide_decrements_unread_and_is_idempotent(monkeypatch) -> None:
    async def fake_session(_db, _user_id, _session_id):
        return {"id": 3, "user1_id": 7, "user2_id": 8}, 8

    monkeypatch.setattr(social_service, "_session", fake_session)
    db = _HideDb(rowcount=1)

    await social_service.hide_message_for_me(db, 7, 7)

    assert db.commits == 1
    update_calls = [call for call in db.calls if call[0].startswith("UPDATE chat_session")]
    assert update_calls
    assert "unread_count_user1" in update_calls[0][0]
    assert "GREATEST" in update_calls[0][0]

    db_again = _HideDb(rowcount=0)
    await social_service.hide_message_for_me(db_again, 7, 7)
    assert not [call for call in db_again.calls if call[0].startswith("UPDATE chat_session")]


@pytest.mark.asyncio
async def test_hide_rejects_missing_message(monkeypatch) -> None:
    class MissingDb:
        async def execute(self, *_args, **_kwargs):
            return _Result()

    async def fake_session(_db, _user_id, _session_id):
        return {"id": 3, "user1_id": 7, "user2_id": 8}, 8

    monkeypatch.setattr(social_service, "_session", fake_session)
    with pytest.raises(HTTPException) as exc_info:
        await social_service.hide_message_for_me(MissingDb(), 7, 999)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_message_lists_exclude_hidden_rows(monkeypatch) -> None:
    async def fake_session(_db, _user_id, _session_id):
        return {"id": 3, "user1_id": 7, "user2_id": 8}, 8

    monkeypatch.setattr(social_service, "_session", fake_session)
    db = _ListDb()
    await social_service.list_messages(db, 7, 3, 1, 20)
    assert "chat_message_hidden" in db.calls[0][0]
    assert db.calls[0][1]["user_id"] == 7

    db_cursor = _ListDb()
    await social_service.list_messages_cursor(db_cursor, 7, 3, None, 20)
    assert "chat_message_hidden" in db_cursor.calls[0][0]
    assert db_cursor.calls[0][1]["user_id"] == 7


def test_session_preview_falls_back_to_visible_message() -> None:
    assert social_service._session_preview({"last_visible_type": None})[0] == "暂无消息"
    assert social_service._session_preview({"last_visible_type": 1, "last_visible_content": "你好"})[0] == "你好"
    assert social_service._session_preview({"last_visible_type": 2})[0] == "[图片]"


def test_for_me_route_is_registered() -> None:
    paths = [getattr(route, "path", "") for route in social_routes.router.routes]
    assert "/chat/messages/{message_id}/for-me" in paths

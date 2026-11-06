"""Chat message quote contract (Q63, quote part only).

Deletion semantics are intentionally out of scope for this batch.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.social import ChatMessageCreate
from app.services import social as social_service


class _Result:
    def __init__(self, *, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> "_Result":
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _QuoteDb:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[dict[str, Any]] = []

    async def execute(self, _statement: object, params: dict[str, Any] | None = None) -> _Result:
        self.calls.append(params or {})
        return _Result(rows=self.rows)


def _message_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "id": 100,
        "session_id": 9,
        "from_user_id": 7,
        "to_user_id": 8,
        "type": 1,
        "content": "回复内容",
        "media_url": None,
        "client_message_id": None,
        "quote_message_id": 50,
        "is_read": 0,
        "revoked_at": None,
        "created_at": datetime(2026, 11, 2, 10, 0, 0),
    }
    row.update(overrides)
    return row


def test_quote_message_id_is_optional_and_positive() -> None:
    assert ChatMessageCreate(content="hi").quote_message_id is None
    assert ChatMessageCreate(content="hi", quote_message_id=3).quote_message_id == 3
    with pytest.raises(ValidationError):
        ChatMessageCreate(content="hi", quote_message_id=0)


@pytest.mark.asyncio
async def test_quote_must_belong_to_same_session() -> None:
    db = _QuoteDb([])
    with pytest.raises(HTTPException) as exc_info:
        await social_service._validate_quote(db, 9, 50)

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "QUOTE_MESSAGE_NOT_FOUND"
    assert db.calls == [{"id": 50, "session_id": 9}]


@pytest.mark.asyncio
async def test_quote_rejects_revoked_message() -> None:
    db = _QuoteDb([{"id": 50, "revoked_at": datetime(2026, 11, 2, 9, 0, 0)}])
    with pytest.raises(HTTPException) as exc_info:
        await social_service._validate_quote(db, 9, 50)

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "QUOTE_MESSAGE_REVOKED"


@pytest.mark.asyncio
async def test_load_quotes_batches_ids_and_projects_quote() -> None:
    db = _QuoteDb(
        [
            {"id": 50, "session_id": 9, "from_user_id": 8, "type": 1, "content": "原文", "revoked_at": None},
        ]
    )
    rows = [
        _message_row(),
        _message_row(id=101, quote_message_id=None),
        _message_row(id=102, quote_message_id=50),
    ]
    quotes = await social_service._load_quotes(db, rows)

    assert set(quotes) == {50}
    assert db.calls == [{"q0": 50}]

    message = social_service._message(rows[0], viewer=7, quotes=quotes)
    assert message.quote_message_id == 50
    assert message.quote is not None
    assert message.quote.id == 50
    assert message.quote.content == "原文"
    assert message.quote.from_user_id == 8
    assert message.quote.revoked is False


def test_idempotent_replay_rejects_different_quote() -> None:
    row = _message_row()
    request = ChatMessageCreate(content="回复内容", quote_message_id=51)

    with pytest.raises(HTTPException) as exc_info:
        social_service._idempotent_message_response(row, request, viewer=7, quotes={})

    assert exc_info.value.status_code == 409

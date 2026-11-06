"""Q3: one approved photo per Q&A answer, card/detail media projection."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services import discovery as discovery_service
from app.services import profile as profile_service


class _Result:
    def __init__(self, rows=None):
        self._rows = rows or []

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _MediaDb:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        return _Result(rows=self.rows)


def test_qa_answers_keep_at_most_one_media_id() -> None:
    parsed = profile_service._qa_answers_from_value([
        {"question_id": 1, "question": "周末", "answer": "徒步", "media_ids": [10, 11]},
    ])
    assert parsed[0]["media_ids"] == [10]
    card = discovery_service._card_qa_answers(parsed)
    assert card[0] == {"question": "周末", "answer": "徒步", "media_ids": [10]}


@pytest.mark.asyncio
async def test_qa_media_must_be_owned_approved_photo() -> None:
    db = _MediaDb([{"id": 10}])
    assert await profile_service._validate_qa_media_ids(db, 7, [10]) == [10]

    with pytest.raises(HTTPException) as exc_info:
        await profile_service._validate_qa_media_ids(_MediaDb([]), 7, [10])
    assert exc_info.value.detail["code"] == "QA_MEDIA_INVALID"

    with pytest.raises(HTTPException):
        await profile_service._validate_qa_media_ids(_MediaDb([]), 7, [10, 11])


@pytest.mark.asyncio
async def test_profile_qa_media_is_attached_only_when_approved() -> None:
    answers = [{"question": "周末", "answer": "徒步", "media_ids": [10]}, {"question": "旅行", "answer": "每年两次", "media_ids": [99]}]
    db = _MediaDb([{"id": 10, "file_url": "/10.webp", "thumbnail_url": "/10-t.webp"}])
    await profile_service.attach_qa_media(db, 7, answers)
    assert answers[0]["media"] == [{"id": 10, "file_url": "/10.webp", "thumbnail_url": "/10-t.webp"}]
    assert answers[1]["media"] == []


@pytest.mark.asyncio
async def test_card_media_attachment_is_batched_and_owner_scoped() -> None:
    cards = [
        SimpleNamespace(user_id=7, qa_answers=[{"question": "Q", "answer": "A", "media_ids": [10]}]),
        SimpleNamespace(user_id=8, qa_answers=[{"question": "Q2", "answer": "A2", "media_ids": [11]}]),
    ]
    db = _MediaDb([
        {"id": 10, "user_id": 7, "file_url": "/10.webp", "thumbnail_url": "/10-t.webp"},
        {"id": 11, "user_id": 99, "file_url": "/11.webp", "thumbnail_url": "/11-t.webp"},
    ])
    await discovery_service._attach_card_qa_media(db, cards)
    assert cards[0].qa_answers[0]["media"] == [{"id": 10, "file_url": "/10.webp", "thumbnail_url": "/10-t.webp"}]
    assert cards[1].qa_answers[0]["media"] == []
    assert db.calls[0][1] == {"m0": 10, "m1": 11}

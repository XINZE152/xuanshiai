"""Q74: community post reminder list and notification policy."""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.community import CommunityPostCreate
from app.services import community as community_service
from app.services.notifications import _PREFERENCE_BY_EVENT


class _Result:
    def __init__(self, *, rows=None, row=None, lastrowid=None, rowcount=1):
        self._rows = rows or ([] if row is None else [row])
        self._row = row
        self.lastrowid = lastrowid
        self.rowcount = rowcount

    def mappings(self):
        return self

    def first(self):
        return self._row

    def all(self):
        return self._rows


class _TargetDb:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        return _Result(rows=self.rows)


def test_remind_ids_are_capped_at_20() -> None:
    assert CommunityPostCreate(content="动态", remind_user_ids=list(range(1, 21))).remind_user_ids
    with pytest.raises(ValidationError):
        CommunityPostCreate(content="动态", remind_user_ids=list(range(1, 22)))


@pytest.mark.asyncio
async def test_remind_targets_must_be_visible_and_not_blocked() -> None:
    db = _TargetDb([
        {"id": 8, "status": 1, "show_profile": 1, "blocked": 0},
        {"id": 9, "status": 1, "show_profile": 0, "blocked": 0},
    ])
    with pytest.raises(HTTPException) as exc_info:
        await community_service._resolve_remind_targets(db, 7, [8, 9])
    assert exc_info.value.detail["code"] == "REMIND_TARGET_INVALID"

    blocked_db = _TargetDb([{"id": 8, "status": 1, "show_profile": 1, "blocked": 1}])
    with pytest.raises(HTTPException):
        await community_service._resolve_remind_targets(blocked_db, 7, [8])

    valid_db = _TargetDb([{"id": 8, "status": 1, "show_profile": 1, "blocked": 0}])
    assert await community_service._resolve_remind_targets(valid_db, 7, [8, 8]) == [8]


@pytest.mark.asyncio
async def test_author_sees_remind_list_and_non_author_does_not() -> None:
    row = {
        "id": 20,
        "user_id": 7,
        "nickname": "作者",
        "avatar": None,
        "gender": 1,
        "birthday": None,
        "content": "动态",
        "images": "[]",
        "video": None,
        "location": None,
        "visibility": 0,
        "declaration": "",
        "topic_id": None,
        "topic_name": None,
        "like_count": 0,
        "comment_count": 0,
        "collect_count": 0,
        "is_liked": 0,
        "is_collected": 0,
        "is_followed": 0,
        "mbti": None,
        "school": None,
        "hometown": None,
        "residence": None,
        "realname_status": 0,
        "moderation_status": "approved",
        "created_at": datetime(2026, 11, 6, 12, 0, 0),
    }

    class _Db:
        def __init__(self, viewer_id):
            self.viewer_id = viewer_id
            self.calls = 0

        async def execute(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return _Result(row=row)
            return _Result(rows=[{"user_id": 8}, {"user_id": 9}])

    author = await community_service.get_post(_Db(7), 7, 20)
    assert author.remind_user_ids == [8, 9]
    other = await community_service.get_post(_Db(9), 9, 20)
    assert other.remind_user_ids is None


def test_remind_notification_uses_system_preference() -> None:
    assert _PREFERENCE_BY_EVENT["community_post_remind"] == "notify_system"


@pytest.mark.asyncio
async def test_create_post_writes_reminders_and_notifies(monkeypatch) -> None:
    from app.services import community as service

    notifications = []

    async def fake_notify(*_args, **kwargs):
        notifications.append(kwargs)
        return True

    async def fake_targets(_db, _author_id, _ids):
        return [8, 9]

    async def fake_moderate(_db, _text, **_kwargs):
        class Decision:
            action = "allow"
            display_content = "动态"
        return Decision()

    monkeypatch.setattr(service, "ensure_user_allowed", lambda *_a, **_k: _async_none())
    monkeypatch.setattr(service, "_resolve_remind_targets", fake_targets)
    monkeypatch.setattr(service, "emit_notification", fake_notify)
    monkeypatch.setattr("app.services.content_filter.moderate_text", fake_moderate)

    class _CreateDb:
        def __init__(self):
            self.calls = 0
            self.reminders = []

        async def execute(self, statement, params=None):
            sql = " ".join(str(statement).split())
            self.calls += 1
            if "INSERT INTO community_post" in sql:
                return _Result(lastrowid=20)
            if "INSERT IGNORE INTO community_post_remind" in sql:
                self.reminders.append(params["user_id"])
                return _Result()
            if "SELECT user_id FROM community_post_remind" in sql:
                return _Result(rows=[{"user_id": 8}, {"user_id": 9}])
            if "FROM community_post p" in sql:
                return _Result(row=_post_row())
            return _Result()

        async def commit(self):
            return None

    db = _CreateDb()
    response = await service.create_post(
        db, 7, CommunityPostCreate(content="动态", remind_user_ids=[8, 9])
    )
    assert db.reminders == [8, 9]
    assert [item["recipient_user_id"] for item in notifications] == [8, 9]
    assert response.remind_user_ids == [8, 9]


async def _async_none():
    return None


@pytest.mark.asyncio
async def test_pending_post_approval_notifies_reminders_once(monkeypatch) -> None:
    from app.services import social as social_service

    notifications = []

    async def fake_notify(*_args, **kwargs):
        notifications.append(kwargs)
        return True

    monkeypatch.setattr(social_service, "emit_notification", fake_notify)

    class _ApproveDb:
        def __init__(self, previous_status: int):
            self.previous_status = previous_status
            self.calls = []

        async def execute(self, statement, params=None):
            sql = " ".join(str(statement).split())
            self.calls.append(sql)
            if sql.startswith("SELECT user_id, moderation_status FROM community_post"):
                return _Result(row={"user_id": 7, "moderation_status": self.previous_status})
            if sql.startswith("UPDATE community_post"):
                return _Result(rowcount=1)
            if "SELECT user_id FROM community_post_remind" in sql:
                return _Result(rows=[{"user_id": 8}, {"user_id": 9}])
            return _Result()

    db = _ApproveDb(previous_status=0)
    await social_service.moderate_content(
        db, target_type="post", target_id=20, hide=False, actor_id=99
    )
    assert [item["recipient_user_id"] for item in notifications] == [8, 9]
    assert all(item["event_type"] == "community_post_remind" for item in notifications)
    assert all(item["target_id"] == 20 for item in notifications)

    notifications.clear()
    already_approved = _ApproveDb(previous_status=1)
    await social_service.moderate_content(
        already_approved, target_type="post", target_id=20, hide=False, actor_id=99
    )
    assert notifications == []
    assert not any(
        "SELECT user_id FROM community_post_remind" in sql
        for sql in already_approved.calls
    )


def _post_row():
    return {
        "id": 20, "user_id": 7, "nickname": "作者", "avatar": None, "gender": 1,
        "birthday": None, "content": "动态", "images": "[]", "video": None,
        "location": None, "visibility": 0, "declaration": "", "topic_id": None,
        "topic_name": None, "like_count": 0, "comment_count": 0, "collect_count": 0,
        "is_liked": 0, "is_collected": 0, "is_followed": 0, "mbti": None,
        "school": None, "hometown": None, "residence": None, "realname_status": 0,
        "moderation_status": "approved", "created_at": datetime(2026, 11, 6, 12, 0, 0),
    }

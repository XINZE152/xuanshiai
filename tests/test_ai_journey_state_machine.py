"""Task 8 journey stage/interactions contract tests."""

from __future__ import annotations

import pytest


class _LockedSessionResult:
    def __init__(self, row: dict[str, object] | None) -> None:
        self._row = row

    def mappings(self) -> _LockedSessionResult:
        return self

    def first(self) -> dict[str, object] | None:
        return self._row


class _SessionLockingDb:
    def __init__(self, row: dict[str, object] | None) -> None:
        self.row = row
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def execute(self, statement: object, params: dict[str, object]) -> _LockedSessionResult:
        self.calls.append((str(statement), params))
        return _LockedSessionResult(self.row)


class _QueuedDb:
    def __init__(self, *rows: dict[str, object] | None) -> None:
        self._rows = list(rows)
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def execute(self, statement: object, params: dict[str, object]) -> _LockedSessionResult:
        self.calls.append((str(statement), params))
        return _LockedSessionResult(self._rows.pop(0) if self._rows else None)


def test_publish_stage_advances_monotonically_and_allows_idempotent_replay() -> None:
    from app.services.ai.moxiang_state import advance_journey_stage

    assert advance_journey_stage("chatting", "building") == "building"
    assert advance_journey_stage("building", "ready") == "ready"
    assert advance_journey_stage("ready", "published") == "published"
    assert advance_journey_stage("building", "published") == "published"
    assert advance_journey_stage("published", "published") == "published"


@pytest.mark.parametrize(
    ("current", "target"),
    [("building", "chatting"), ("ready", "building"), ("published", "ready")],
)
def test_publish_advance_rejects_regression(current: str, target: str) -> None:
    from app.services.ai.moxiang_state import advance_journey_stage

    with pytest.raises(ValueError) as exc_info:
        advance_journey_stage(current, target)
    assert getattr(exc_info.value, "code", None) == "JOURNEY_STAGE_TRANSITION_INVALID"


def test_snooze_is_explicit_interaction_transition_building_to_chatting() -> None:
    from app.services.ai.moxiang_state import transition_journey_stage

    assert (
        transition_journey_stage("building", "chatting", event="snooze")
        == "chatting"
    )


@pytest.mark.parametrize(
    ("current", "target"),
    [("chatting", "chatting"), ("ready", "chatting"), ("published", "chatting")],
)
def test_snooze_rejects_non_building_stage(current: str, target: str) -> None:
    from app.services.ai.moxiang_state import transition_journey_stage

    with pytest.raises(ValueError):
        transition_journey_stage(current, target, event="snooze")


def test_pause_and_wake_are_session_interactions_and_do_not_rewrite_journey_stage() -> None:
    from app.services.ai.moxiang_state import transition_journey_stage

    assert transition_journey_stage("building", "building", event="pause") == "building"
    assert transition_journey_stage("building", "building", event="wake") == "building"


def test_unknown_event_and_stage_are_rejected() -> None:
    from app.services.ai.moxiang_state import transition_journey_stage

    with pytest.raises(ValueError):
        transition_journey_stage("chatting", "building", event="made_up")
    with pytest.raises(ValueError):
        transition_journey_stage("chatting", "unknown", event="advance")


@pytest.mark.asyncio
async def test_invite_flow_locks_session_and_uses_persisted_stage() -> None:
    """邀请流的状态依据必须来自同一事务内锁定的 session 行。"""
    from app.services.ai.journey import _lock_active_journey_session

    db = _SessionLockingDb(
        {
            "session_id": "session-1",
            "user_id": 7,
            "subject": "personal",
            "status": "awaiting_confirmation",
            "active_status": 1,
            "journey_stage": "ready",
        }
    )

    locked = await _lock_active_journey_session(
        db, session_id="session-1", user_id=7, subject="personal"
    )

    assert locked is not None
    assert locked.journey_stage == "ready"
    assert "FOR UPDATE" in db.calls[0][0]


@pytest.mark.asyncio
async def test_invite_flow_refuses_published_or_inactive_session() -> None:
    from app.services.ai.journey import _lock_active_journey_session

    db = _SessionLockingDb(
        {
            "session_id": "session-published",
            "user_id": 7,
            "subject": "personal",
            "status": "published",
            "active_status": 0,
            "journey_stage": "published",
        }
    )

    assert await _lock_active_journey_session(
        db, session_id="session-published", user_id=7, subject="personal"
    ) is None


@pytest.mark.asyncio
async def test_resolve_does_not_snooze_a_session_published_concurrently() -> None:
    """迟到的 snooze 只能拒绝，不能把持久化 published 回写成 chatting。"""
    from app.services.ai.journey import resolve_journey_invite
    from app.services.ai.profile import AIInputError

    db = _QueuedDb(
        {"session_id": "session-published", "user_id": 7, "subject": "personal"},
        {
            "session_id": "session-published",
            "user_id": 7,
            "subject": "personal",
            "status": "published",
            "active_status": 0,
            "journey_stage": "published",
        },
    )

    with pytest.raises(AIInputError, match="会话已结束"):
        await resolve_journey_invite(
            db, invite_id="invite-1", user_id=7, resolution="snoozed"
        )

    assert len(db.calls) == 2
    assert "FOR UPDATE" in db.calls[1][0]
    assert all("UPDATE ai_profile_session" not in sql for sql, _ in db.calls)


@pytest.mark.asyncio
async def test_accept_locks_draft_before_session_and_invite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """accept 与发布统一为 draft → session → invite，消除反向锁环。"""
    from types import SimpleNamespace

    import app.services.ai.journey as journey

    async def _active_session(*_: object, **__: object) -> SimpleNamespace:
        return SimpleNamespace(
            subject=SimpleNamespace(value="personal"),
            consent_snapshot={},
            policy_revision="policy-v1",
        )

    async def _no_candidates(*_: object, **__: object) -> list[object]:
        return []

    monkeypatch.setattr(journey, "load_owned_active_session", _active_session)
    monkeypatch.setattr(journey, "list_session_candidates", _no_candidates)
    db = _QueuedDb(
        {"session_id": "session-1", "user_id": 7, "subject": "personal"},
        {"draft_id": "draft-1"},
        {
            "session_id": "session-1",
            "user_id": 7,
            "subject": "personal",
            "status": "awaiting_confirmation",
            "active_status": 1,
            "journey_stage": "building",
        },
        {
            "invite_id": "invite-1",
            "session_id": "session-1",
            "user_id": 7,
            "subject": "personal",
            "status": "pending",
            "summary_json": "[]",
            "effective_turn_count_at_create": 4,
            "dimension_count": 3,
            "candidate_count": 3,
        },
    )

    accepted, draft_id = await journey.resolve_journey_invite(
        db, invite_id="invite-1", user_id=7, resolution="accepted"
    )

    assert accepted.status == "accepted"
    assert draft_id == "draft-1"
    locks = [sql for sql, _ in db.calls if "FOR UPDATE" in sql]
    assert "ai_profile_draft" in locks[0]
    assert "ai_profile_session" in locks[1]
    assert "ai_profile_build_invite" in locks[2]

"""Task 11：任务终态「commit 后通知」契约测试。

计划要求（docs/superpowers/plans/2026-09-09-ai-backend-four-batches.md Task 11）：
- 数据库事务成功 commit 后再 publish（不在 complete_task() 未提交时发布）。
- 覆盖 succeeded、failed、cancelled、superseded；非终态（not_applied /
  retry_wait）不发布。
- WebSocket 等待方建立时先读一次终态，解决订阅竞态。
- Redis 故障自动退回轮询。
- disconnect 时 unsubscribe 并关闭资源。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.schemas.ai_common import AiTaskStatus
from app.services.revisions import RevisionVector
from tests.test_worker_finalize_gate import (
    _build_process_patches,
    _make_task,
    _TrackedSessionProvider,
    _TrackedSession,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# 发布侧：worker 终态 commit 后 publish
# ---------------------------------------------------------------------------


class _OrderedProvider(_TrackedSessionProvider):
    """在每个会话 commit 后追加事件，用于校验 commit→publish 顺序。"""

    def __init__(self, timeline: list[str]) -> None:
        super().__init__()
        self._timeline = timeline

    def __call__(self) -> _TrackedSession:
        session = super().__call__()
        inner_commit = session.commit

        async def commit() -> None:
            await inner_commit()
            self._timeline.append(f"commit:{session.label}")

        session.commit = commit  # type: ignore[method-assign]
        return session


def _patch_notify(monkeypatch: pytest.MonkeyPatch, timeline: list[str]) -> list[tuple[str, str | None]]:
    publishes: list[tuple[str, str | None]] = []

    async def fake_notify(task_id: str, status: str | None = None) -> bool:
        publishes.append((task_id, status))
        timeline.append(f"publish:{status or 'wake'}")
        return True

    monkeypatch.setattr("app.workers.ai_worker.notify_task_event", fake_notify)
    return publishes


def _succeeded_handler(outcome: tuple[str, RevisionVector] | None):
    async def handler(db: Any, task: Any, worker_id: str) -> Any:
        return outcome

    return handler


def _fake_complete(status: AiTaskStatus):
    async def fake_complete_task(
        db: Any,
        task_id: str,
        worker_id: str,
        result_ref: str,
        revisions: Any,
        *,
        before_supersede: Any = None,
        before_not_applied: Any = None,
    ) -> Any:
        return _make_task(task_id=task_id, status=status)

    return fake_complete_task


async def _run_process(
    provider: _OrderedProvider,
    task: Any,
    fake_handler: Any,
    fake_complete: Any,
) -> str:
    from app.workers.ai_worker import _process

    with __import__("contextlib").ExitStack() as stack:
        for patcher in _build_process_patches(
            fake_handler=fake_handler,
            fake_complete_task=fake_complete,
            task=task,
        ):
            stack.enter_context(patcher)
        return await _process(
            __import__("unittest").mock.MagicMock(),
            task,
            worker_id="worker-a",
            session_provider=provider,
        )


def _assert_publish_after_last_commit(timeline: list[str], event: str) -> None:
    """publish 必须晚于最后一个 commit 事件（完成复用 handler 会话提交）。"""
    commit_indexes = [
        i for i, entry in enumerate(timeline) if entry.startswith("commit:")
    ]
    assert commit_indexes, "没有任何 commit 事件"
    assert timeline.index(f"publish:{event}") > max(commit_indexes)


async def test_complete_publishes_succeeded_only_after_commit(monkeypatch) -> None:
    """succeeded 终态：publish 必须发生在最后一个 finalize commit 之后。"""

    timeline: list[str] = []
    publishes = _patch_notify(monkeypatch, timeline)
    provider = _OrderedProvider(timeline)
    task = _make_task()

    result = await _run_process(
        provider,
        task,
        _succeeded_handler(("profile-draft:draft-1", RevisionVector(profile=1))),
        _fake_complete(AiTaskStatus.SUCCEEDED),
    )

    assert result == "completed"
    assert publishes == [("task-1", "succeeded")]
    _assert_publish_after_last_commit(timeline, "succeeded")


async def test_superseded_publishes_superseded_after_commit(monkeypatch) -> None:
    """superseded 终态同样在 commit 后发布。"""

    async def fake_complete_supersede(
        db: Any,
        task_id: str,
        worker_id: str,
        result_ref: str,
        revisions: Any,
        *,
        before_supersede: Any = None,
        before_not_applied: Any = None,
    ) -> Any:
        if before_supersede is not None:
            await before_supersede()
        return _make_task(task_id=task_id, status=AiTaskStatus.SUPERSEDED)

    timeline: list[str] = []
    publishes = _patch_notify(monkeypatch, timeline)
    provider = _OrderedProvider(timeline)
    task = _make_task()

    result = await _run_process(
        provider,
        task,
        _succeeded_handler(("profile-draft:draft-1", RevisionVector(profile=1))),
        fake_complete_supersede,
    )

    assert result == "completed"
    assert publishes == [("task-1", "superseded")]
    _assert_publish_after_last_commit(timeline, "superseded")


async def test_not_applied_does_not_publish(monkeypatch) -> None:
    """not_applied（终态门未接管，任务仍非终态）不得发布。"""

    timeline: list[str] = []
    publishes = _patch_notify(monkeypatch, timeline)
    provider = _OrderedProvider(timeline)
    task = _make_task()

    result = await _run_process(
        provider,
        task,
        _succeeded_handler(("profile-draft:draft-1", RevisionVector(profile=1))),
        _fake_complete(AiTaskStatus.RUNNING),
    )

    assert result == "completed"
    assert publishes == []


async def test_failed_terminal_publishes_but_retry_wait_does_not(monkeypatch) -> None:
    """终态 failed 发布；retry_wait/queued 等非终态不发布（过滤在
    _notify_task_terminal 内，与 error_code 无关）。"""

    from app.workers.ai_worker import _notify_task_terminal

    publishes: list[tuple[str, str | None]] = []

    async def fake_notify(task_id: str, status: str | None = None) -> bool:
        publishes.append((task_id, status))
        return True

    monkeypatch.setattr("app.workers.ai_worker.notify_task_event", fake_notify)

    await _notify_task_terminal(
        _make_task(task_id="task-f", status=AiTaskStatus.FAILED)
    )
    await _notify_task_terminal(
        _make_task(task_id="task-r", status=AiTaskStatus.RETRY_WAIT)
    )
    await _notify_task_terminal(
        _make_task(task_id="task-q", status=AiTaskStatus.QUEUED)
    )
    await _notify_task_terminal(
        _make_task(task_id="task-c", status=AiTaskStatus.CANCELLED)
    )

    assert publishes == [("task-f", "failed"), ("task-c", "cancelled")]


async def test_exec_failed_retry_wait_does_not_publish(monkeypatch) -> None:
    """handler 异常 → fail_task(retryable=True) → retry_wait 非终态 → 不发布。"""

    async def failing_handler(db: Any, task: Any, worker_id: str) -> Any:
        raise RuntimeError("provider exploded")

    async def fake_fail_task(
        db: Any, task_id: str, worker_id: str, error_code: str, retryable: bool
    ) -> Any:
        status = AiTaskStatus.FAILED if not retryable else AiTaskStatus.RETRY_WAIT
        return _make_task(task_id=task_id, status=status)

    from unittest.mock import MagicMock, patch

    timeline: list[str] = []
    _patch_notify(monkeypatch, timeline)
    provider = _OrderedProvider(timeline)
    task = _make_task(status=AiTaskStatus.RUNNING)
    monkeypatch.setattr("app.workers.ai_worker.fail_task", fake_fail_task)
    with patch(
        "app.workers.ai_worker.TASK_HANDLERS",
        {"profile_extract": failing_handler},
    ), patch("app.workers.ai_worker.start_task", return_value=task):
        from app.workers.ai_worker import _process

        result = await _process(
            MagicMock(), task, worker_id="worker-a", session_provider=provider
        )

    assert result == "failed"
    assert [entry for entry in timeline if entry.startswith("publish:")] == []


async def test_reaper_recovery_publishes_wake_signals(monkeypatch) -> None:
    """reaper 恢复（retry_wait 或终态 failed）发布无状态唤醒信号。"""

    publishes: list[tuple[str, str | None]] = []

    async def fake_notify(task_id: str, status: str | None = None) -> bool:
        publishes.append((task_id, status))
        return True

    monkeypatch.setattr("app.workers.ai_worker.notify_task_event", fake_notify)
    from app.workers.ai_worker import _notify_reaped_tasks

    await _notify_reaped_tasks(["task-a", "task-b"])

    assert [p[0] for p in publishes] == ["task-a", "task-b"]
    assert all(p[1] is None for p in publishes)


async def test_cancel_route_publishes_after_commit(monkeypatch) -> None:
    """取消路由：cancelled 终态在 db.commit() 之后发布。"""

    from tests.test_ai_tasks import TaskStore
    from app.api.routes import ai_tasks as ai_tasks_mod

    timeline: list[str] = []
    publishes: list[tuple[str, str | None]] = []

    async def fake_notify(task_id: str, status: str | None = None) -> bool:
        publishes.append((task_id, status))
        timeline.append("publish")
        return True

    monkeypatch.setattr(ai_tasks_mod, "notify_task_event", fake_notify)

    store = TaskStore()
    db = store.session
    inner_commit = db.commit

    async def commit() -> None:
        await inner_commit()
        timeline.append("commit")

    db.commit = commit  # type: ignore[method-assign]
    task = await store.seed(status="running", owner_user_id=10, lease_owner="w")

    class _FakeUser:
        id = 10

    await ai_tasks_mod.cancel_ai_task(task.task_id, current=_FakeUser(), db=db)

    assert publishes == [(task.task_id, "cancelled")]
    assert timeline == ["commit", "publish"]


# ---------------------------------------------------------------------------
# 发布原语：task_events
# ---------------------------------------------------------------------------


async def test_notify_task_event_swallows_redis_failure(monkeypatch) -> None:
    """Redis 不可用时发布静默失败（返回 False、不抛出）。"""

    from app.services.ai import task_events

    class _BrokenRedis:
        def publish(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("redis down")

    monkeypatch.setattr(
        "app.core.redis.redis_client", _BrokenRedis(), raising=False
    )
    assert await task_events.notify_task_event("task-1", "failed") is False


async def test_task_event_channel_format() -> None:
    from app.services.ai.task_events import task_event_channel

    assert task_event_channel("abc").startswith("ai:task-events:abc")


# ---------------------------------------------------------------------------
# 订阅侧：voice_moxiang 等待方
# ---------------------------------------------------------------------------


class _FakeWs:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))


class _FakeResult:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def mappings(self) -> "_FakeResult":
        return self

    def first(self) -> dict[str, Any] | None:
        return self._row


class _FakeDb:
    """按顺序吐出 ai_task.status；最后一个状态复用到窗口结束。"""

    def __init__(self, statuses: list[str]) -> None:
        self._statuses = list(statuses)
        self.reads = 0

    async def execute(self, statement: Any, params: Any = None) -> _FakeResult:
        assert "FROM ai_task" in str(statement)
        self.reads += 1
        if len(self._statuses) > 1:
            return _FakeResult({"status": self._statuses.pop(0)})
        return _FakeResult({"status": self._statuses[0]})

    async def __aenter__(self) -> "_FakeDb":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


class _FakePubsub:
    def __init__(
        self,
        messages: list[dict[str, Any]] | None = None,
        fail: bool = False,
    ) -> None:
        self.subscribed: list[str] = []
        self.unsubscribed = 0
        self.closed = False
        self._messages = list(messages or [])
        self._fail = fail

    async def subscribe(self, channel: str) -> None:
        self.subscribed.append(channel)

    async def unsubscribe(self, *channels: str) -> None:
        self.unsubscribed += 1

    async def get_message(
        self, ignore_subscribe_messages: bool = False, timeout: float | None = None
    ) -> dict[str, Any] | None:
        if self._fail:
            raise RuntimeError("pubsub connection dropped")
        await asyncio.sleep(0)
        if self._messages:
            return self._messages.pop(0)
        return None

    async def aclose(self) -> None:
        self.closed = True


def _watch_mocks(monkeypatch: pytest.MonkeyPatch, statuses: list[str]) -> _FakeDb:
    from app.api.routes import voice_moxiang

    db = _FakeDb(statuses)
    monkeypatch.setattr(voice_moxiang, "_db_session_factory", lambda: db)
    monkeypatch.setattr(
        voice_moxiang, "_push_journey_progress", _async_noop
    )
    monkeypatch.setattr(
        voice_moxiang, "_maybe_push_build_invite", _async_noop
    )
    return db


async def _async_noop(*args: Any, **kwargs: Any) -> None:
    return None


async def test_already_terminal_task_pushes_without_waiting(monkeypatch) -> None:
    """订阅竞态：等待建立时先读一次终态，已终态立即收尾（不等待）。"""

    from app.api.routes import voice_moxiang

    opened: list[_FakePubsub] = []

    async def fake_open(task_id: str) -> _FakePubsub:
        pubsub = _FakePubsub()
        opened.append(pubsub)
        return pubsub

    monkeypatch.setattr(voice_moxiang, "_open_task_pubsub", fake_open)
    db = _watch_mocks(monkeypatch, ["succeeded"])
    ws = _FakeWs()

    await voice_moxiang._wait_candidate_and_push(
        ws, user_id=1, session_id="s", subject="personal", task_id="t-1"
    )

    assert [m["status"] for m in ws.sent] == ["processing", "completed"]
    assert db.reads == 1  # 只读了初始状态，未进入轮询
    # 已终态早退：无需订阅，也不应打开 pub/sub。
    assert opened == []


async def test_pubsub_wake_triggers_immediate_terminal_read(monkeypatch) -> None:
    """唤醒事件把下一次数据库读取提前；读取以数据库为准。"""

    from app.api.routes import voice_moxiang

    pubsub = _FakePubsub(messages=[{"type": "message", "channel": "c", "data": "x"}])

    async def fake_open(task_id: str) -> _FakePubsub:
        return pubsub

    monkeypatch.setattr(voice_moxiang, "_open_task_pubsub", fake_open)
    db = _watch_mocks(monkeypatch, ["running", "succeeded"])
    monkeypatch.setattr(
        voice_moxiang, "_TASK_EVENT_POLL_SCHEDULE", (0.05,) * 4, raising=False
    )
    ws = _FakeWs()

    await voice_moxiang._wait_candidate_and_push(
        ws, user_id=1, session_id="s", subject="personal", task_id="t-1"
    )

    assert [m["status"] for m in ws.sent] == ["processing", "completed"]
    assert db.reads == 2  # 初始一次 + 唤醒一次
    assert pubsub.closed and pubsub.unsubscribed >= 1


async def test_redis_broken_pubsub_falls_back_to_polling(monkeypatch) -> None:
    """订阅成功但连接中断：自动退回轮询，最终仍能感知终态。"""

    from app.api.routes import voice_moxiang

    pubsub = _FakePubsub(fail=True)

    async def fake_open(task_id: str) -> _FakePubsub:
        return pubsub

    monkeypatch.setattr(voice_moxiang, "_open_task_pubsub", fake_open)
    db = _watch_mocks(monkeypatch, ["running", "running", "succeeded"])
    monkeypatch.setattr(
        voice_moxiang, "_TASK_EVENT_POLL_SCHEDULE", (0.01,) * 6, raising=False
    )
    ws = _FakeWs()

    await voice_moxiang._wait_candidate_and_push(
        ws, user_id=1, session_id="s", subject="personal", task_id="t-1"
    )

    assert [m["status"] for m in ws.sent] == ["processing", "completed"]


async def test_redis_unavailable_uses_pure_polling(monkeypatch) -> None:
    """Redis 不可用（订阅失败）：退回纯轮询并记录 fallback 指标。"""

    from app.api.routes import voice_moxiang

    async def fake_open(task_id: str) -> None:
        return None

    monkeypatch.setattr(voice_moxiang, "_open_task_pubsub", fake_open)
    db = _watch_mocks(monkeypatch, ["running", "succeeded"])
    monkeypatch.setattr(
        voice_moxiang, "_TASK_EVENT_POLL_SCHEDULE", (0.01,) * 4, raising=False
    )
    metrics: list[tuple[str, float]] = []
    monkeypatch.setattr(
        voice_moxiang,
        "emit_ai_metric",
        lambda name, value, tags=None: metrics.append((name, value)),
    )
    ws = _FakeWs()

    await voice_moxiang._wait_candidate_and_push(
        ws, user_id=1, session_id="s", subject="personal", task_id="t-1"
    )

    assert [m["status"] for m in ws.sent] == ["processing", "completed"]
    assert ("websocket_fallback", 1) in metrics


async def test_failed_terminal_pushes_failed_event(monkeypatch) -> None:
    from app.api.routes import voice_moxiang

    async def fake_open(task_id: str) -> None:
        return None

    monkeypatch.setattr(voice_moxiang, "_open_task_pubsub", fake_open)
    _watch_mocks(monkeypatch, ["superseded"])
    ws = _FakeWs()

    await voice_moxiang._wait_candidate_and_push(
        ws, user_id=1, session_id="s", subject="personal", task_id="t-1"
    )

    assert [m["status"] for m in ws.sent] == ["processing", "failed"]


async def test_window_expires_silently(monkeypatch) -> None:
    """窗口内未到终态：静默放弃，保持占位。"""

    from app.api.routes import voice_moxiang

    async def fake_open(task_id: str) -> None:
        return None

    monkeypatch.setattr(voice_moxiang, "_open_task_pubsub", fake_open)
    _watch_mocks(monkeypatch, ["running"])
    monkeypatch.setattr(
        voice_moxiang, "_TASK_EVENT_POLL_SCHEDULE", (0.01, 0.01), raising=False
    )
    ws = _FakeWs()

    await voice_moxiang._wait_candidate_and_push(
        ws, user_id=1, session_id="s", subject="personal", task_id="t-1"
    )

    assert [m["status"] for m in ws.sent] == ["processing"]


async def test_disconnect_closes_pubsub_resources(monkeypatch) -> None:
    """等待方被取消（断开）：订阅退订并关闭连接。"""

    from app.api.routes import voice_moxiang

    pubsub = _FakePubsub()

    async def fake_open(task_id: str) -> _FakePubsub:
        return pubsub

    monkeypatch.setattr(voice_moxiang, "_open_task_pubsub", fake_open)
    _watch_mocks(monkeypatch, ["running"])
    monkeypatch.setattr(
        voice_moxiang, "_TASK_EVENT_POLL_SCHEDULE", (0.05,) * 200, raising=False
    )
    ws = _FakeWs()

    watch_task = asyncio.create_task(
        voice_moxiang._wait_candidate_and_push(
            ws, user_id=1, session_id="s", subject="personal", task_id="t-1"
        )
    )
    await asyncio.sleep(0.05)
    watch_task.cancel()
    with __import__("contextlib").suppress(asyncio.CancelledError):
        await watch_task

    assert pubsub.unsubscribed >= 1
    assert pubsub.closed


async def test_missing_row_stops_quietly(monkeypatch) -> None:
    """任务行消失（如被 tombstone）：静默返回，不推送终态。"""

    from app.api.routes import voice_moxiang

    async def fake_open(task_id: str) -> None:
        return None

    monkeypatch.setattr(voice_moxiang, "_open_task_pubsub", fake_open)

    class _NoRowDb:
        async def execute(self, statement: Any, params: Any = None) -> _FakeResult:
            return _FakeResult(None)

        async def __aenter__(self) -> "_NoRowDb":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    no_row_db = _NoRowDb()
    monkeypatch.setattr(voice_moxiang, "_db_session_factory", lambda: no_row_db)
    monkeypatch.setattr(voice_moxiang, "_push_journey_progress", _async_noop)
    monkeypatch.setattr(voice_moxiang, "_maybe_push_build_invite", _async_noop)
    ws = _FakeWs()

    await voice_moxiang._wait_candidate_and_push(
        ws, user_id=1, session_id="s", subject="personal", task_id="t-1"
    )

    assert [m["status"] for m in ws.sent] == ["processing"]


async def test_open_task_pubsub_uses_event_channel(monkeypatch) -> None:
    """真实订阅路径：channel 名与 task_events 约定一致；Redis 故障返回 None。"""

    from app.api.routes import voice_moxiang
    from app.services.ai.task_events import task_event_channel

    pubsub = _FakePubsub()

    class _FakeRedis:
        def pubsub(self) -> _FakePubsub:
            return pubsub

    monkeypatch.setattr(
        "app.core.redis.redis_client", _FakeRedis(), raising=False
    )
    opened = await voice_moxiang._open_task_pubsub("task-xyz")
    assert opened is pubsub
    assert pubsub.subscribed == [task_event_channel("task-xyz")]

    class _BrokenRedis:
        def pubsub(self) -> _FakePubsub:
            raise RuntimeError("redis down")

    monkeypatch.setattr(
        "app.core.redis.redis_client", _BrokenRedis(), raising=False
    )
    assert await voice_moxiang._open_task_pubsub("task-xyz") is None

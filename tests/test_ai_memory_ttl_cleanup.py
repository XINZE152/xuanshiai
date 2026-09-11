"""Memory State TTL 清理接入 worker 的单测（Batch-1 Task 2）。

覆盖（对应 task-2-brief 验收项）：

- 清理使用独立 AsyncSession（每批新会话，绝不复用请求/业务/种子会话）；
- 每个批次独立提交；异常时显式 rollback，且失败批次的全部未提交写入
  （事件 / 物化视图 / outbox 行）必须整体丢弃；
- 批量上限（单批行数 / 单轮批次数）与单次执行的墙钟时间上限；
- 清理成功、清理失败、重复执行（节流跳过）分别记录指标；
- 清理失败不会污染后续 outbox 或 worker 事务（同 store 上的 outbox 消费
  与收据幂等在失败后仍然正常）；
- ``_run_forever`` 主循环接入（首轮立即执行、按间隔节流、失败不终止循环）。

所有数据库交互都基于 ``tests.test_ai_memory_ledger`` 的内存 fake store；
为使 rollback 语义对齐真实 AsyncSession，测试会话在事务首条 SQL 前补一张
「事务开始」快照，使 rollback 能撤掉整个未提交批次。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.core.config import settings
from app.services.ai.audit import KNOWN_METRICS, metric_snapshot
from app.services.ai.memory.derivations import (
    MemoryStateTtlCleanupStats,
    run_memory_state_ttl_cleanup,
)
from app.workers import ai_worker

from tests.test_ai_memory_ledger import FakeMemorySession, MemoryStore, make_ledger

pytestmark = pytest.mark.asyncio

_OWNER = 42


def _naive(*, minutes: float = 0) -> datetime:
    """Naive UTC 时间（MySQL DATETIME 约定）；minutes>0 表示过去。"""
    value = datetime.now(UTC) - timedelta(minutes=minutes)
    return value.replace(tzinfo=None)


def _store_snapshot(store: MemoryStore) -> dict[str, Any]:
    """失败批次前后的污染对照快照（事件 / 状态 / outbox / 序列）。"""
    return {
        "owner_sequences": dict(store.owner_sequences),
        "events": [dict(row) for row in store.events],
        "states": {key: dict(row) for key, row in store.states.items()},
        "outbox": {key: dict(row) for key, row in store.outbox.items()},
    }


def _expiry_events(store: MemoryStore) -> list[dict[str, Any]]:
    return [row for row in store.events if row["event_type"] == "state_expired"]


# ----------------------------------------------------------------------
# 测试会话基建：事务开始快照 + 注入点
# ----------------------------------------------------------------------


class _SessionCM:
    """把 FakeMemorySession 包成 async CM（对齐 async_sessionmaker 行为）。"""

    def __init__(self, session: FakeMemorySession) -> None:
        self._session = session

    async def __aenter__(self) -> FakeMemorySession:
        return self._session

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _TtlCleanupSession(FakeMemorySession):
    """rollback 语义对齐真实 AsyncSession 的测试会话。

    FakeMemorySession 只在 commit 时快照，rollback 撤不掉「从未 commit 过」的
    事务内写入；本类在事务首条 SQL 前补一张事务开始快照，使 rollback 能把
    整个未提交批次（事件 / 物化 / outbox 行）全部撤掉——与生产事务语义一致。
    """

    def __init__(self, store: MemoryStore) -> None:
        super().__init__(store)
        self._txn_open = False

    async def execute(self, statement: object, params: dict[str, Any] | None = None) -> Any:
        if not self._txn_open:
            self._txn_open = True
            self._snapshot = self._capture()
        return await super().execute(statement, params)

    async def commit(self) -> None:
        await super().commit()
        self._txn_open = False

    async def rollback(self) -> None:
        await super().rollback()
        self._txn_open = False


class _MidBatchFailureSession(_TtlCleanupSession):
    """第二条 ``ai_memory_event`` INSERT 时抛错：模拟批次中途瞬时 DB 故障。

    第一条事件已完整写入（事件行 + 物化 + outbox 行，均未提交），第二条
    INSERT 失败——rollback 必须把第一批的中间写入全部丢弃。
    """

    def __init__(self, store: MemoryStore) -> None:
        super().__init__(store)
        self._event_inserts = 0

    async def execute(self, statement: object, params: dict[str, Any] | None = None) -> Any:
        sql = str(statement)
        if "INSERT INTO ai_memory_event" in sql:
            self._event_inserts += 1
            if self._event_inserts >= 2:
                raise RuntimeError("模拟批次中途数据库故障")
        return await super().execute(statement, params)


class _Provider:
    """按顺序发放预置会话的 session_factory 替身（多领会话即测试失败）。"""

    def __init__(self, *sessions: FakeMemorySession) -> None:
        self._pending = list(sessions)
        self.created: list[FakeMemorySession] = []

    def __call__(self) -> _SessionCM:
        if not self._pending:
            raise AssertionError("cleanup 请求了超出预期的会话数量")
        session = self._pending.pop(0)
        self.created.append(session)
        return _SessionCM(session)

    @property
    def unused(self) -> int:
        return len(self._pending)


class _InexhaustibleProvider:
    """常驻 session_factory 替身：每次调用发放一个全新的会话。"""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store
        self.created = 0
        self.sessions: list[FakeMemorySession] = []

    def __call__(self) -> _SessionCM:
        self.created += 1
        session = _TtlCleanupSession(self._store)
        self.sessions.append(session)
        return _SessionCM(session)


# ----------------------------------------------------------------------
# 种子数据：走真实 ledger 记账路径（含 outbox 入队副作用）
# ----------------------------------------------------------------------


async def _seed_state(
    ledger: Any,
    *,
    seed: int,
    minutes_until: float,
) -> None:
    from app.schemas.ai_memory import MemoryEventInput

    await ledger.append(
        MemoryEventInput(
            owner_user_id=_OWNER,
            subject="personal",
            node_type="state",
            event_type="state_activated",
            payload={
                "canonical_key": f"session:context:topic-{seed}",
                "value": f"状态值-{seed}",
                "valid_until": _naive(minutes=-minutes_until),
            },
            source_kind="user_explicit",
            idempotency_key=f"ttl-cleanup-seed-{seed}",
        )
    )


async def _seeded_store(*, expired: int = 1, fresh: int = 0) -> tuple[Any, FakeMemorySession, MemoryStore]:
    ledger, seed_session, store, _ = make_ledger()
    for idx in range(expired):
        await _seed_state(ledger, seed=idx, minutes_until=-5.0)
    for idx in range(fresh):
        await _seed_state(ledger, seed=100 + idx, minutes_until=60.0)
    return ledger, seed_session, store


# ======================================================================
# 服务层：独立会话 / 分批提交 / 双重上限
# ======================================================================


async def test_expires_due_states_with_independent_session_per_batch() -> None:
    """每批领取全新独立会话并独立提交；种子会话绝不复用。"""
    _, seed_session, store = await _seeded_store(expired=3, fresh=1)
    before_expiry = len(_expiry_events(store))
    provider = _Provider(_TtlCleanupSession(store), _TtlCleanupSession(store))

    stats = await run_memory_state_ttl_cleanup(
        provider,
        now=_naive(),
        batch_size=2,
        max_batches=10,
        time_budget_seconds=10.0,
    )

    assert (stats.expired, stats.batches, stats.failed_batches) == (3, 2, 0)
    assert stats.truncated is False
    assert len(provider.created) == 2, "每批一个全新独立会话"
    first, second = provider.created
    assert first is not second, "批次之间不得复用会话"
    assert seed_session not in provider.created, "不得复用业务/种子会话"
    assert seed_session.commits == 0, "种子会话未被清理触碰"
    assert (first.commits, first.rollbacks) == (1, 0)
    assert (second.commits, second.rollbacks) == (1, 0)
    expired_states = [row for row in store.states.values() if row["status"] == "expired"]
    fresh_states = [row for row in store.states.values() if row["status"] == "active"]
    assert len(expired_states) == 3
    assert len(fresh_states) == 1
    assert len(_expiry_events(store)) == before_expiry + 3
    # 每条过期事件都进入 outbox（幂等键绑定 state_id，供下游消费者处理）。
    assert len(store.outbox) == len(store.events)


async def test_second_round_is_idempotent() -> None:
    """重复执行：首轮过期后第二轮零扫描、零提交。"""
    _, _, store = await _seeded_store(expired=1)
    first = await run_memory_state_ttl_cleanup(
        _Provider(_TtlCleanupSession(store)),
        now=_naive(),
        batch_size=10,
        max_batches=10,
        time_budget_seconds=10.0,
    )
    assert first.expired == 1
    before = _store_snapshot(store)

    second = await run_memory_state_ttl_cleanup(
        _Provider(_TtlCleanupSession(store)),
        now=_naive(),
        batch_size=10,
        max_batches=10,
        time_budget_seconds=10.0,
    )

    assert (second.expired, second.failed_batches) == (0, 0)
    assert _store_snapshot(store) == before, "第二轮不得产生任何写入"


async def test_batch_size_and_max_batches_caps() -> None:
    """单批行数决定每批规模；单轮批次数上限截断剩余积压。"""
    _, _, store = await _seeded_store(expired=3)
    provider = _Provider(_TtlCleanupSession(store), _TtlCleanupSession(store))

    stats = await run_memory_state_ttl_cleanup(
        provider,
        now=_naive(),
        batch_size=1,
        max_batches=2,
        time_budget_seconds=60.0,
    )

    assert (stats.expired, stats.batches, stats.failed_batches) == (2, 2, 0)
    assert provider.unused == 0
    active = [row for row in store.states.values() if row["status"] == "active"]
    assert len(active) == 1, "批次数上限之外的积压留给下一轮"


async def test_time_budget_stops_run_before_any_batch() -> None:
    """墙钟时间上限在批次开始前生效：不领会话、不写库。"""
    _, _, store = await _seeded_store(expired=2)
    provider = _Provider(_TtlCleanupSession(store), _TtlCleanupSession(store))

    stats = await run_memory_state_ttl_cleanup(
        provider,
        now=_naive(),
        batch_size=10,
        max_batches=10,
        time_budget_seconds=0.0,
    )

    assert stats.batches == 0
    assert stats.expired == 0
    assert stats.truncated is True
    assert provider.created == [], "时间预算耗尽时不得打开任何会话"


# ======================================================================
# 失败隔离：rollback + 不污染后续 outbox / worker 事务
# ======================================================================


async def test_batch_failure_rolls_back_and_pollutes_nothing() -> None:
    """批次中途失败：显式 rollback 丢弃全部未提交写入；下一轮照常工作。"""
    _, _, store = await _seeded_store(expired=2)
    before = _store_snapshot(store)
    failing = _MidBatchFailureSession(store)
    provider = _Provider(failing)

    stats = await run_memory_state_ttl_cleanup(
        provider,
        now=_naive(),
        batch_size=10,
        max_batches=10,
        time_budget_seconds=10.0,
    )

    assert (stats.expired, stats.batches, stats.failed_batches) == (0, 0, 1)
    assert failing.rollbacks == 1, "异常时必须显式 rollback"
    assert failing.commits == 0
    assert _store_snapshot(store) == before, (
        "失败批次的未提交写入（事件/物化/outbox 行）必须全部回滚"
    )

    # 下一轮（新会话）正常完成：失败没有污染后续 worker 事务。
    healthy = _TtlCleanupSession(store)
    retry = await run_memory_state_ttl_cleanup(
        _Provider(healthy),
        now=_naive(),
        batch_size=10,
        max_batches=10,
        time_budget_seconds=10.0,
    )
    assert (retry.expired, retry.failed_batches) == (2, 0)
    assert (healthy.commits, healthy.rollbacks) == (1, 0)
    assert all(row["status"] == "expired" for row in store.states.values())


async def test_failed_batch_keeps_outbox_consumable() -> None:
    """清理失败后，同 store 上的 outbox 消费与收据幂等照常工作。"""
    _, _, store = await _seeded_store(expired=2)
    failing = _MidBatchFailureSession(store)
    failed_stats = await run_memory_state_ttl_cleanup(
        _Provider(failing),
        now=_naive(),
        batch_size=10,
        max_batches=10,
        time_budget_seconds=10.0,
    )
    assert failed_stats.failed_batches == 1

    # 失败后的成功轮次照常入队 outbox；随后用真实消费路径验证完整性。
    await run_memory_state_ttl_cleanup(
        _Provider(_TtlCleanupSession(store)),
        now=_naive(),
        batch_size=10,
        max_batches=10,
        time_budget_seconds=10.0,
    )
    expiry_event_ids = {
        row["event_id"] for row in store.events if row["event_type"] == "state_expired"
    }
    expiry_rows = [
        row
        for row in store.outbox.values()
        if row["event_id"] in expiry_event_ids and row["event_type"] == "memory_state"
    ]
    assert len(expiry_rows) == 2
    expiry_row = expiry_rows[0]
    # fake store 不落 SQL 默认值列：缺 status 等价于 DB 默认 'pending'。
    assert expiry_row.get("status", "pending") == "pending"

    from datetime import UTC as _UTC

    from app.services.ai.memory.derivations import handle_memory_derivation
    from app.services.derivation_outbox import DerivationEvent, consume_outbox_event
    from app.services.revisions import RevisionVector

    event = DerivationEvent(
        event_id=str(expiry_row["event_id"]),
        aggregate_type="ai_memory",
        aggregate_id=_OWNER,
        event_type=str(expiry_row["event_type"]),
        changed_fields=(),
        source_revision=RevisionVector(),
        occurred_at=datetime.now(_UTC),
        priority=50,
    )
    consume_session = _TtlCleanupSession(store)

    async def _handler(evt: DerivationEvent) -> str:
        return await handle_memory_derivation(consume_session, evt)

    result = await consume_outbox_event(
        consume_session, event, "ttl-cleanup-test", _handler
    )
    assert result.status == "succeeded"
    # 收据幂等：重复消费 → duplicate，同一事件不再重复处理。
    result2 = await consume_outbox_event(
        consume_session, event, "ttl-cleanup-test", _handler
    )
    assert result2.status == "duplicate"


# ======================================================================
# 指标：成功 / 失败 / 重复执行（节流跳过）
# ======================================================================


def _last_metric(name: str) -> tuple[float, dict[str, str]] | None:
    samples = metric_snapshot().get(name, [])
    return samples[-1] if samples else None


async def test_worker_round_emits_success_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """worker 清理轮次：过期计数打点 memory_state_ttl_cleanup_success。"""
    _, seed_session, store = await _seeded_store(expired=2)
    provider = _InexhaustibleProvider(store)
    monkeypatch.setattr(ai_worker, "session_factory", provider)

    payload = await ai_worker._run_memory_state_ttl_cleanup_round()

    assert payload["expired"] == 2
    assert payload["failed_batches"] == 0
    assert provider.created >= 1
    assert seed_session not in provider.sessions, "清理不得复用业务/种子会话"
    assert seed_session.commits == 0
    sample = _last_metric("memory_state_ttl_cleanup_success")
    assert sample is not None
    value, tags = sample
    assert value == 2
    assert tags.get("worker_id")
    assert _last_metric("memory_state_ttl_cleanup_failed") is None


async def test_worker_round_emits_failure_metric_and_pollutes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """worker 清理轮次：批次失败打点 memory_state_ttl_cleanup_failed。"""
    _, _, store = await _seeded_store(expired=2)
    before = _store_snapshot(store)
    provider = _Provider(_MidBatchFailureSession(store))
    monkeypatch.setattr(ai_worker, "session_factory", provider)

    payload = await ai_worker._run_memory_state_ttl_cleanup_round()

    assert payload["failed_batches"] == 1
    assert payload["expired"] == 0
    assert provider.created[0].rollbacks == 1
    sample = _last_metric("memory_state_ttl_cleanup_failed")
    assert sample is not None
    value, tags = sample
    assert value == 1
    assert tags.get("worker_id")
    assert _store_snapshot(store) == before


async def test_throttle_skips_within_interval_and_emits_skip_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """间隔未到：节流跳过并单独打点 skipped；间隔到了：立即执行。"""
    calls: list[dict[str, Any]] = []

    async def fake_round() -> dict[str, int]:
        calls.append({})
        return {"expired": 0, "batches": 0, "failed_batches": 0, "truncated": 0}

    monkeypatch.setattr(ai_worker, "_run_memory_state_ttl_cleanup_round", fake_round)

    last = await ai_worker._maybe_run_memory_state_ttl_cleanup(time.monotonic())
    assert calls == [], "间隔内不得重复执行"
    assert last == time.monotonic() or abs(last - time.monotonic()) < 1.0
    sample = _last_metric("memory_state_ttl_cleanup_skipped")
    assert sample is not None
    assert sample[0] == 1

    await ai_worker._maybe_run_memory_state_ttl_cleanup(float("-inf"))
    assert len(calls) == 1, "首轮（从未执行过）应立即执行"


async def test_ttl_cleanup_metrics_and_settings_registered() -> None:
    """三个指标名必须注册进 KNOWN_METRICS；四个配置键存在且默认值合法。"""
    assert {
        "memory_state_ttl_cleanup_success",
        "memory_state_ttl_cleanup_failed",
        "memory_state_ttl_cleanup_skipped",
    } <= KNOWN_METRICS
    assert isinstance(MemoryStateTtlCleanupStats(), MemoryStateTtlCleanupStats)
    assert settings.ai_memory_state_ttl_batch_size >= 1
    assert settings.ai_memory_state_ttl_max_batches >= 1
    assert settings.ai_memory_state_ttl_time_budget_seconds > 0
    assert settings.ai_memory_state_ttl_cleanup_interval_seconds >= 0


# ======================================================================
# worker 主循环接入
# ======================================================================


async def test_worker_forever_loop_schedules_memory_ttl_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_run_forever`` 主循环触发 Memory State TTL 清理（首轮即执行）。"""
    _, _, store = await _seeded_store(expired=1)
    monkeypatch.setattr(ai_worker, "session_factory", _InexhaustibleProvider(store))

    async def fake_round(worker_id: str, batch_size: int):
        return 0, 0, 0

    monkeypatch.setattr(ai_worker, "_run_round", fake_round)
    monkeypatch.setattr(settings, "ai_memory_state_ttl_cleanup_interval_seconds", 3600)

    task = asyncio.create_task(ai_worker._run_forever("worker-ttl", 1, 0.01))
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not _expiry_events(store):
            await asyncio.sleep(0.01)
        # 首轮执行后再跑几个 tick：验证节流路径打点 skipped 指标。
        deadline = time.monotonic() + 0.1
        while time.monotonic() < deadline:
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert _expiry_events(store), "主循环必须触发记忆状态 TTL 清理"
    assert _last_metric("memory_state_ttl_cleanup_success") is not None
    assert _last_metric("memory_state_ttl_cleanup_skipped") is not None


async def test_worker_forever_loop_survives_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """清理失败只记日志与指标，业务任务轮次继续运转。"""
    business_rounds: list[int] = []

    async def fake_round(worker_id: str, batch_size: int):
        business_rounds.append(1)
        return 0, 0, 0

    async def failing_cleanup() -> dict[str, int]:
        raise RuntimeError("模拟记忆状态清理崩溃")

    monkeypatch.setattr(ai_worker, "_run_round", fake_round)
    monkeypatch.setattr(ai_worker, "_run_memory_state_ttl_cleanup_round", failing_cleanup)
    monkeypatch.setattr(settings, "ai_memory_state_ttl_cleanup_interval_seconds", 3600)

    task = asyncio.create_task(ai_worker._run_forever("worker-ttl", 1, 0.01))
    try:
        deadline = time.monotonic() + 0.1
        while time.monotonic() < deadline:
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert len(business_rounds) >= 2, "清理失败后主循环必须继续处理业务任务"
    sample = _last_metric("memory_state_ttl_cleanup_failed")
    assert sample is not None and sample[0] == 1

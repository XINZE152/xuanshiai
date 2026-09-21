"""第四批：记忆生命周期（逐表清理 / 序号水位围栏 / 暂停 vs 删除）。

覆盖修复清单 §3.17 的验收要点，且期望值独立书写——不调用被测实现推导：

1. **逐表行为**（§3.17.1 清单）：`event`/`observation`/`insight`/`state`/
   `projection` 物理删除；`claim` 先「`value_json` 置 NULL + `status='expired'`」
   再删除；`suppression` 只清引用占位；`projection_grant` 只置 revoked；
   **`ai_consent_grant` 绝不出现 DELETE/UPDATE**（撤回记录是合规证据）。
2. **dry-run 与实删同口径**（§3.17.3）：两者必须使用**完全相同的 WHERE 条件**，
   dry-run 只统计不写。
3. **序号水位围栏**（§3.17.2）：清理读取水位、末尾删除 owner_sequence，
   使撤回后新建记忆从 1 重新编号。
4. **清理可观测**（§3.17.3）：逐表计数上报，指标名已登记 `KNOWN_METRICS`。
5. **接入撤回路径**（§3.17.5）：`purge_ai_resources` 的记忆清理随 `memory`
   与既有 `profile`/`consent_profile`/`user` scope 生效；其余 scope 不触发。
6. **清理任务消费**：`cleanup_handler` 的 `memory` 分支校验 resource_id、
   走 `scope="memory"`；非法 payload 不可重试失败。
7. **两个入口、两种生命周期**（决策 2(a)）：`pause` 不删数据、不建任务；
   `forget` 建任务且不可恢复。
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from app.services.ai.audit import KNOWN_METRICS, metric_snapshot
from app.services.ai.memory.purge import (
    METRIC_MEMORY_PURGE_DELETED,
    METRIC_MEMORY_PURGE_DRY_RUN,
    MemoryPurgeStats,
    purge_memory_for_owner,
)

OWNER_ID = 4242

#: 期望被物理删除的记忆表（子表/派生视图 + 账本 + 序列）。
EXPECTED_DELETED_TABLES = frozenset(
    {
        "ai_memory_observation",
        "ai_memory_insight",
        "ai_memory_state",
        "ai_memory_claim",
        "ai_memory_projection",
        "ai_memory_event",
        "ai_memory_owner_sequence",
    }
)

#: 期望只做 UPDATE（绝不 DELETE）的记忆表。
EXPECTED_UPDATED_TABLES = frozenset(
    {"ai_memory_claim", "ai_memory_suppression", "ai_memory_projection_grant"}
)

#: 合规证据：清理路径绝不触碰的授权表。
FORBIDDEN_TABLES = frozenset({"ai_consent_grant"})


class RecordingMemorySession:
    """记录 SQL 的假会话：`scalar` 返回预设计数，`execute` 记录语句与参数。"""

    def __init__(self, counts: dict[str, int] | None = None) -> None:
        self.counts = dict(counts or {})
        self.selects: list[tuple[str, dict[str, Any]]] = []
        self.writes: list[tuple[str, dict[str, Any]]] = []
        self.commits = 0

    # -- 被测实现用到的两个入口 -------------------------------------------
    async def scalar(self, statement: object, params: dict[str, Any] | None = None) -> Any:
        sql = str(statement)
        values = dict(params or {})
        self.selects.append((sql, values))
        if "next_seq FROM ai_memory_owner_sequence" in sql:
            return self.counts.get("watermark", 7)
        match = re.search(r"FROM (ai_memory_\w+)", sql)
        if match is not None and "COUNT(*)" in sql:
            return self.counts.get(match.group(1), 0)
        return 0

    async def execute(self, statement: object, params: dict[str, Any] | None = None) -> Any:
        sql = str(statement)
        values = dict(params or {})
        self.writes.append((sql, values))
        return _Rowcount(1)

    async def commit(self) -> None:
        self.commits += 1


class _Rowcount:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


def _table_of(statement: str) -> str | None:
    match = re.search(r"(?:FROM|UPDATE|INTO)\s+(ai_\w+)", statement)
    return match.group(1) if match else None


def _where_clause(statement: str) -> str:
    """规范化 WHERE 之后的过滤条件（用于 dry-run / 实删同口径断言）。"""
    marker = " WHERE "
    tail = statement.split(marker, 1)[1] if marker in statement else ""
    return " ".join(tail.split())


# ---------------------------------------------------------------------------
# 1. 逐表行为
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_deletes_exactly_the_declared_tables() -> None:
    db = RecordingMemorySession()
    await purge_memory_for_owner(db, OWNER_ID)

    deleted = {
        table
        for sql, _ in db.writes
        if sql.lstrip().upper().startswith("DELETE")
        for table in [_table_of(sql)]
        if table
    }
    assert deleted == EXPECTED_DELETED_TABLES
    assert not (deleted & FORBIDDEN_TABLES)


@pytest.mark.asyncio
async def test_purge_never_deletes_or_updates_consent_evidence() -> None:
    db = RecordingMemorySession()
    await purge_memory_for_owner(db, OWNER_ID)

    for sql, _ in db.writes:
        assert "ai_consent_grant" not in sql, f"授权证据不得被清理触碰: {sql}"


@pytest.mark.asyncio
async def test_claim_content_is_cleared_before_the_row_is_deleted() -> None:
    db = RecordingMemorySession()
    await purge_memory_for_owner(db, OWNER_ID)

    claim_writes = [
        sql for sql, _ in db.writes if _table_of(sql) == "ai_memory_claim"
    ]
    assert len(claim_writes) == 2, "claim 必须先清内容、再删行（两步都落库）"
    clear, delete = claim_writes
    assert clear.lstrip().upper().startswith("UPDATE")
    assert "value_json = NULL" in clear
    assert "status = 'expired'" in clear
    assert delete.lstrip().upper().startswith("DELETE")


@pytest.mark.asyncio
async def test_suppression_and_grant_are_updated_not_deleted() -> None:
    db = RecordingMemorySession()
    await purge_memory_for_owner(db, OWNER_ID)

    for table in ("ai_memory_suppression", "ai_memory_projection_grant"):
        statements = [sql for sql, _ in db.writes if _table_of(sql) == table]
        assert statements, f"{table} 必须被处理（墓碑/审计保留）"
        for sql in statements:
            verb = sql.lstrip().split()[0].upper()
            assert verb == "UPDATE", f"{table} 只允许 UPDATE，实际 {verb}"


@pytest.mark.asyncio
async def test_deletes_children_before_the_ledger_and_sequence() -> None:
    db = RecordingMemorySession()
    await purge_memory_for_owner(db, OWNER_ID)

    order = [
        _table_of(sql)
        for sql, _ in db.writes
        if sql.lstrip().upper().startswith("DELETE")
    ]
    assert order.index("ai_memory_event") > order.index("ai_memory_claim")
    assert order.index("ai_memory_owner_sequence") > order.index("ai_memory_event")


# ---------------------------------------------------------------------------
# 2. dry-run 与实删同口径
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_writes_nothing_but_reports_the_same_conditions() -> None:
    counts = {
        "ai_memory_event": 3,
        "ai_memory_observation": 4,
        "ai_memory_claim": 5,
        "ai_memory_insight": 1,
        "ai_memory_state": 2,
        "ai_memory_projection": 6,
        "ai_memory_suppression": 1,
        "ai_memory_projection_grant": 2,
        "ai_memory_owner_sequence": 1,
        "watermark": 11,
    }
    dry = RecordingMemorySession(counts)
    stats = await purge_memory_for_owner(dry, OWNER_ID, dry_run=True)
    assert dry.writes == [], "dry-run 绝不允许任何写语句"
    assert stats.dry_run is True
    assert stats.events_deleted == 3
    assert stats.observations_deleted == 4
    assert stats.claims_deleted == 5
    assert stats.insights_deleted == 1
    assert stats.states_deleted == 2
    assert stats.projections_deleted == 6
    assert stats.suppressions_scrubbed == 1
    assert stats.grants_revoked == 2
    assert stats.owner_sequence_deleted == 1
    # counts 里给的是 next_seq=11，即已分配最大序号 10；
    # 围栏取「已分配最大序号」而不是 next_seq（否则会吞掉撤回后第一条新事件）。
    assert stats.watermark == 10


    real = RecordingMemorySession(counts)
    await purge_memory_for_owner(real, OWNER_ID)
    def _conditions(statements: list[tuple[str, dict[str, Any]]]) -> set[tuple[str, str]]:
        """(表名, WHERE 条件) 集合；只保留带 WHERE 的语句。"""
        pairs: set[tuple[str, str]] = set()
        for sql, _ in statements:
            table = _table_of(sql)
            if table is None or " WHERE " not in sql:
                continue
            pairs.add((table, _where_clause(sql)))
        return pairs

    dry_conditions = _conditions(dry.selects)
    real_conditions = _conditions(real.writes)
    # 实删条件必须全部出现在 dry-run 的统计条件里（同口径，不多也不少）。
    assert real_conditions <= dry_conditions
    assert real_conditions, "实删必须有带 WHERE 的语句，否则本断言无意义"


@pytest.mark.asyncio
async def test_dry_run_and_real_delete_agree_on_the_claim_clearing_scope() -> None:
    """claim 的「待清除」口径在 dry-run 与实删中必须是同一个 WHERE。"""
    db = RecordingMemorySession()
    await purge_memory_for_owner(db, OWNER_ID, dry_run=True)
    claim_count_sql = next(
        sql
        for sql, _ in db.selects
        if "COUNT(*)" in sql and "ai_memory_claim" in sql
    )
    assert "value_json IS NOT NULL" in claim_count_sql
    assert "status <> 'expired'" in claim_count_sql


# ---------------------------------------------------------------------------
# 3. 序号水位围栏
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watermark_is_read_inside_the_same_transaction_and_sequence_removed() -> None:
    db = RecordingMemorySession()
    stats = await purge_memory_for_owner(db, OWNER_ID)

    assert stats.watermark == 6  # counts 给 next_seq=7 → 已分配最大序号 6
    watermark_reads = [
        sql for sql, _ in db.selects if "next_seq FROM ai_memory_owner_sequence" in sql
    ]
    assert len(watermark_reads) == 1
    assert "FOR UPDATE" not in watermark_reads[0], "读取水位不锁行（清理非阻塞）"
    assert any(
        sql.lstrip().upper().startswith("DELETE")
        and _table_of(sql) == "ai_memory_owner_sequence"
        for sql, _ in db.writes
    ), "水位归零：撤回后新建记忆必须重新从 1 编号"


@pytest.mark.asyncio
async def test_purge_does_not_commit_its_own_transaction() -> None:
    """事务归调用方：清理必须与 cleanup 任务同事务提交或一起回滚。"""
    db = RecordingMemorySession()
    await purge_memory_for_owner(db, OWNER_ID)
    assert db.commits == 0


# ---------------------------------------------------------------------------
# 4. 清理可观测
# ---------------------------------------------------------------------------


def test_purge_metrics_are_registered() -> None:
    assert {
        METRIC_MEMORY_PURGE_DELETED,
        METRIC_MEMORY_PURGE_DRY_RUN,
    } <= KNOWN_METRICS


@pytest.mark.asyncio
async def test_purge_reports_counts_without_any_user_content() -> None:
    counts = {"ai_memory_event": 2, "watermark": 3}
    db = RecordingMemorySession(counts)
    await purge_memory_for_owner(db, OWNER_ID, scope="memory")

    samples = metric_snapshot().get(METRIC_MEMORY_PURGE_DELETED, [])
    assert samples, "实删必须上报计数指标"
    value, tags = samples[-1]
    assert value == 2.0, "上报的是被真正清除的行数"
    assert tags.get("scope") == "memory"
    # 标签里只允许出现 scope 与围栏标记，不得夹带任何用户内容或原始引文。
    assert set(tags) <= {"scope", "fenced"}


@pytest.mark.asyncio
async def test_dry_run_uses_a_distinct_metric_name() -> None:
    db = RecordingMemorySession({"watermark": 1})
    await purge_memory_for_owner(db, OWNER_ID, dry_run=True)
    assert metric_snapshot().get(METRIC_MEMORY_PURGE_DRY_RUN), (
        "dry-run 必须用独立指标名，避免预检数字被误读成已删除量"
    )


def test_stats_expose_per_table_counts_and_total() -> None:
    stats = MemoryPurgeStats(
        events_deleted=1,
        observations_deleted=2,
        claims_deleted=3,
        watermark=9,
    )
    payload = stats.as_dict()
    assert payload["events_deleted"] == 1
    assert payload["watermark"] == 9
    assert stats.total_deleted() == 6


# ---------------------------------------------------------------------------
# 5. 接入撤回路径
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["memory", "profile", "consent_profile", "user"])
async def test_purge_ai_resources_cleans_memory_for_revocation_scopes(
    monkeypatch: pytest.MonkeyPatch, scope: str
) -> None:
    from app.services import derivation_outbox as outbox

    calls: list[tuple[int, str]] = []

    async def fake_purge(
        db: Any, user_id: int, *, scope: str, fence_seq: int | None = None
    ) -> MemoryPurgeStats:
        calls.append((user_id, scope))
        return MemoryPurgeStats()

    async def noop_tombstone(db: Any, user_id: int, *, task_type: str) -> None:
        return None

    monkeypatch.setattr(outbox, "_purge_ai_redis_cache", _noop)
    monkeypatch.setattr(
        "app.services.ai.memory.purge.purge_memory_for_owner", fake_purge
    )
    monkeypatch.setattr(
        "app.services.ai.tasks.tombstone_owner_tasks", noop_tombstone
    )

    await outbox.purge_ai_resources(_StubSession(), OWNER_ID, scope=scope)
    assert calls == [(OWNER_ID, scope)]


@pytest.mark.asyncio
async def test_purge_ai_resources_leaves_memory_alone_for_other_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import derivation_outbox as outbox

    calls: list[str] = []

    async def fake_purge(
        db: Any, user_id: int, *, scope: str, fence_seq: int | None = None
    ) -> MemoryPurgeStats:
        calls.append(scope)
        return MemoryPurgeStats()

    monkeypatch.setattr(outbox, "_purge_ai_redis_cache", _noop)
    monkeypatch.setattr(
        "app.services.ai.memory.purge.purge_memory_for_owner", fake_purge
    )

    await outbox.purge_ai_resources(
        _StubSession(), OWNER_ID, scope="compatibility"
    )
    assert calls == [], "非撤回 scope（如 compat shadow）不得清记忆"


async def _noop(*args: Any, **kwargs: Any) -> None:
    return None


class _StubSession:
    """只记录写入的哑会话：本组断言只关心记忆清理是否被调用。"""

    async def execute(self, statement: object, params: dict[str, Any] | None = None) -> Any:
        return _Rowcount(0)


# ---------------------------------------------------------------------------
# 6. 清理任务消费
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_handler_routes_memory_scope_to_memory_purge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.ai import profile as profile_module

    seen: list[tuple[int, str]] = []

    async def fake_purge(
        db: Any, user_id: int, *, scope: str, fence_seq: int | None = None
    ) -> None:
        seen.append((user_id, scope))

    monkeypatch.setattr(profile_module, "purge_ai_resources", fake_purge)

    result = await profile_module.cleanup_handler(
        _StubSession(), _Task(scope="memory", resource_id=f"memory:{OWNER_ID}"), "w1"
    )
    assert seen == [(OWNER_ID, "memory")]
    assert result is not None
    assert result[0] == f"cleanup:memory:{OWNER_ID}"


@pytest.mark.asyncio
async def test_cleanup_handler_passes_the_task_fence_to_memory_purge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cleanup 任务路径必须把 payload 里冻结的围栏透传给记忆清理。

    否则 fail-closed 会让 ``profile`` 删除后的记忆**静默永不清理**（合规假账）。
    """
    from app.services.ai import profile as profile_module

    seen: list[int | None] = []

    async def fake_purge(
        db: Any, user_id: int, *, scope: str, fence_seq: int | None = None, **kw: Any
    ) -> None:
        seen.append(fence_seq)

    monkeypatch.setattr(profile_module, "purge_ai_resources", fake_purge)
    monkeypatch.setattr(
        profile_module, "run_cleanup_for_user", _noop
    )

    await profile_module.cleanup_handler(
        _StubSession(),
        _Task(scope="profile", resource_id=f"profile:{OWNER_ID}:personal", fence_seq=9),
        "w1",
    )
    assert seen == [9], "任务路径必须透传围栏，否则记忆永远不会被清理"


@pytest.mark.asyncio
async def test_cleanup_handler_without_fence_degrades_to_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """payload 缺围栏时透传 None，由 _effective_memory_fence 退化为不清。"""
    from app.services.ai import profile as profile_module

    seen: list[int | None] = []

    async def fake_purge(
        db: Any, user_id: int, *, scope: str, fence_seq: int | None = None, **kw: Any
    ) -> None:
        seen.append(fence_seq)

    monkeypatch.setattr(profile_module, "purge_ai_resources", fake_purge)
    monkeypatch.setattr(profile_module, "run_cleanup_for_user", _noop)

    await profile_module.cleanup_handler(
        _StubSession(),
        _Task(scope="profile", resource_id=f"profile:{OWNER_ID}:personal"),
        "w1",
    )
    assert seen == [None]

@pytest.mark.asyncio
async def test_cleanup_handler_rejects_invalid_memory_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.ai import profile as profile_module

    failures: list[tuple[str, bool]] = []

    async def fake_fail(db: Any, task_id: str, worker_id: str, *, error_code: str, retryable: bool) -> None:
        failures.append((error_code, retryable))

    monkeypatch.setattr(profile_module, "fail_task", fake_fail)
    monkeypatch.setattr(
        profile_module, "purge_ai_resources", _unexpected_purge_call
    )

    result = await profile_module.cleanup_handler(
        _StubSession(), _Task(scope="memory", resource_id="memory"), "w1"
    )
    assert result is None
    assert failures == [("AI_INPUT_INVALID", False)], "缺 user_id 必须不可重试失败"


async def _unexpected_purge_call(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("非法 payload 不得触发物理清理")


def test_cleanup_resource_id_parses_memory_scope() -> None:
    from app.services.ai.profile import _parse_cleanup_resource_id

    parsed = _parse_cleanup_resource_id(f"memory:{OWNER_ID}")
    assert parsed.get("user_id") == str(OWNER_ID)


class _Task:
    """cleanup_handler 需要的最小 task 形状。"""

    def __init__(
        self, *, scope: str, resource_id: str, fence_seq: int | None = None
    ) -> None:
        self.task_id = "task-1"
        self.payload_summary: dict[str, Any] = {
            "scope": scope,
            "resource_id": resource_id,
        }
        if fence_seq is not None:
            self.payload_summary["fence_seq"] = fence_seq
        self.source_revision_json = None


# ---------------------------------------------------------------------------
# 7. 两个入口、两种生命周期
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_only_revokes_and_deletes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.ai.memory import lifecycle

    revoked: list[int] = []

    async def fake_revoke(db: Any, *, owner_user_id: int) -> int:
        revoked.append(owner_user_id)
        return 7

    monkeypatch.setattr(
        lifecycle, "revoke_projection_dimensions_for_owner", fake_revoke
    )
    db = RecordingMemorySession()

    count = await lifecycle.pause_memory_for_owner(db, OWNER_ID)

    assert count == 7
    assert revoked == [OWNER_ID]
    assert db.writes == [], "「暂停使用」不得写任何数据（保留 = 可恢复的前提）"


@pytest.mark.asyncio
async def test_forget_schedules_cleanup_and_is_not_recoverable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.ai.memory import lifecycle

    tasks: list[dict[str, Any]] = []

    async def fake_enqueue(db: Any, **kwargs: Any) -> Any:
        tasks.append(kwargs)
        return _Enqueued("task-forget-1")

    async def fake_revoke(db: Any, *, owner_user_id: int) -> int:
        return 7

    monkeypatch.setattr(lifecycle, "enqueue_task", fake_enqueue)
    monkeypatch.setattr(
        lifecycle, "revoke_projection_dimensions_for_owner", fake_revoke
    )
    db = RecordingMemorySession()

    result = await lifecycle.forget_memory_for_owner(
        db, OWNER_ID, idempotency_key="key-1"
    )

    assert tasks and tasks[0]["task_type"] == "cleanup"
    assert result.task_id == "task-forget-1"
    assert result.revoked_dimensions == 7
    # payload 必须声明 memory scope，否则 Worker 会走错清理分支。
    payload_writes = [
        values for sql, values in db.writes if "UPDATE ai_task" in sql
    ]
    assert payload_writes, "必须写入 cleanup 任务的 payload_summary"
    assert '"scope": "memory"' in payload_writes[0]["payload_summary"]
    assert f'"resource_id": "memory:{OWNER_ID}"' in payload_writes[0]["payload_summary"]


@pytest.mark.asyncio
async def test_forget_is_idempotent_on_the_same_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.ai.memory import lifecycle

    keys: list[str] = []

    async def fake_enqueue(db: Any, **kwargs: Any) -> Any:
        keys.append(str(kwargs["idempotency_key"]))
        return _Enqueued("task-forget-1")

    async def fake_revoke(db: Any, *, owner_user_id: int) -> int:
        return 0

    monkeypatch.setattr(lifecycle, "enqueue_task", fake_enqueue)
    monkeypatch.setattr(
        lifecycle, "revoke_projection_dimensions_for_owner", fake_revoke
    )
    db = RecordingMemorySession()

    first = await lifecycle.forget_memory_for_owner(db, OWNER_ID, idempotency_key="k")
    second = await lifecycle.forget_memory_for_owner(db, OWNER_ID, idempotency_key="k")
    assert keys[0] == keys[1], "同一 key 必须派生同一个任务幂等键（可回放）"
    assert first.task_id == second.task_id


def test_forget_task_key_respects_the_ai_task_column_limit() -> None:
    from app.services.ai.memory.lifecycle import _forget_task_idempotency_key

    key = _forget_task_idempotency_key("k" * 128)
    assert len(key) <= 128
    # 前 128 字符相同、尾部不同的键不得坍缩成同一个任务。
    assert key != _forget_task_idempotency_key("k" * 127 + "j")


class _Enqueued:
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id


def test_both_entrypoints_are_exposed_on_the_router() -> None:
    from app.api.routes import ai_memory

    paths = {route.path for route in ai_memory.router.routes}
    assert "/memory/pause" in paths
    assert "/memory/forget" in paths


# ---------------------------------------------------------------------------
# 8. 序号围栏：迟到清理不得误删撤回后新建的记忆
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fence_is_last_allocated_seq_not_next_seq() -> None:
    """围栏必须是「已分配的最大序号」，不是 ``next_seq``。

    ``next_seq`` 是下一个待分配值：事件写入时先拿它当 ``server_seq`` 再递增。
    若把 ``next_seq`` 当围栏，``server_seq <= fence_seq`` 会连撤回后写入的
    第一条新事件（恰好拿到那个值）一起删掉——不可恢复的数据损失。
    """
    # 该 owner 已分配 seq 1..5，next_seq = 6。
    db = RecordingMemorySession({"watermark": 6})
    stats = await purge_memory_for_owner(db, OWNER_ID, dry_run=True)

    assert stats.watermark == 5, "围栏应为已分配最大序号 5，而非 next_seq=6"
    for sql, values in db.selects:
        if "last_event_seq <= :fence_seq" in sql:
            assert values.get("fence_seq") == 5, (
                "过滤条件必须用 5；用 6 会吞掉撤回后第一条新事件"
            )


@pytest.mark.asyncio
async def test_current_owner_sequence_returns_zero_without_a_row() -> None:
    """无序号行 = 该 owner 从未写入记忆 → 围栏 0（不是 None，不等于全量清理）。

    ``fence_seq=0`` 下所有 ``<= 0`` 的过滤都不命中，即什么都不删——这正是
    「没有内容可清」的正确行为；而 ``fence_seq=None`` 是全量清理，二者必须区分。
    """
    from app.services.ai.memory.purge import current_owner_sequence

    db = RecordingMemorySession({"watermark": 0})
    assert await current_owner_sequence(db, OWNER_ID) == 0


@pytest.mark.asyncio
async def test_fence_zero_deletes_nothing_while_none_is_a_full_wipe() -> None:
    """围栏 0 与围栏 None 的语义必须不同：前者不删，后者全删。"""

    fenced = RecordingMemorySession()
    await purge_memory_for_owner(fenced, OWNER_ID, fence_seq=0)

    for sql, _ in fenced.writes:
        if not sql.lstrip().upper().startswith(("DELETE", "UPDATE")):
            continue
        table = _table_of(sql)
        if table in {"ai_memory_projection", "ai_memory_projection_grant"}:
            # 这两张表没有序号列，按 status 收窄（保留 active 投影与已撤销授权）。
            assert "status" in sql, f"{table} 必须按 status 收窄"
            continue
        assert "<= :fence_seq" in sql, (
            f"{table} 在围栏 0 时必须带序号过滤，否则会删到不该删的行"
        )
    unfenced = RecordingMemorySession()
    await purge_memory_for_owner(unfenced, OWNER_ID)
    assert any(
        sql.lstrip().upper().startswith("DELETE")
        and "fence_seq" not in sql
        for sql, _ in unfenced.writes
    ), "无围栏（None）才是全量清理"



@pytest.mark.asyncio
async def test_fence_filters_ledger_and_views_by_sequence() -> None:
    """有围栏时，账本按 server_seq、视图按 last_event_seq 过滤。"""
    db = RecordingMemorySession()
    await purge_memory_for_owner(db, OWNER_ID, fence_seq=5)

    for table in (
        "ai_memory_observation",
        "ai_memory_insight",
        "ai_memory_state",
        "ai_memory_claim",
    ):
        statements = [sql for sql, _ in db.writes if _table_of(sql) == table]
        assert statements, f"{table} 必须被处理"
        for sql in statements:
            assert "last_event_seq <= :fence_seq" in sql, (
                f"{table} 缺少围栏过滤，会误删撤回后新建的行"
            )
    event_writes = [
        sql for sql, _ in db.writes if _table_of(sql) == "ai_memory_event"
    ]
    assert event_writes and all(
        "server_seq <= :fence_seq" in sql for sql in event_writes
    )


@pytest.mark.asyncio
async def test_fence_preserves_new_generation_projections() -> None:
    """投影无序号列：有围栏时只能删已失效的旧代投影。"""
    db = RecordingMemorySession()
    await purge_memory_for_owner(db, OWNER_ID, fence_seq=5)

    projection_writes = [
        sql for sql, _ in db.writes if _table_of(sql) == "ai_memory_projection"
    ]
    assert projection_writes
    for sql in projection_writes:
        assert "status <> 'active'" in sql, (
            "有围栏时必须保留 active 投影（可能是撤回后重建的新代）"
        )
        assert "last_event_seq" not in sql, "投影表没有 last_event_seq 列"


@pytest.mark.asyncio
async def test_fenced_purge_keeps_the_sequence_row() -> None:
    """有围栏时保留 owner_sequence：幸存新代行与后续写入的相对顺序不被破坏。"""
    db = RecordingMemorySession()
    stats = await purge_memory_for_owner(db, OWNER_ID, fence_seq=5)

    assert stats.fence_seq == 5
    assert stats.watermark == 5
    assert stats.owner_sequence_deleted == 0
    assert not [
        sql
        for sql, _ in db.writes
        if sql.lstrip().upper().startswith("DELETE")
        and _table_of(sql) == "ai_memory_owner_sequence"
    ], "有围栏时删除 sequence 会让新事件拿到更小的 seq，破坏分页顺序"


@pytest.mark.asyncio
async def test_unfenced_purge_is_full_wipe_and_resets_the_sequence() -> None:
    """无围栏（账号注销）才全量删除并归零水位。"""
    db = RecordingMemorySession({"watermark": 9, "ai_memory_owner_sequence": 1})
    stats = await purge_memory_for_owner(db, OWNER_ID)

    assert stats.fence_seq is None
    assert stats.watermark == 8  # counts 给 next_seq=9 → 已分配最大序号 8
    assert stats.owner_sequence_deleted == 1
    assert any(
        sql.lstrip().upper().startswith("DELETE")
        and _table_of(sql) == "ai_memory_owner_sequence"
        for sql, _ in db.writes
    )
    for sql, _ in db.writes:
        assert "fence_seq" not in sql, "全量清理不应带围栏条件"


@pytest.mark.asyncio
async def test_forget_captures_fence_inside_the_request_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """forget 必须在请求事务内读水位并冻结进 payload（不是清理时再读）。"""
    from app.services.ai.memory import lifecycle

    read_order: list[str] = []

    async def fake_sequence(db: Any, owner_user_id: int) -> int:
        read_order.append("read_sequence")
        return 4

    async def fake_enqueue(db: Any, **kwargs: Any) -> Any:
        read_order.append("enqueue")
        return _Enqueued("t1")

    async def fake_revoke(db: Any, *, owner_user_id: int) -> int:
        return 1

    monkeypatch.setattr(lifecycle, "current_owner_sequence", fake_sequence)
    monkeypatch.setattr(lifecycle, "enqueue_task", fake_enqueue)
    monkeypatch.setattr(
        lifecycle, "revoke_projection_dimensions_for_owner", fake_revoke
    )
    db = RecordingMemorySession()

    result = await lifecycle.forget_memory_for_owner(
        db, OWNER_ID, idempotency_key="k1"
    )

    assert result.fence_seq == 4
    assert read_order[0] == "read_sequence", "围栏必须在入队前读取并冻结"
    payloads = [
        values["payload_summary"]
        for sql, values in db.writes
        if "UPDATE ai_task" in sql
    ]
    assert payloads and '"fence_seq": 4' in payloads[0]


@pytest.mark.asyncio
async def test_memory_scope_without_fence_refuses_to_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """旧任务/漏写围栏的 memory cleanup：退化为不清，绝不退化为全删。"""
    from app.services import derivation_outbox as outbox

    captured: list[int | None] = []

    async def fake_purge(
        db: Any, user_id: int, *, scope: str, fence_seq: int | None = None
    ) -> MemoryPurgeStats:
        captured.append(fence_seq)
        return MemoryPurgeStats()

    monkeypatch.setattr(outbox, "_purge_ai_redis_cache", _noop)
    monkeypatch.setattr(
        "app.services.ai.memory.purge.purge_memory_for_owner", fake_purge
    )

    await outbox.purge_ai_resources(_StubSession(), OWNER_ID, scope="memory")
    assert captured == [0], (
        "缺少围栏的 memory 清理必须退化为不清（0），否则会误删撤回后新建的记忆"
    )


@pytest.mark.asyncio
async def test_account_deletion_still_does_a_full_wipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """账号注销没有未来行：允许无围栏全量清理。"""
    from app.services import derivation_outbox as outbox

    captured: list[int | None] = []

    async def fake_purge(
        db: Any, user_id: int, *, scope: str, fence_seq: int | None = None
    ) -> MemoryPurgeStats:
        captured.append(fence_seq)
        return MemoryPurgeStats()

    async def noop_tombstone(db: Any, user_id: int, *, task_type: str) -> None:
        return None

    monkeypatch.setattr(outbox, "_purge_ai_redis_cache", _noop)
    monkeypatch.setattr(
        "app.services.ai.memory.purge.purge_memory_for_owner", fake_purge
    )
    monkeypatch.setattr("app.services.ai.tasks.tombstone_owner_tasks", noop_tombstone)

    await outbox.purge_ai_resources(_StubSession(), OWNER_ID, scope="user")
    assert captured == [None], "账号注销必须全量清理（无围栏）"

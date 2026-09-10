"""Memory Kernel Core v1 ledger unit tests (Task 3).

Uses an in-memory fake session routed by SQL substring (same pattern as
``tests/test_ai_profile_sessions.py``).  The fake enforces the frozen
constraints that matter at unit level: event (owner, server_seq) and
(owner, idempotency_key) uniqueness, claim/suppression canonical uniqueness,
and commit/rollback snapshot semantics.

Real-database concurrency and constraint behaviour is covered separately by
``tests/integration/ai/test_ai_memory_real_db.py``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from app.schemas.ai_memory import (
    MemoryEventInput,
    MemoryEventRecord,
    MemoryIdempotencyConflict,
)
from app.services.ai.memory.ledger import MemoryLedger
from app.services.ai.memory.materializer import MemoryMaterializer

pytestmark = pytest.mark.asyncio


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)

OBSERVATION_PAYLOAD = {
    "canonical_key": "personal:lifestyle:coffee",
    "dimension": "lifestyle",
    "value": "每天喝咖啡",
    "confidence": 0.82,
    "fact_kind": "about_user",
}


def observation_event(**overrides: Any) -> MemoryEventInput:
    fields: dict[str, Any] = {
        "owner_user_id": 42,
        "subject": "personal",
        "node_type": "observation",
        "event_type": "observation_proposed",
        "payload": dict(OBSERVATION_PAYLOAD),
        "source_kind": "user_explicit",
        "source_quote": "我每天早上都要喝一杯咖啡",
        "idempotency_key": "seed-observation-001",
    }
    fields.update(overrides)
    return MemoryEventInput(**fields)


# ---------------------------------------------------------------------------
# In-memory store + fake session (shared with materializer tests)
# ---------------------------------------------------------------------------


class _MappingResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> "_MappingResult":
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None


class _WriteResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class MemoryStore:
    """In-memory image of the seven memory tables (JSON columns as text)."""

    def __init__(self) -> None:
        self.owner_sequences: dict[int, int] = {}
        self.events: list[dict[str, Any]] = []
        self.observations: dict[str, dict[str, Any]] = {}
        self.claims: dict[str, dict[str, Any]] = {}
        self.insights: dict[str, dict[str, Any]] = {}
        self.states: dict[str, dict[str, Any]] = {}
        self.suppressions: dict[str, dict[str, Any]] = {}
        self.outbox: dict[str, dict[str, Any]] = {}
        self.receipts: dict[tuple[str, str], dict[str, Any]] = {}
        # 旧业务表回填扫描（Task 8）：预 join 好的行由测试直接注入。
        self.legacy_candidates: list[dict[str, Any]] = []
        self.legacy_revision_fields: list[dict[str, Any]] = []

    def clear_views(self) -> None:
        self.observations.clear()
        self.claims.clear()
        self.insights.clear()
        self.states.clear()
        self.suppressions.clear()


class FakeMemorySession:
    def __init__(self, store: MemoryStore) -> None:
        self._store = store
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.commits = 0
        self.rollbacks = 0
        self._snapshot: dict[str, Any] | None = None

    # -- transaction surface ------------------------------------------------
    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1
        self._snapshot = self._capture()

    async def rollback(self) -> None:
        self.rollbacks += 1
        if self._snapshot is not None:
            self._restore(self._snapshot)

    def _capture(self) -> dict[str, Any]:
        return {
            "owner_sequences": dict(self._store.owner_sequences),
            "events": [dict(row) for row in self._store.events],
            "observations": {k: dict(v) for k, v in self._store.observations.items()},
            "claims": {k: dict(v) for k, v in self._store.claims.items()},
            "insights": {k: dict(v) for k, v in self._store.insights.items()},
            "states": {k: dict(v) for k, v in self._store.states.items()},
            "suppressions": {k: dict(v) for k, v in self._store.suppressions.items()},
            "outbox": {k: dict(v) for k, v in self._store.outbox.items()},
            "receipts": {k: dict(v) for k, v in self._store.receipts.items()},
        }

    def _restore(self, snapshot: dict[str, Any]) -> None:
        self._store.owner_sequences = dict(snapshot["owner_sequences"])
        self._store.events = [dict(row) for row in snapshot["events"]]
        self._store.observations = {k: dict(v) for k, v in snapshot["observations"].items()}
        self._store.claims = {k: dict(v) for k, v in snapshot["claims"].items()}
        self._store.insights = {k: dict(v) for k, v in snapshot["insights"].items()}
        self._store.states = {k: dict(v) for k, v in snapshot["states"].items()}
        self._store.suppressions = {k: dict(v) for k, v in snapshot["suppressions"].items()}
        self._store.outbox = {k: dict(v) for k, v in snapshot["outbox"].items()}
        self._store.receipts = {k: dict(v) for k, v in snapshot["receipts"].items()}

    # -- execution surface --------------------------------------------------
    async def execute(self, statement: object, params: dict[str, Any] | None = None) -> Any:
        sql = str(statement)
        values = dict(params or {})
        self.calls.append((sql, values))
        return self._route(sql, values)

    def _route(self, sql: str, v: dict[str, Any]) -> Any:
        store = self._store
        if "INSERT INTO derivation_outbox" in sql:
            event_id = str(v["event_id"])
            if event_id in store.outbox:
                return _WriteResult(rowcount=0)
            values = dict(v)
            # SQL 字面量列（非绑定参数）按语句语义补齐，与真实 DB 一致。
            if "'ai_memory'" in sql:
                values["aggregate_type"] = "ai_memory"
            values["published_at"] = _utcnow()
            store.outbox[event_id] = values
            return _WriteResult(rowcount=1)
        if "INSERT IGNORE INTO derivation_consumer_receipt" in sql:
            key = (str(v["event_id"]), str(v["consumer_name"]))
            if key in store.receipts:
                return _WriteResult(rowcount=0)
            store.receipts[key] = dict(v)
            return _WriteResult(rowcount=1)
        if "UPDATE derivation_consumer_receipt" in sql:
            return _WriteResult(rowcount=1)
        if "DELETE FROM derivation_consumer_receipt" in sql:
            key = (str(v["event_id"]), str(v["consumer_name"]))
            return _WriteResult(rowcount=1 if store.receipts.pop(key, None) is not None else 0)
        if "UPDATE derivation_outbox" in sql:
            row = store.outbox.get(str(v["event_id"]))
            if row is None:
                return _WriteResult(rowcount=0)
            row.update({k: val for k, val in v.items() if k != "event_id"})
            return _WriteResult(rowcount=1)
        if "FROM ai_profile_candidate" in sql and "id >= :resume_from" in sql:
            resume_from = int(v["resume_from"])
            rows = [
                dict(row)
                for row in store.legacy_candidates
                if int(row["id"]) >= resume_from
            ]
            rows.sort(key=lambda row: int(row["id"]))
            return _MappingResult(rows[: int(v["batch"])])
        if "FROM ai_profile_revision_field" in sql and "id >= :resume_from" in sql:
            resume_from = int(v["resume_from"])
            rows = [
                dict(row)
                for row in store.legacy_revision_fields
                if int(row["id"]) >= resume_from
            ]
            rows.sort(key=lambda row: int(row["id"]))
            return _MappingResult(rows[: int(v["batch"])])
        if "INSERT INTO ai_memory_owner_sequence" in sql:
            store.owner_sequences.setdefault(int(v["owner_user_id"]), 1)
            return _WriteResult(rowcount=1)
        if "FROM ai_memory_owner_sequence" in sql and "FOR UPDATE" in sql:
            owner = int(v["owner_user_id"])
            next_seq = store.owner_sequences.get(owner)
            return _MappingResult([{"next_seq": next_seq}] if next_seq is not None else [])
        if "UPDATE ai_memory_owner_sequence" in sql:
            store.owner_sequences[int(v["owner_user_id"])] = int(v["next_seq"])
            return _WriteResult(rowcount=1)
        if "FROM ai_memory_event" in sql and "event_id = :event_id" in sql:
            row = next(
                (e for e in store.events if e["event_id"] == str(v["event_id"])), None
            )
            return _MappingResult([row] if row else [])
        if "FROM ai_memory_event" in sql and "idempotency_key = :idempotency_key" in sql:
            row = self._find_event(int(v["owner_user_id"]), str(v["idempotency_key"]))
            return _MappingResult([row] if row else [])
        if "INSERT INTO ai_memory_event" in sql:
            return _WriteResult(rowcount=self._insert_event(v))
        if "FROM ai_memory_event" in sql and "server_seq > :after_seq" in sql:
            owner = int(v["owner_user_id"])
            rows = [
                row
                for row in store.events
                if row["owner_user_id"] == owner
                and row["server_seq"] > int(v["after_seq"])
            ]
            rows.sort(key=lambda row: row["server_seq"])
            return _MappingResult(rows[: int(v["limit"])])
        if "FROM ai_memory_claim" in sql and "claim_id = :claim_id" in sql and "FOR UPDATE" in sql:
            row = store.claims.get(str(v["claim_id"]))
            if row is None or row["owner_user_id"] != int(v["owner_user_id"]):
                return _MappingResult([])
            return _MappingResult([dict(row)])
        if "FROM ai_memory_claim" in sql and "canonical_key LIKE" in sql:
            digest = str(v["digest"])
            rows = [
                dict(row)
                for row in store.claims.values()
                if row["owner_user_id"] == int(v["owner_user_id"])
                and row["subject"] == v["subject"]
                and str(row["canonical_key"]).endswith(":" + digest)
            ]
            return _MappingResult(rows[:1])
        if "FROM ai_memory_claim" in sql and "status IN (" in sql and "FOR UPDATE" not in sql:
            owner = int(v["owner_user_id"])
            after_seq = int(v.get("after_seq", 0))
            limit = int(v.get("limit", 20))
            status_part = sql.split("status IN (", 1)[1].split(")", 1)[0]
            allowed = tuple(s.strip().strip("'") for s in status_part.split(","))
            # NOT EXISTS 语义：canonical key 命中活动墓碑的 Claim 不出列表。
            active_tombstones = {
                (
                    row["owner_user_id"],
                    row["subject"],
                    row["namespace"],
                    row["canonical_key"],
                )
                for row in self._store.suppressions.values()
                if row["status"] == "active"
            }
            rows = [
                dict(row)
                for row in store.claims.values()
                if row["owner_user_id"] == owner
                and row["subject"] == v["subject"]
                and row["status"] in allowed
                and row["last_event_seq"] > after_seq
                and (
                    row["owner_user_id"],
                    row["subject"],
                    row["namespace"],
                    row["canonical_key"],
                )
                not in active_tombstones
            ]
            rows.sort(key=lambda row: row["last_event_seq"])
            return _MappingResult(rows[:limit])
        if "FROM ai_memory_claim" in sql and "FOR UPDATE" in sql:
            row = self._find_claim(v)
            return _MappingResult([row] if row else [])
        if "INSERT INTO ai_memory_claim" in sql:
            return _WriteResult(rowcount=self._insert_claim(v))
        if "UPDATE ai_memory_claim" in sql:
            claim = store.claims.get(str(v["claim_id"]))
            if claim is None:
                return _WriteResult(rowcount=0)
            claim.update({k: val for k, val in v.items() if k != "claim_id"})
            return _WriteResult(rowcount=1)
        if "FROM ai_memory_observation" in sql and "observation_id = :observation_id" in sql:
            row = store.observations.get(str(v["observation_id"]))
            return _MappingResult([{"observation_id": row["observation_id"]}] if row else [])
        if "INSERT INTO ai_memory_observation" in sql:
            store.observations[str(v["observation_id"])] = dict(v)
            return _WriteResult(rowcount=1)
        if "UPDATE ai_memory_observation" in sql:
            updated = 0
            for row in store.observations.values():
                if (
                    row["owner_user_id"] == int(v["owner_user_id"])
                    and row["subject"] == v["subject"]
                    and row["namespace"] == v["namespace"]
                    and row["canonical_key"] == v["canonical_key"]
                    and row["status"] in ("proposed", "active")
                ):
                    row.update({k: val for k, val in v.items() if k not in ("owner_user_id", "subject", "namespace", "canonical_key")})
                    updated += 1
            return _WriteResult(rowcount=updated)
        if "FROM ai_memory_suppression" in sql and "suppression_id = :suppression_id" in sql:
            row = store.suppressions.get(str(v["suppression_id"]))
            if row is None or row["owner_user_id"] != int(v["owner_user_id"]):
                return _MappingResult([])
            return _MappingResult([dict(row)])
        if "FROM ai_memory_projection_grant" in sql and "status = 'active'" in sql:
            # Phase 2 投影维度扫描：Core fake 不持有投影授权，恒为空。
            return _MappingResult([])
        if "FROM ai_memory_suppression" in sql and "FOR UPDATE" in sql:
            row = self._find_suppression(v)
            return _MappingResult([row] if row else [])
        if "INSERT INTO ai_memory_suppression" in sql:
            return _WriteResult(rowcount=self._insert_suppression(v))
        if "UPDATE ai_memory_suppression" in sql:
            suppression = store.suppressions.get(str(v["suppression_id"]))
            if suppression is None:
                return _WriteResult(rowcount=0)
            suppression.update({k: val for k, val in v.items() if k != "suppression_id"})
            return _WriteResult(rowcount=1)
        if "FROM ai_memory_state" in sql and "valid_until < :now" in sql:
            owner = v.get("owner_user_id")
            rows = [
                dict(row)
                for row in store.states.values()
                if row["status"] == "active"
                and row["valid_until"] < v["now"]
                and (owner is None or row["owner_user_id"] == int(owner))
            ]
            return _MappingResult(rows[: int(v.get("limit", 200))])
        if "FROM ai_memory_state" in sql and "FOR UPDATE" in sql:
            row = store.states.get(str(v["state_id"]))
            if row is None or row["owner_user_id"] != int(v["owner_user_id"]):
                return _MappingResult([])
            return _MappingResult([{"state_id": row["state_id"], "status": row["status"]}])
        if "INSERT INTO ai_memory_state" in sql:
            store.states[str(v["state_id"])] = dict(v)
            return _WriteResult(rowcount=1)
        if "UPDATE ai_memory_state" in sql:
            state = store.states.get(str(v["state_id"]))
            if state is None or state["owner_user_id"] != int(v["owner_user_id"]):
                return _WriteResult(rowcount=0)
            state.update({k: val for k, val in v.items() if k not in ("state_id", "owner_user_id")})
            return _WriteResult(rowcount=1)
        if "FROM ai_memory_insight" in sql and "status IN ('proposed', 'confirmed')" in sql:
            owner = int(v["owner_user_id"])
            rows = [
                dict(row)
                for row in store.insights.values()
                if row["owner_user_id"] == owner
                and row["status"] in ("proposed", "confirmed")
            ]
            return _MappingResult(rows)
        if "FROM ai_memory_insight" in sql and "FOR UPDATE" in sql:
            row = store.insights.get(str(v["insight_id"]))
            if row is None or row["owner_user_id"] != int(v["owner_user_id"]):
                return _MappingResult([])
            return _MappingResult([{"insight_id": row["insight_id"], "status": row["status"]}])
        if "INSERT INTO ai_memory_insight" in sql:
            store.insights[str(v["insight_id"])] = dict(v)
            return _WriteResult(rowcount=1)
        if "UPDATE ai_memory_insight" in sql:
            insight = store.insights.get(str(v["insight_id"]))
            if insight is None or insight["owner_user_id"] != int(v["owner_user_id"]):
                return _WriteResult(rowcount=0)
            insight.update({k: val for k, val in v.items() if k not in ("insight_id", "owner_user_id")})
            return _WriteResult(rowcount=1)
        raise AssertionError(f"FakeMemorySession: unrouted SQL: {sql[:120]}")

    # -- row helpers ---------------------------------------------------------
    @staticmethod
    def _dumps(value: Any) -> str | None:
        if value is None:
            return None
        return json.dumps(value, sort_keys=True, ensure_ascii=False)

    def _find_event(self, owner: int, idempotency_key: str) -> dict[str, Any] | None:
        for row in self._store.events:
            if row["owner_user_id"] == owner and row["idempotency_key"] == idempotency_key:
                return row
        return None

    def _insert_event(self, v: dict[str, Any]) -> int:
        store = self._store
        if any(row["event_id"] == v["event_id"] for row in store.events):
            raise ValueError("duplicate event_id (uk_ai_memory_event_event_id)")
        if any(
            row["owner_user_id"] == v["owner_user_id"] and row["server_seq"] == v["server_seq"]
            for row in store.events
        ):
            raise ValueError("duplicate owner+server_seq (uk_ai_memory_event_owner_seq)")
        if self._find_event(int(v["owner_user_id"]), str(v["idempotency_key"])) is not None:
            raise ValueError("duplicate owner+idempotency_key (uk_ai_memory_event_owner_idem)")
        store.events.append(dict(v))
        return 1

    def _find_claim(self, v: dict[str, Any]) -> dict[str, Any] | None:
        for row in self._store.claims.values():
            if (
                row["owner_user_id"] == int(v["owner_user_id"])
                and row["subject"] == v["subject"]
                and row["namespace"] == v["namespace"]
                and row["canonical_key"] == v["canonical_key"]
            ):
                return row
        return None

    def _insert_claim(self, v: dict[str, Any]) -> int:
        if self._find_claim(v) is not None:
            raise ValueError("duplicate canonical claim (uk_ai_memory_claim_canonical)")
        self._store.claims[str(v["claim_id"])] = dict(v)
        return 1

    def _find_suppression(self, v: dict[str, Any]) -> dict[str, Any] | None:
        for row in self._store.suppressions.values():
            if (
                row["owner_user_id"] == int(v["owner_user_id"])
                and row["subject"] == v["subject"]
                and row["namespace"] == v["namespace"]
                and row["canonical_key"] == v["canonical_key"]
            ):
                return row
        return None

    def _insert_suppression(self, v: dict[str, Any]) -> int:
        if self._find_suppression(v) is not None:
            raise ValueError("duplicate canonical suppression (uk_ai_memory_suppression_canonical)")
        self._store.suppressions[str(v["suppression_id"])] = dict(v)
        return 1


class CountingMaterializer(MemoryMaterializer):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.apply_count = 0

    async def apply(self, record: MemoryEventRecord) -> None:
        self.apply_count += 1
        await super().apply(record)


def make_ledger(store: MemoryStore | None = None) -> tuple[MemoryLedger, FakeMemorySession, MemoryStore, CountingMaterializer]:
    store = store or MemoryStore()
    session = FakeMemorySession(store)
    materializer = CountingMaterializer(session)
    ledger = MemoryLedger(session, materializer=materializer)
    return ledger, session, store, materializer


# ---------------------------------------------------------------------------
# server_seq 分配与幂等回放
# ---------------------------------------------------------------------------


async def test_append_assigns_monotonic_unique_server_seq() -> None:
    ledger, _, store, _ = make_ledger()
    first = await ledger.append(observation_event())
    second = await ledger.append(observation_event(idempotency_key="seed-2"))
    third = await ledger.append(observation_event(idempotency_key="seed-3"))
    assert (first.server_seq, second.server_seq, third.server_seq) == (1, 2, 3)
    assert len({first.event_id, second.event_id, third.event_id}) == 3
    assert len(store.events) == 3
    assert all(record.owner_user_id == 42 for record in (first, second, third))
    assert isinstance(first, MemoryEventRecord)


async def test_replay_same_idempotency_returns_same_event_without_rematerializing() -> None:
    ledger, _, store, materializer = make_ledger()
    original = await ledger.append(observation_event())
    replay = await ledger.append(observation_event())
    assert replay.event_id == original.event_id
    assert replay.server_seq == original.server_seq
    assert len(store.events) == 1
    assert materializer.apply_count == 1, "replayed request must not re-materialize"


async def test_same_idempotency_key_different_payload_is_stable_conflict() -> None:
    ledger, _, _, _ = make_ledger()
    await ledger.append(observation_event())
    with pytest.raises(MemoryIdempotencyConflict) as excinfo:
        await ledger.append(
            observation_event(
                payload={**OBSERVATION_PAYLOAD, "confidence": 0.10},
            )
        )
    assert excinfo.value.owner_user_id == 42
    assert excinfo.value.idempotency_key == "seed-observation-001"
    assert excinfo.value.existing_event_id


async def test_replay_isolated_per_owner() -> None:
    ledger, _, store, _ = make_ledger()
    await ledger.append(observation_event())  # owner 42
    other = await ledger.append(observation_event(owner_user_id=43))
    assert other.server_seq == 1, "idempotency keys are scoped per owner"
    assert len(store.events) == 2


# ---------------------------------------------------------------------------
# 固定事务顺序与账本不可变性
# ---------------------------------------------------------------------------


async def test_transaction_order_lock_then_idem_then_insert_then_materialize() -> None:
    ledger, session, _, _ = make_ledger()
    await ledger.append(observation_event())
    sqls = [sql for sql, _ in session.calls]
    lock_index = next(i for i, s in enumerate(sqls) if "ai_memory_owner_sequence" in s and "FOR UPDATE" in s)
    idem_index = next(i for i, s in enumerate(sqls) if "FROM ai_memory_event" in s and "idempotency_key" in s)
    insert_index = next(i for i, s in enumerate(sqls) if "INSERT INTO ai_memory_event" in s)
    materialize_index = next(i for i, s in enumerate(sqls) if "INSERT INTO ai_memory_observation" in s)
    assert lock_index < idem_index < insert_index < materialize_index


def test_event_ledger_has_no_update_or_delete_path() -> None:
    import inspect

    from app.services.ai.memory import ledger as ledger_module

    source = inspect.getsource(ledger_module)
    assert "UPDATE ai_memory_event" not in source
    assert "DELETE FROM ai_memory_event" not in source


async def test_append_does_not_commit_caller_owns_transaction() -> None:
    ledger, session, _, _ = make_ledger()
    await ledger.append(observation_event())
    assert session.commits == 0


# ---------------------------------------------------------------------------
# list_events 与按 owner 重建
# ---------------------------------------------------------------------------


async def test_list_events_is_owner_scoped_and_ordered() -> None:
    ledger, _, _, _ = make_ledger()
    await ledger.append(observation_event())
    await ledger.append(observation_event(idempotency_key="seed-2", owner_user_id=43))
    second = await ledger.append(observation_event(idempotency_key="seed-3"))
    records = await ledger.list_events(42)
    assert [r.server_seq for r in records] == [1, 2]
    assert all(r.owner_user_id == 42 for r in records)
    page = await ledger.list_events(42, after_seq=1)
    assert [r.event_id for r in page] == [second.event_id]


# ---------------------------------------------------------------------------
# 记录构建
# ---------------------------------------------------------------------------


async def test_record_round_trips_payload_and_causal_ids() -> None:
    ledger, _, _, _ = make_ledger()
    causal = await ledger.append(observation_event(idempotency_key="seed-root"))
    child = await ledger.append(
        observation_event(
            idempotency_key="seed-child",
            causal_event_ids=(causal.event_id,),
            source_kind="inferred",
        )
    )
    assert child.causal_event_ids == (causal.event_id,)
    stored = (await ledger.list_events(42))[1]
    assert stored.payload["canonical_key"] == OBSERVATION_PAYLOAD["canonical_key"]
    assert stored.payload["confidence"] == pytest.approx(0.82)
    assert stored.consent_scope == "profile_text_extract"
    assert stored.occurred_at is not None


async def test_append_rejects_policy_denied_subject_pair() -> None:
    from pydantic import ValidationError

    from app.services.ai.memory.policy import MemoryPolicy, MemoryPolicyDenied

    ledger, _, store, _ = make_ledger()
    # schema 层先拒绝：subject ↔ fact_kind 互锁在信封校验时生效。
    with pytest.raises(ValidationError):
        observation_event(
            subject="ideal_partner",
            payload={
                "canonical_key": "ideal_partner:lifestyle:x",
                "dimension": "lifestyle",
                "value": "x",
                "confidence": 0.5,
                "fact_kind": "about_user",
            },
        )
    assert store.events == []
    # 纵深防御：绕过 schema 的调用路径同样被 policy 守卫拦下。
    with pytest.raises(MemoryPolicyDenied):
        MemoryPolicy.assert_subject_write("ideal_partner", fact_kind="about_user")


async def test_owner_lock_ensures_row_before_taking_for_update() -> None:
    """对抗性审查回归：owner 首写必须是 ensure-first。

    旧实现"先 SELECT FOR UPDATE 再 insert-if-missing"在 REPEATABLE READ 下
    让并发首写互持间隙锁（裸连接实验实测 1213 死锁）；ensure-first 让后到
    事务阻塞在 duplicate-key 检查上按行锁串行。此测试确定性钉死语句顺序。
    """

    ledger, session, _, _ = make_ledger()
    await ledger.append(observation_event())
    sqls = [sql for sql, _ in session.calls]
    ensure_index = next(
        i for i, s in enumerate(sqls) if "INSERT INTO ai_memory_owner_sequence" in s
    )
    lock_index = next(
        i
        for i, s in enumerate(sqls)
        if "FROM ai_memory_owner_sequence" in s and "FOR UPDATE" in s
    )
    assert ensure_index < lock_index, "ensure-first 顺序不可反转"

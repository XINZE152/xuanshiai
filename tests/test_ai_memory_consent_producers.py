"""Consent producer → ProjectionGrant 接线测试（Phase 3 Task 1）。

授权生产者语义：
- ``profile_text_extract`` consent 成功授予后，自动为全部消费维度创建
  ProjectionGrant（服务端重读 consent 派生 snapshot，调用方不能自行声明）；
- 撤回 / 画像删除 / 账号注销路径同步撤销全部授权并立即失效 active 投影；
- 重复事件与并发授予幂等；重新授予生成新 grant，不复活旧撤回记录；
- 授权快照版本变化（过期/重授）后，旧 snapshot 引用一律拒绝；
- 生产者失败不影响原 consent 状态，进入安全日志与 outbox 重试。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.services.ai.memory.consent_producers import (
    CONSENT_PRODUCER_DIMENSIONS,
    PRODUCER_CONSENT_SCOPE,
    PRODUCER_RETRY_EVENT_TYPE,
    enqueue_projection_producer_retry,
    grant_projection_dimensions_for_consent,
    handle_projection_producer_retry,
    revoke_projection_dimensions_for_owner,
)
from app.services.ai.memory.projections import (
    MemoryProjectionService,
    ProjectionGrantDenied,
    derive_consent_snapshot_id,
)
from tests.test_ai_memory_projections import (
    FakeProjectionSession,
    ProjectionStore,
    _MappingResult,
    seed_claim,
)

pytestmark = pytest.mark.asyncio

OWNER_ID = 42
POLICY_REVISION = "ai-policy-2026-08-07-v1"

CONSENT_ROW = {
    "scope": PRODUCER_CONSENT_SCOPE,
    "version": "profile-text-v1",
    "policy_revision": POLICY_REVISION,
    "granted_at": "2026-09-06T08:00:00",
}


def _make(
    *, with_consent: bool = True
) -> tuple[FakeProjectionSession, ProjectionStore]:
    store = ProjectionStore()
    if with_consent:
        store.consents[OWNER_ID] = dict(CONSENT_ROW)
    return FakeProjectionSession(store), store


def _active_dimension_keys(store: ProjectionStore) -> set[tuple[str, str, str]]:
    return {
        (str(row["function_key"]), str(row["purpose"]), str(row["data_category"]))
        for row in store.grants.values()
        if row["status"] == "active"
    }


# ---------------------------------------------------------------------------
# 授予
# ---------------------------------------------------------------------------


async def test_grant_producer_creates_all_consumer_dimensions() -> None:
    session, store = _make()
    granted = await grant_projection_dimensions_for_consent(
        session, owner_user_id=OWNER_ID, scope=PRODUCER_CONSENT_SCOPE,
        consent_row=CONSENT_ROW,
    )
    assert granted == len(CONSENT_PRODUCER_DIMENSIONS)
    assert _active_dimension_keys(store) == {
        (d["function_key"], d["purpose"], d["data_category"])
        for d in CONSENT_PRODUCER_DIMENSIONS
    }
    snapshot_id = derive_consent_snapshot_id(CONSENT_ROW)
    for row in store.grants.values():
        assert row["consent_snapshot_id"] == snapshot_id
        assert row["policy_revision"] == POLICY_REVISION


async def test_grant_producer_is_idempotent_on_replay() -> None:
    session, store = _make()
    first = await grant_projection_dimensions_for_consent(
        session, owner_user_id=OWNER_ID, scope=PRODUCER_CONSENT_SCOPE,
        consent_row=CONSENT_ROW,
    )
    second = await grant_projection_dimensions_for_consent(
        session, owner_user_id=OWNER_ID, scope=PRODUCER_CONSENT_SCOPE,
        consent_row=CONSENT_ROW,
    )
    assert first == second == len(CONSENT_PRODUCER_DIMENSIONS)
    assert len(store.grants) == len(CONSENT_PRODUCER_DIMENSIONS)


async def test_grant_producer_without_active_consent_fails_closed() -> None:
    session, store = _make(with_consent=False)
    with pytest.raises(ProjectionGrantDenied):
        await grant_projection_dimensions_for_consent(
            session, owner_user_id=OWNER_ID, scope=PRODUCER_CONSENT_SCOPE,
            consent_row=CONSENT_ROW,
        )
    assert not store.grants


async def test_grant_producer_uses_current_database_snapshot() -> None:
    """producer 以数据库当前 consent 为准，避免调用方 datetime 精度漂移。"""

    session, store = _make()
    stale_row = dict(CONSENT_ROW, granted_at="2026-01-01T00:00:00")
    granted = await grant_projection_dimensions_for_consent(
        session, owner_user_id=OWNER_ID, scope=PRODUCER_CONSENT_SCOPE,
        consent_row=stale_row,
    )
    assert granted == len(CONSENT_PRODUCER_DIMENSIONS)
    assert store.grants


async def test_grant_producer_noop_for_other_scopes() -> None:
    session, store = _make()
    granted = await grant_projection_dimensions_for_consent(
        session, owner_user_id=OWNER_ID, scope="search_parse",
        consent_row=dict(CONSENT_ROW, scope="search_parse"),
    )
    assert granted == 0
    assert not store.grants


async def test_concurrent_grant_producer_calls_converge() -> None:
    session, store = _make()
    await __import__("asyncio").gather(
        *[
            grant_projection_dimensions_for_consent(
                session, owner_user_id=OWNER_ID, scope=PRODUCER_CONSENT_SCOPE,
                consent_row=CONSENT_ROW,
            )
            for _ in range(3)
        ]
    )
    assert len(store.grants) == len(CONSENT_PRODUCER_DIMENSIONS)
    assert all(row["status"] == "active" for row in store.grants.values())


# ---------------------------------------------------------------------------
# 撤回 / 重授
# ---------------------------------------------------------------------------


async def test_revoke_producer_invalidates_all_dimensions_immediately() -> None:
    session, store = _make()
    await grant_projection_dimensions_for_consent(
        session, owner_user_id=OWNER_ID, scope=PRODUCER_CONSENT_SCOPE,
        consent_row=CONSENT_ROW,
    )
    seed_claim(store, "c_ok", value="公开事实")
    build_service = MemoryProjectionService(session, policy_revision=POLICY_REVISION)
    for dimension in CONSENT_PRODUCER_DIMENSIONS:
        await build_service.build(
            owner_user_id=OWNER_ID,
            function_key=dimension["function_key"],
            purpose=dimension["purpose"],
            data_category=dimension["data_category"],
        )
    active_before = sum(
        1
        for rows in store.projections.values()
        for row in rows
        if row["status"] == "active"
    )
    assert active_before == len(CONSENT_PRODUCER_DIMENSIONS)

    revoked = await revoke_projection_dimensions_for_owner(
        session, owner_user_id=OWNER_ID
    )
    assert revoked == len(CONSENT_PRODUCER_DIMENSIONS)
    assert all(row["status"] == "revoked" for row in store.grants.values())
    assert not _active_dimension_keys(store)
    assert all(
        row["status"] == "invalidated"
        for rows in store.projections.values()
        for row in rows
    )
    # 撤回后读取立即 fail closed。
    read_service = MemoryProjectionService(session, policy_revision=POLICY_REVISION)
    assert (
        await read_service.read_active(
            owner_user_id=OWNER_ID,
            function_key="search",
            purpose="candidate_filter",
            data_category="personal_profile",
        )
        is None
    )


async def test_revoke_producer_is_idempotent() -> None:
    session, store = _make()
    assert (
        await revoke_projection_dimensions_for_owner(session, owner_user_id=OWNER_ID)
        == 0
    )
    await grant_projection_dimensions_for_consent(
        session, owner_user_id=OWNER_ID, scope=PRODUCER_CONSENT_SCOPE,
        consent_row=CONSENT_ROW,
    )
    await revoke_projection_dimensions_for_owner(session, owner_user_id=OWNER_ID)
    assert (
        await revoke_projection_dimensions_for_owner(session, owner_user_id=OWNER_ID)
        == 0
    )


async def test_regrant_after_revoke_uses_new_snapshot_not_reviving_old() -> None:
    session, store = _make()
    await grant_projection_dimensions_for_consent(
        session, owner_user_id=OWNER_ID, scope=PRODUCER_CONSENT_SCOPE,
        consent_row=CONSENT_ROW,
    )
    await revoke_projection_dimensions_for_owner(session, owner_user_id=OWNER_ID)

    new_row = dict(CONSENT_ROW, granted_at="2026-09-06T09:30:00")
    store.consents[OWNER_ID] = dict(new_row)
    await grant_projection_dimensions_for_consent(
        session, owner_user_id=OWNER_ID, scope=PRODUCER_CONSENT_SCOPE,
        consent_row=new_row,
    )
    new_snapshot = derive_consent_snapshot_id(new_row)
    old_snapshot = derive_consent_snapshot_id(CONSENT_ROW)
    assert new_snapshot != old_snapshot
    assert all(
        row["status"] == "active" and row["consent_snapshot_id"] == new_snapshot
        for row in store.grants.values()
    )
    # 旧投影保持 invalidated（不复活旧撤回记录），读取用新 snapshot 门。
    service = MemoryProjectionService(session, policy_revision=POLICY_REVISION)
    seed_claim(store, "c_ok", value="重授后的公开事实")
    built = await service.build(
        owner_user_id=OWNER_ID,
        function_key="search",
        purpose="candidate_filter",
        data_category="personal_profile",
    )
    assert built["consent_snapshot_id"] == new_snapshot
    assert (
        await service.read_active(
            owner_user_id=OWNER_ID,
            function_key="search",
            purpose="candidate_filter",
            data_category="personal_profile",
        )
        is not None
    )


# ---------------------------------------------------------------------------
# outbox 重试
# ---------------------------------------------------------------------------


async def test_retry_enqueue_writes_minimal_payload() -> None:
    session, store = _make()
    await enqueue_projection_producer_retry(
        session,
        owner_user_id=OWNER_ID,
        scope=PRODUCER_CONSENT_SCOPE,
        action="grant",
        snapshot_id="cs_abc",
        revision={"privacy": 3},
    )
    assert len(store.outbox) == 1
    row = next(iter(store.outbox.values()))
    assert row["event_type"] == PRODUCER_RETRY_EVENT_TYPE
    assert len(str(row["event_id"])) <= 64
    payload = json.loads(str(row["payload_minimal"]))
    assert payload["action"] == "grant"
    assert payload["scope"] == PRODUCER_CONSENT_SCOPE
    # payload 只含动作与 scope，不携带用户内容。
    assert set(payload) <= {"action", "scope", "snapshot_id"}


async def test_retry_handler_reapplies_grant_and_is_idempotent() -> None:
    session, store = _make()
    await enqueue_projection_producer_retry(
        session,
        owner_user_id=OWNER_ID,
        scope=PRODUCER_CONSENT_SCOPE,
        action="grant",
        snapshot_id=derive_consent_snapshot_id(CONSENT_ROW),
        revision={"privacy": 1},
    )
    event = _event_from_row(next(iter(store.outbox.values())))
    assert await handle_projection_producer_retry(session, event) == "processed"
    assert (
        await handle_projection_producer_retry(session, event) == "processed"
    )
    assert _active_dimension_keys(store) == {
        (d["function_key"], d["purpose"], d["data_category"])
        for d in CONSENT_PRODUCER_DIMENSIONS
    }


async def test_retry_handler_revoke_and_missing_consent_noop() -> None:
    session, store = _make()
    await grant_projection_dimensions_for_consent(
        session, owner_user_id=OWNER_ID, scope=PRODUCER_CONSENT_SCOPE,
        consent_row=CONSENT_ROW,
    )
    revoke_event = _event_from_row(
        {
            "event_id": "prjp-revoke",
            "aggregate_id": OWNER_ID,
            "event_type": PRODUCER_RETRY_EVENT_TYPE,
            "payload_minimal": json.dumps({"action": "revoke", "scope": PRODUCER_CONSENT_SCOPE}),
        }
    )
    assert await handle_projection_producer_retry(session, revoke_event) == "processed"
    assert not _active_dimension_keys(store)

    # grant 重试但 consent 已不存在：noop，不创建授权。
    session2, store2 = _make(with_consent=False)
    grant_event = _event_from_row(
        {
            "event_id": "prjp-grant",
            "aggregate_id": OWNER_ID,
            "event_type": PRODUCER_RETRY_EVENT_TYPE,
            "payload_minimal": json.dumps({"action": "grant", "scope": PRODUCER_CONSENT_SCOPE}),
        }
    )
    assert await handle_projection_producer_retry(session2, grant_event) == "noop"
    assert not store2.grants


async def test_retry_handler_ignores_foreign_events() -> None:
    session, store = _make()
    event = _event_from_row(
        {
            "event_id": "other",
            "aggregate_id": OWNER_ID,
            "event_type": "memory_claim",
            "payload_minimal": json.dumps({"action": "grant", "scope": PRODUCER_CONSENT_SCOPE}),
        }
    )
    assert await handle_projection_producer_retry(session, event) == "noop"
    assert not store.grants


def _event_from_row(row: dict[str, Any]) -> Any:
    from app.services.derivation_outbox import DerivationEvent
    from app.services.revisions import RevisionVector

    source_revision = row.get("source_revision_json") or "{}"
    payload = row.get("payload_minimal")
    if isinstance(payload, str):
        payload = json.loads(payload)
    return DerivationEvent(
        event_id=str(row["event_id"]),
        aggregate_type=str(row.get("aggregate_type") or "ai_memory"),
        aggregate_id=int(row["aggregate_id"]),
        event_type=str(row["event_type"]),
        changed_fields=(),
        source_revision=RevisionVector(**json.loads(source_revision)),
        occurred_at="2026-09-06T08:00:00",
        priority=50,
        payload=payload,
    )


# ---------------------------------------------------------------------------
# 注销链：user_deleted / account_deleted 先撤投影授权再走既有清理
# ---------------------------------------------------------------------------


async def test_account_deleted_handler_chains_producer_revoke(monkeypatch) -> None:
    from app.services.derivation_outbox import CLEANUP_HANDLERS
    from app.services.ai.memory import consent_producers

    session, store = _make()
    await grant_projection_dimensions_for_consent(
        session, owner_user_id=OWNER_ID, scope=PRODUCER_CONSENT_SCOPE,
        consent_row=CONSENT_ROW,
    )
    calls: list[str] = []

    async def fake_legacy(db, event) -> str:
        calls.append("legacy")
        return "processed"

    monkeypatch.setattr(
        consent_producers, "_LEGACY_ACCOUNT_DELETED_HANDLERS",
        {"user_deleted": fake_legacy, "account_deleted": fake_legacy},
    )
    handler = CLEANUP_HANDLERS["user_deleted"]
    event = _event_from_row(
        {
            "event_id": "del-1",
            "aggregate_id": OWNER_ID,
            "event_type": "user_deleted",
            "payload_minimal": None,
        }
    )
    await handler(session, event)
    assert calls == ["legacy"]
    assert not _active_dimension_keys(store)


# ---------------------------------------------------------------------------
# consent 服务接缝：grant_consent / revoke_consent 真实走生产者
# ---------------------------------------------------------------------------


class _WriteResultLocal:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _FlowMappingResult:
    """consent 流专用结果：revisions.py 需要 mappings().one()。"""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> "_FlowMappingResult":
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def one(self) -> dict[str, Any]:
        return self._rows[0]


class ConsentFlowSession(FakeProjectionSession):
    """在内存投影假库之上补齐 consent 服务流的 SQL 路由。"""

    async def execute(self, statement: object, params: dict[str, Any] | None = None) -> Any:
        sql = str(statement)
        values = dict(params or {})
        self.calls.append((sql, values))
        if "INSERT INTO user_revision_state" in sql:
            return _WriteResultLocal(rowcount=1)
        if "FROM user_revision_state" in sql:
            return _FlowMappingResult(
                [
                    {
                        "profile_revision": 0,
                        "preference_revision": 0,
                        "privacy_revision": int(
                            getattr(self.store, "privacy_revision", 0)
                        ),
                        "relationship_revision": 0,
                        "policy_revision": 0,
                    }
                ]
            )
        if "FROM ai_consent_operation" in sql:
            return _MappingResult([])
        if "FROM ai_task" in sql:
            task_id = values.get("task_id")
            row = getattr(self.store, "ai_tasks", {}).get(str(task_id))
            return _FlowMappingResult([dict(row)] if row else [])
        if sql.startswith("INSERT INTO ai_task"):
            tasks = getattr(self.store, "ai_tasks", None)
            if tasks is None:
                tasks = self.store.ai_tasks = {}
            # 补齐 INSERT 语句中 SQL 字面量/默认列（fake 无时钟用固定值）。
            tasks[str(values["task_id"])] = {
                **dict(values),
                "id": 1,
                "status": "queued",
                "attempt_count": 0,
                "stage": None,
                "progress_percent": None,
                "next_run_at": None,
                "lease_owner": None,
                "lease_until": None,
                "consent_snapshot_json": None,
                "source_revision_json": None,
                "payload_summary": None,
                "error_code": None,
                "error_message": None,
                "result_ref": None,
                "created_at": "2026-09-06T08:00:00",
                "updated_at": "2026-09-06T08:00:00",
                "started_at": None,
                "finished_at": None,
            }
            return _WriteResultLocal(rowcount=1)
        if "INSERT INTO ai_consent_grant" in sql:
            self.store.consents[int(values["user_id"])] = {
                "scope": str(values["scope"]),
                "version": str(values["version"]),
                "policy_revision": str(values["policy_revision"]),
                "granted_at": values["granted_at"],
            }
            return _WriteResultLocal(rowcount=1)
        if "UPDATE ai_consent_grant" in sql:
            row = self.store.consents.get(int(values["user_id"]))
            if row is not None and row.get("revoked_at") is None:
                row["revoked_at"] = "FAKE-UTC"
                return _WriteResultLocal(rowcount=1)
            return _WriteResultLocal(rowcount=0)
        # consent 流尾部语句（操作记录 / 撤权失效 / 清理任务）：内存假库一律
        # 按成功写入处理，与真实 DB 语义一致（语句本身均为幂等标记/插入）。
        for prefix in (
            "INSERT INTO ai_consent_operation",
            "INSERT INTO ai_task",
            "UPDATE ai_task",
            "UPDATE ai_profile_draft",
            "UPDATE ai_profile_session",
            "UPDATE ai_feature_projection",
            "UPDATE ai_search_draft",
            "UPDATE ai_search_snapshot",
            "UPDATE ai_search_result",
            "UPDATE ai_compatibility_snapshot",
        ):
            if sql.startswith(prefix):
                return _WriteResultLocal(rowcount=1)
        return self._route(sql, values)


def _grant_body() -> Any:
    from app.schemas.ai_common import AiConsentGrantRequest

    return AiConsentGrantRequest(
        consent_version="profile-text-v1",
        policy_revision=POLICY_REVISION,
    )


async def test_consent_grant_end_to_end_produces_all_grants() -> None:
    from app.services.ai.consents import grant_consent

    store = ProjectionStore()
    session = ConsentFlowSession(store)
    response = await grant_consent(
        session, OWNER_ID, "profile_text_extract", _grant_body(), "idem-1", 0
    )
    assert response.status == "active"
    assert _active_dimension_keys(store) == {
        (d["function_key"], d["purpose"], d["data_category"])
        for d in CONSENT_PRODUCER_DIMENSIONS
    }


async def test_consent_grant_producer_failure_keeps_consent_and_enqueues_retry(
    monkeypatch,
) -> None:
    from app.services.ai.memory import consent_producers as producers_mod
    from app.services.ai.consents import grant_consent

    async def failing_grant(db, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        producers_mod, "grant_projection_dimensions_for_consent", failing_grant
    )
    store = ProjectionStore()
    session = ConsentFlowSession(store)
    response = await grant_consent(
        session, OWNER_ID, "profile_text_extract", _grant_body(), "idem-1", 0
    )
    # consent 主流程不受 producer 失败影响。
    assert response.status == "active"
    assert not store.grants
    # 重试事件已入 outbox（payload 只含动作与 scope）。
    retry_rows = [
        row
        for row in store.outbox.values()
        if row["event_type"] == PRODUCER_RETRY_EVENT_TYPE
    ]
    assert len(retry_rows) == 1
    payload = json.loads(str(retry_rows[0]["payload_minimal"]))
    assert payload["action"] == "grant"
    assert payload["scope"] == PRODUCER_CONSENT_SCOPE


async def test_consent_revoke_end_to_end_revokes_all_grants() -> None:
    from app.services.ai.consents import grant_consent, revoke_consent

    store = ProjectionStore()
    session = ConsentFlowSession(store)
    await grant_consent(
        session, OWNER_ID, "profile_text_extract", _grant_body(), "idem-1", 0
    )
    assert len(_active_dimension_keys(store)) == len(CONSENT_PRODUCER_DIMENSIONS)
    response = await revoke_consent(
        session, OWNER_ID, "profile_text_extract", "idem-2", 0
    )
    assert response.status == "revoked"
    assert not _active_dimension_keys(store)
    assert all(row["status"] == "revoked" for row in store.grants.values())


async def test_consent_revoke_other_scope_leaves_grants_untouched() -> None:
    from app.services.ai.consents import grant_consent, revoke_consent

    store = ProjectionStore()
    session = ConsentFlowSession(store)
    await grant_consent(
        session, OWNER_ID, "profile_text_extract", _grant_body(), "idem-1", 0
    )
    await revoke_consent(session, OWNER_ID, "search_parse", "idem-2", 0)
    assert len(_active_dimension_keys(store)) == len(CONSENT_PRODUCER_DIMENSIONS)

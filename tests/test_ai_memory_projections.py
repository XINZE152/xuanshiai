"""MemoryProjectionService grant/revoke/read tests (Task 3).

The service must re-read the ``profile_text_extract`` consent server-side on
every grant and read, bind the current policy revision, keep grants idempotent
per (owner, function, purpose, category), revoke owner-scoped with immediate
invalidation of every active projection in the dimension, and return ``None``
(no existence leak) on any failed read gate.  Future function keys fail
closed.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.schemas.ai_memory_projection import MEMORY_PROJECTION_ENTRY_ALLOWLIST
from app.services.ai.memory.projection_policy import (
    ProjectionFeatureNotEnabled,
    ProjectionPolicyDenied,
)
from app.services.ai.memory.projections import (
    ProjectionGrantDenied,
    ProjectionGrantNotFound,
    MemoryProjectionService,
    derive_consent_snapshot_id,
)

pytestmark = pytest.mark.asyncio

OWNER_ID = 42
OTHER_ID = 43
POLICY_REVISION = "ai-policy-2026-08-07-v1"
GRANT_KWARGS = {
    "owner_user_id": OWNER_ID,
    "function_key": "search",
    "purpose": "candidate_filter",
    "data_category": "personal_profile",
}


# ---------------------------------------------------------------------------
# 假会话：按 SQL 子串路由（与仓库既有 fake 模式一致）
# ---------------------------------------------------------------------------


class ProjectionStore:
    def __init__(self) -> None:
        self.grants: dict[tuple[int, str, str, str], dict[str, Any]] = {}
        self.projections: dict[tuple[int, str, str, str], list[dict[str, Any]]] = {}
        self.consents: dict[int, dict[str, Any] | None] = {}
        self.claims: list[dict[str, Any]] = []
        self.events: dict[str, dict[str, Any]] = {}
        self.outbox: dict[str, dict[str, Any]] = {}
        self.suppressions: list[dict[str, Any]] = []
        self.owner_sequences: dict[int, int] = {}

    def snapshot(self) -> dict[str, Any]:
        return {
            "grants": {k: dict(v) for k, v in self.grants.items()},
            "projections": {
                k: [dict(row) for row in v]
                for k, v in self.projections.items()
            },
            "consents": {k: dict(v) if v else None for k, v in self.consents.items()},
            "claims": [dict(row) for row in self.claims],
            "events": {k: dict(v) for k, v in self.events.items()},
            "outbox": {k: dict(v) for k, v in self.outbox.items()},
        }

    def restore(self, snap: dict[str, Any]) -> None:
        self.grants = {k: dict(v) for k, v in snap["grants"].items()}
        self.projections = {
            k: [dict(row) for row in v] for k, v in snap["projections"].items()
        }
        self.consents = {
            k: dict(v) if v else None for k, v in snap["consents"].items()
        }
        self.claims = [dict(row) for row in snap["claims"]]
        self.events = {k: dict(v) for k, v in snap["events"].items()}
        self.outbox = {k: dict(v) for k, v in snap["outbox"].items()}


class FakeProjectionSession:
    def __init__(self, store: ProjectionStore) -> None:
        self.store = store
        self.commits = 0
        self.rollbacks = 0
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._snapshot: dict[str, Any] | None = None

    async def commit(self) -> None:
        self.commits += 1
        self._snapshot = self.store.snapshot()

    async def flush(self) -> None:
        return None

    async def rollback(self) -> None:
        self.rollbacks += 1
        if self._snapshot is not None:
            self.store.restore(self._snapshot)

    async def execute(
        self, statement: object, params: dict[str, Any] | list[dict[str, Any]] | None = None
    ) -> Any:
        sql = str(statement)
        if isinstance(params, list):
            # Task 15：executemany——逐行分派同一 SQL，合并 rowcount。
            rowcount = 0
            for single in params:
                result = await self.execute(statement, single)
                rowcount += getattr(result, "rowcount", 1)
            return _WriteResult(rowcount=rowcount)
        values = dict(params or {})
        self.calls.append((sql, values))
        return self._route(sql, values)

    def _route(self, sql: str, v: dict[str, Any]) -> Any:
        store = self.store
        if "INSERT INTO ai_memory_owner_sequence" in sql:
            store.owner_sequences.setdefault(int(v["owner_user_id"]), 1)
            return _WriteResult(rowcount=1)
        if "FROM ai_memory_owner_sequence" in sql and "FOR UPDATE" in sql:
            nxt = store.owner_sequences.get(int(v["owner_user_id"]))
            return _MappingResult([{"next_seq": nxt}] if nxt is not None else [])
        if "FROM ai_consent_grant" in sql:
            if "user_id IN (" in sql:
                # read_active_batch（Task 14）：批量 consent 读取。
                owner_ids = {
                    int(value)
                    for key, value in v.items()
                    if key.startswith("owner")
                }
                rows = [
                    dict(consent, user_id=uid)
                    for uid, consent in store.consents.items()
                    if uid in owner_ids and consent is not None
                ]
                return _MappingResult(rows)
            consent = store.consents.get(int(v["user_id"]))
            rows = [consent] if consent is not None else []
            return _MappingResult(rows)
        if "FROM ai_memory_event" in sql and "event_id = :event_id" in sql:
            row = store.events.get(str(v["event_id"]))
            return _MappingResult([dict(row)] if row else [])
        if "FROM ai_memory_claim" in sql:
            import re as _re

            match = _re.search(r"subject IN \(([^)]*)\)", sql)
            subjects = tuple(
                item.strip().strip("'\"") for item in match.group(1).split(",")
            )
            rows = [
                dict(row)
                for row in store.claims
                if row["owner_user_id"] == int(v["owner_user_id"])
                and row["subject"] in subjects
                and ("status = 'confirmed'" not in sql or row["status"] == "confirmed")
            ]
            return _MappingResult(rows)
        if "FROM ai_memory_suppression" in sql:
            rows = [
                dict(row)
                for row in store.suppressions
                if row["owner_user_id"] == int(v["owner_user_id"])
                and row["status"] == "active"
            ]
            return _MappingResult(rows)
        if "INSERT INTO ai_memory_projection (" in sql:
            key = (
                int(v["owner_user_id"]),
                str(v["function_key"]),
                str(v["purpose"]),
                str(v["data_category"]),
            )
            row = dict(v, status="active", invalidated_at=None, invalidated_reason=None)
            store.projections.setdefault(key, []).append(row)
            return _WriteResult(rowcount=1)
        if "SELECT COALESCE(MAX(projection_version)" in sql:
            key = (
                int(v["owner_user_id"]),
                str(v["function_key"]),
                str(v["purpose"]),
                str(v["data_category"]),
            )
            versions = [
                int(row["projection_version"])
                for row in store.projections.get(key, [])
            ]
            return _MappingResult([{"max_version": max(versions) if versions else 0}])
        if "INSERT INTO derivation_outbox" in sql:
            event_id = str(v["event_id"])
            if event_id in store.outbox:
                return _WriteResult(rowcount=0)
            values = dict(v)
            if "'ai_memory'" in sql:
                values["aggregate_type"] = "ai_memory"
            values["published_at"] = "FAKE-UTC"
            store.outbox[event_id] = values
            return _WriteResult(rowcount=1)
        if "INSERT INTO ai_memory_projection_grant" in sql:
            key = (
                int(v["owner_user_id"]),
                str(v["function_key"]),
                str(v["purpose"]),
                str(v["data_category"]),
            )
            existing = store.grants.get(key)
            if existing is None:
                store.grants[key] = dict(v, status="active")
                return _WriteResult(rowcount=1)
            # ON DUPLICATE KEY UPDATE：granted_at/revoked_at 由 SQL 字面量
            # （UTC_TIMESTAMP()/NULL）赋值，fake 无时钟，保留原值即可。
            existing.update(dict(v, status="active", revoked_at=None))
            return _WriteResult(rowcount=2)
        if (
            "FROM ai_memory_projection_grant" in sql
            and "function_key = :function_key" not in sql
        ):
            # rebuild 扫描：owner 全部 active 授权（无 function_key 参数）。
            rows = [
                dict(row)
                for row in store.grants.values()
                if row["owner_user_id"] == int(v["owner_user_id"])
                and row["status"] == "active"
            ]
            return _MappingResult(rows)
        if (
            "FROM ai_memory_projection_grant" in sql
            and "owner_user_id IN (" in sql
        ):
            # read_active_batch（Task 14）：批量 grant 读取（含非 active，
            # 由服务端按 status 过滤）。
            owner_ids = {
                int(value) for key, value in v.items() if key.startswith("owner")
            }
            rows = [
                dict(row)
                for row in store.grants.values()
                if row["owner_user_id"] in owner_ids
                and row["function_key"] == str(v["function_key"])
                and row["purpose"] == str(v["purpose"])
                and row["data_category"] == str(v["data_category"])
            ]
            return _MappingResult(rows)
        if "FROM ai_memory_projection_grant" in sql:
            key = (
                int(v["owner_user_id"]),
                str(v["function_key"]),
                str(v["purpose"]),
                str(v["data_category"]),
            )
            row = store.grants.get(key)
            return _MappingResult([dict(row)] if row else [])
        if "UPDATE ai_memory_projection_grant" in sql:
            # 真实语句按 grant_id 定位；status/revoked_at 由 SQL 字面量赋值。
            target = str(v["grant_id"])
            for row in store.grants.values():
                if str(row.get("grant_id")) == target and row["status"] == "active":
                    row["status"] = "revoked"
                    row["revoked_at"] = "FAKE-UTC"
                    return _WriteResult(rowcount=1)
            return _WriteResult(rowcount=0)
        if (
            "SELECT DISTINCT owner_user_id AS user_id FROM ai_memory_projection"
            in sql
        ):
            # 候选池发现：有 active 投影的其他用户（排除 viewer，截断）。
            seen: list[int] = []
            for (owner, _f, _p, _c), rows in store.projections.items():
                if owner == int(v["viewer"]) or owner in seen:
                    continue
                if not any(row["status"] == "active" for row in rows):
                    continue
                seen.append(owner)
            return _MappingResult([{"user_id": uid} for uid in seen[: int(v["limit"])]])
        if (
            "FROM ai_memory_projection WHERE" in sql
            and "owner_user_id IN (" in sql
            and "function_key = :function_key" in sql
        ):
            # read_active_batch（Task 14）：按 owner IN 列表批量读 active 投影。
            owner_ids = {
                int(value) for key, value in v.items() if key.startswith("owner")
            }
            rows = [
                dict(row)
                for rows in store.projections.values()
                for row in rows
                if row["owner_user_id"] in owner_ids
                and row["status"] == "active"
                and row["function_key"] == str(v["function_key"])
                and row["purpose"] == str(v["purpose"])
                and row["data_category"] == str(v["data_category"])
            ]
            return _MappingResult(rows)
        if (
            "FROM ai_memory_projection WHERE" in sql
            and "function_key = :function_key" not in sql
        ):
            # invalidate_for_claim 扫描：owner 全部 active 投影（无维度参数）。
            rows = [
                dict(row)
                for rows in store.projections.values()
                for row in rows
                if row["owner_user_id"] == int(v["owner_user_id"])
                and row["status"] == "active"
            ]
            return _MappingResult(rows)
        if (
            "FROM ai_memory_projection WHERE" in sql
            and "projection_input_hash = :input_hash" in sql
        ):
            key = (
                int(v["owner_user_id"]),
                str(v["function_key"]),
                str(v["purpose"]),
                str(v["data_category"]),
            )
            matches = [
                dict(row)
                for row in store.projections.get(key, [])
                if row["projection_input_hash"] == v["input_hash"]
            ]
            matches.sort(key=lambda row: int(row["projection_version"]), reverse=True)
            return _MappingResult([matches[0]] if matches else [])
        if "UPDATE ai_memory_projection SET status = 'active'" in sql:
            for rows in store.projections.values():
                for row in rows:
                    if str(row["projection_id"]) == str(v["projection_id"]) and row[
                        "status"
                    ] == "invalidated":
                        row["status"] = "active"
                        row["invalidated_at"] = None
                        row["invalidated_reason"] = None
                        return _WriteResult(rowcount=1)
            return _WriteResult(rowcount=0)
        if "FROM ai_memory_projection" in sql:
            key = (
                int(v["owner_user_id"]),
                str(v["function_key"]),
                str(v["purpose"]),
                str(v["data_category"]),
            )
            rows = sorted(
                store.projections.get(key, []),
                key=lambda row: int(row["projection_version"]),
                reverse=True,
            )
            pinned = v.get("projection_version")
            if pinned is not None:
                rows = [row for row in rows if int(row["projection_version"]) == int(pinned)]
            rows = [row for row in rows if row["status"] == "active"]
            return _MappingResult([dict(rows[0])] if rows else [])
        if "UPDATE ai_memory_projection SET status = 'invalidated'" in sql:
            if "WHERE projection_id = :projection_id" in sql:
                # invalidate_for_claim：按 projection_id 精确失效。
                for rows in store.projections.values():
                    for row in rows:
                        if str(row["projection_id"]) == str(
                            v["projection_id"]
                        ) and row["status"] == "active":
                            row.update(
                                {
                                    "status": "invalidated",
                                    "invalidated_at": "FAKE-UTC",
                                    "invalidated_reason": v["invalidated_reason"],
                                }
                            )
                            return _WriteResult(rowcount=1)
                return _WriteResult(rowcount=0)
            key = (
                int(v["owner_user_id"]),
                str(v["function_key"]),
                str(v["purpose"]),
                str(v["data_category"]),
            )
            # supersede 语句带 projection_version < :projection_version 上限；
            # revoke 失效语句无版本参数，作用于全部 active 行。
            version_ceiling = v.get("projection_version")
            updated = 0
            for row in store.projections.get(key, []):
                if row["status"] != "active":
                    continue
                if version_ceiling is not None and int(
                    row["projection_version"]
                ) >= int(version_ceiling):
                    continue
                # invalidated_at 由 SQL 字面量 UTC_TIMESTAMP() 赋值，
                # fake 无时钟，用固定标记。
                row.update(
                    {
                        "status": "invalidated",
                        "invalidated_at": "FAKE-UTC",
                        "invalidated_reason": v["invalidated_reason"],
                    }
                )
                updated += 1
            return _WriteResult(rowcount=updated)
        raise AssertionError(f"unrouted SQL: {sql[:120]}")


class _MappingResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> "_MappingResult":
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)


class _WriteResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


def make_service(
    *, with_consent: bool = True, policy_revision: str = POLICY_REVISION
) -> tuple[MemoryProjectionService, FakeProjectionSession, ProjectionStore]:
    store = ProjectionStore()
    if with_consent:
        store.consents[OWNER_ID] = {
            "scope": "profile_text_extract",
            "version": "profile_text_extract-v3",
            "policy_revision": policy_revision,
            "granted_at": "2026-09-05T08:00:00",
        }
    session = FakeProjectionSession(store)
    return (
        MemoryProjectionService(session, policy_revision=policy_revision),
        session,
        store,
    )


def seed_projection(
    store: ProjectionStore,
    *,
    owner: int = OWNER_ID,
    function_key: str = "search",
    purpose: str = "candidate_filter",
    data_category: str = "personal_profile",
    version: int = 1,
    status: str = "active",
    snapshot_id: str | None = None,
    policy_revision: str = POLICY_REVISION,
) -> dict[str, Any]:
    from app.services.ai.memory.projections import derive_consent_snapshot_id

    key = (owner, function_key, purpose, data_category)
    row = {
        "projection_id": f"prj_{owner}_{function_key}_{version}",
        "owner_user_id": owner,
        "function_key": function_key,
        "purpose": purpose,
        "data_category": data_category,
        "subject": "personal",
        "projection_version": version,
        "projection_input_hash": "a" * 64,
        "status": status,
        "invalidated_at": None,
        "invalidated_reason": None,
        "entries_json": json.dumps(
            [
                {
                    "field_key": "interest_tags",
                    "value": ["hiking"],
                    "value_type": "string_list",
                    "source_kind": "user_confirmed",
                    "claim_id": "clm_1",
                    "stability": 0.85,
                    "importance": 0.5,
                    "constraint_type": None,
                    "projection_version": version,
                    "evidence_ref": "memory:claim:clm_1",
                }
            ]
        ),
        "policy_revision": policy_revision,
        "consent_snapshot_id": snapshot_id or "cs_STALE",
        "built_at": "2026-09-05T08:00:00",
    }
    store.projections.setdefault(key, []).append(row)
    return row


# ---------------------------------------------------------------------------
# grant：服务端重读授权 + 幂等
# ---------------------------------------------------------------------------


async def test_grant_requires_active_consent() -> None:
    service, _, _ = make_service(with_consent=False)
    with pytest.raises(ProjectionGrantDenied):
        await service.grant(**GRANT_KWARGS, consent_snapshot_id="cs_x", policy_revision=POLICY_REVISION)


async def test_grant_rejects_stale_snapshot_reference() -> None:
    service, _, _ = make_service()
    with pytest.raises(ProjectionGrantDenied):
        await service.grant(
            **GRANT_KWARGS,
            consent_snapshot_id="cs_not_the_current_one",
            policy_revision=POLICY_REVISION,
        )


async def test_grant_accepts_current_snapshot_reference() -> None:
    service, _, store = make_service()
    consent = store.consents[OWNER_ID]
    snapshot_id = derive_consent_snapshot_id(consent)
    row = await service.grant(
        **GRANT_KWARGS, consent_snapshot_id=snapshot_id, policy_revision=POLICY_REVISION
    )
    assert row["status"] == "active"
    assert row["consent_snapshot_id"] == snapshot_id
    key = (OWNER_ID, "search", "candidate_filter", "personal_profile")
    assert store.grants[key]["consent_snapshot_id"] == snapshot_id


async def test_grant_policy_revision_mismatch_denied() -> None:
    service, _, store = make_service()
    consent = store.consents[OWNER_ID]
    snapshot_id = derive_consent_snapshot_id(consent)
    with pytest.raises(ProjectionGrantDenied):
        await service.grant(
            **GRANT_KWARGS,
            consent_snapshot_id=snapshot_id,
            policy_revision="ai-policy-2020-01-01-v1",
        )


async def test_duplicate_grant_is_idempotent() -> None:
    service, _, store = make_service()
    snapshot_id = derive_consent_snapshot_id(store.consents[OWNER_ID])
    first = await service.grant(
        **GRANT_KWARGS, consent_snapshot_id=snapshot_id, policy_revision=POLICY_REVISION
    )
    second = await service.grant(
        **GRANT_KWARGS, consent_snapshot_id=snapshot_id, policy_revision=POLICY_REVISION
    )
    assert first["grant_id"] == second["grant_id"]
    key = (OWNER_ID, "search", "candidate_filter", "personal_profile")
    assert len(store.grants) == 1


async def test_revoked_consent_blocks_new_grant() -> None:
    service, _, store = make_service()
    snapshot_id = derive_consent_snapshot_id(store.consents[OWNER_ID])
    store.consents[OWNER_ID] = None  # revoked / deleted
    with pytest.raises(ProjectionGrantDenied):
        await service.grant(
            **GRANT_KWARGS, consent_snapshot_id=snapshot_id, policy_revision=POLICY_REVISION
        )


async def test_consumer_function_key_grant_enabled_phase3() -> None:
    """Phase 3 启用后：counselor/persona 消费 key 可正常授予（快照绑定）。"""

    service, _, store = make_service()
    snapshot_id = derive_consent_snapshot_id(store.consents[OWNER_ID])
    await service.grant(
        owner_user_id=OWNER_ID,
        function_key="counselor_context",
        purpose="session_context",
        data_category="personal_profile",
        consent_snapshot_id=snapshot_id,
        policy_revision=POLICY_REVISION,
    )
    await service.grant(
        owner_user_id=OWNER_ID,
        function_key="persona_context",
        purpose="session_context",
        data_category="public_profile_summary",
        consent_snapshot_id=snapshot_id,
        policy_revision=POLICY_REVISION,
    )
    assert len(store.grants) == 2
    assert all(row["status"] == "active" for row in store.grants.values())


async def test_unknown_function_key_grant_denied() -> None:
    service, _, _ = make_service()
    with pytest.raises(ProjectionPolicyDenied):
        await service.grant(
            owner_user_id=OWNER_ID,
            function_key="matchmaking",
            purpose="candidate_filter",
            data_category="personal_profile",
            consent_snapshot_id="cs_x",
            policy_revision=POLICY_REVISION,
        )


# ---------------------------------------------------------------------------
# revoke：owner 隔离 + 立即失效
# ---------------------------------------------------------------------------


async def test_revoke_unknown_grant_is_owner_scoped_404() -> None:
    service, _, _ = make_service()
    with pytest.raises(ProjectionGrantNotFound):
        await service.revoke(**GRANT_KWARGS)


async def test_revoke_invalidates_all_active_projections_immediately() -> None:
    service, _, store = make_service()
    snapshot_id = derive_consent_snapshot_id(store.consents[OWNER_ID])
    await service.grant(
        **GRANT_KWARGS, consent_snapshot_id=snapshot_id, policy_revision=POLICY_REVISION
    )
    seed_projection(store, version=1)
    seed_projection(store, version=2)
    seed_projection(store, version=3, status="invalidated")

    invalidated = await service.revoke(**GRANT_KWARGS)
    assert invalidated == 2, "只失效 active 投影，不触碰已 invalidated 的历史版本"
    key = (OWNER_ID, "search", "candidate_filter", "personal_profile")
    statuses = {row["version"]: row["status"] for row in _flatten(store, key)}
    assert statuses == {1: "invalidated", 2: "invalidated", 3: "invalidated"}
    grant_row = store.grants[key]
    assert grant_row["status"] == "revoked"
    assert grant_row["revoked_at"] is not None


def _flatten(store: ProjectionStore, key) -> list[dict[str, Any]]:
    return [
        {
            "version": row["projection_version"],
            "status": row["status"],
            "invalidated_at": row["invalidated_at"],
        }
        for row in store.projections.get(key, [])
    ]


async def test_revoke_is_scoped_to_one_dimension() -> None:
    service, _, store = make_service()
    snapshot_id = derive_consent_snapshot_id(store.consents[OWNER_ID])
    await service.grant(
        **GRANT_KWARGS, consent_snapshot_id=snapshot_id, policy_revision=POLICY_REVISION
    )
    seed_projection(store)  # search/candidate_filter/personal_profile
    other = (OWNER_ID, "recommend", "candidate_rank", "personal_profile")
    seed_projection(store, function_key="recommend", purpose="candidate_rank")

    await service.revoke(**GRANT_KWARGS)
    assert store.projections[other][0]["status"] == "active"


# ---------------------------------------------------------------------------
# read_active：owner / consent / policy / status / version 全量门
# ---------------------------------------------------------------------------


async def test_read_active_returns_none_without_projection() -> None:
    service, _, _ = make_service()
    assert await service.read_active(**GRANT_KWARGS) is None


async def test_read_active_returns_active_projection_with_entries() -> None:
    service, _, store = make_service()
    snapshot_id = derive_consent_snapshot_id(store.consents[OWNER_ID])
    await service.grant(
        **GRANT_KWARGS, consent_snapshot_id=snapshot_id, policy_revision=POLICY_REVISION
    )
    seed_projection(store, snapshot_id=snapshot_id)
    result = await service.read_active(**GRANT_KWARGS)
    assert result is not None
    assert result["projection_version"] == 1
    assert result["entries"][0]["field_key"] == "interest_tags"
    assert "source_quote" not in result["entries"][0]


async def test_read_active_none_when_grant_missing_or_revoked() -> None:
    service, _, store = make_service()
    snapshot_id = derive_consent_snapshot_id(store.consents[OWNER_ID])
    seed_projection(store, snapshot_id=snapshot_id)
    # 无 grant → None
    assert await service.read_active(**GRANT_KWARGS) is None
    # revoked grant → None
    key = (OWNER_ID, "search", "candidate_filter", "personal_profile")
    store.grants[key] = {
        "grant_id": "g1",
        "owner_user_id": OWNER_ID,
        "function_key": "search",
        "purpose": "candidate_filter",
        "data_category": "personal_profile",
        "status": "revoked",
        "consent_snapshot_id": snapshot_id,
        "policy_revision": POLICY_REVISION,
        "granted_at": "2026-09-05T08:00:00",
        "revoked_at": "2026-09-05T09:00:00",
    }
    assert await service.read_active(**GRANT_KWARGS) is None


async def test_read_active_none_when_consent_revoked_after_build() -> None:
    service, _, store = make_service()
    snapshot_id = derive_consent_snapshot_id(store.consents[OWNER_ID])
    await service.grant(
        **GRANT_KWARGS, consent_snapshot_id=snapshot_id, policy_revision=POLICY_REVISION
    )
    seed_projection(store, snapshot_id=snapshot_id)
    store.consents[OWNER_ID] = None
    assert await service.read_active(**GRANT_KWARGS) is None


async def test_read_active_none_when_policy_revision_mismatch() -> None:
    service, _, store = make_service()
    snapshot_id = derive_consent_snapshot_id(store.consents[OWNER_ID])
    await service.grant(
        **GRANT_KWARGS, consent_snapshot_id=snapshot_id, policy_revision=POLICY_REVISION
    )
    seed_projection(store, snapshot_id=snapshot_id, policy_revision="ai-policy-2020-v1")
    assert await service.read_active(**GRANT_KWARGS) is None


async def test_read_active_none_for_stale_snapshot_binding() -> None:
    service, _, store = make_service()
    await service.grant(
        **GRANT_KWARGS,
        consent_snapshot_id=derive_consent_snapshot_id(store.consents[OWNER_ID]),
        policy_revision=POLICY_REVISION,
    )
    seed_projection(store, snapshot_id="cs_old_snapshot")
    assert await service.read_active(**GRANT_KWARGS) is None


async def test_read_active_none_for_owner_mismatch() -> None:
    service, _, store = make_service()
    snapshot_id = derive_consent_snapshot_id(store.consents[OWNER_ID])
    await service.grant(
        **GRANT_KWARGS, consent_snapshot_id=snapshot_id, policy_revision=POLICY_REVISION
    )
    seed_projection(store, snapshot_id=snapshot_id)
    assert (
        await service.read_active(
            owner_user_id=OTHER_ID,
            function_key="search",
            purpose="candidate_filter",
            data_category="personal_profile",
        )
        is None
    )


async def test_read_active_pinned_version() -> None:
    service, _, store = make_service()
    snapshot_id = derive_consent_snapshot_id(store.consents[OWNER_ID])
    await service.grant(
        **GRANT_KWARGS, consent_snapshot_id=snapshot_id, policy_revision=POLICY_REVISION
    )
    seed_projection(store, version=1, status="invalidated")
    seed_projection(store, version=2, snapshot_id=snapshot_id)
    result = await service.read_active(**GRANT_KWARGS, projection_version=1)
    assert result is None, "pinned 到已失效版本必须 None"
    result = await service.read_active(**GRANT_KWARGS, projection_version=2)
    assert result is not None and result["projection_version"] == 2


async def test_read_consumer_function_key_no_row_returns_none() -> None:
    """启用后的 consumer key：无授权/无投影时读取返回 None（不泄露存在性）。"""

    service, _, _ = make_service()
    assert (
        await service.read_active(
            owner_user_id=OWNER_ID,
            function_key="counselor_context",
            purpose="session_context",
            data_category="personal_profile",
        )
        is None
    )


# ---------------------------------------------------------------------------
# Task 4：build（Builder、版本、幂等）
# ---------------------------------------------------------------------------


def seed_claim(
    store: ProjectionStore,
    claim_id: str,
    *,
    owner_user_id: int = OWNER_ID,
    subject: str = "personal",
    dimension: str = "lifestyle",
    canonical_key: str | None = None,
    status: str = "confirmed",
    value: Any = "我每天早上都要喝一杯咖啡",
    stability: float = 0.85,
    importance: float = 0.5,
    constraint_type: str | None = None,
    importance_confirmed: int = 0,
    source_kind: str = "user_confirmed",
) -> dict[str, Any]:
    row = {
        "claim_id": claim_id,
        "owner_user_id": owner_user_id,
        "subject": subject,
        "namespace": "moxiang",
        "canonical_key": canonical_key or f"{subject}:{dimension}:digest_{claim_id}",
        "dimension": dimension,
        "value_json": json.dumps(value, ensure_ascii=False)
        if not isinstance(value, str)
        else value,
        "confidence": 0.9,
        "stability": stability,
        "importance": importance,
        "constraint_type": constraint_type,
        "importance_confirmed": importance_confirmed,
        "fact_kind": "about_user" if subject == "personal" else "partner_preference",
        "status": status,
        "source_kind": source_kind,
        "last_event_id": f"evt_{claim_id}",
        "last_event_seq": 7,
    }
    store.claims.append(row)
    return row


def structured_canonical(subject: str, dimension: str, field_key: str) -> str:
    from app.services.ai.memory.policy import MemoryPolicy

    identity = MemoryPolicy.candidate_identity("structured", field_key, None, None)
    return MemoryPolicy.canonical_key(subject, dimension, identity)


def entry_canonical(subject: str, dimension: str, category: str, content: str) -> str:
    from app.services.ai.candidates import compute_candidate_content_hash
    from app.services.ai.memory.policy import MemoryPolicy

    content_hash = compute_candidate_content_hash(subject, "entry", None, category, None, content)
    identity = MemoryPolicy.candidate_identity("entry", None, category, content_hash)
    return MemoryPolicy.canonical_key(subject, dimension, identity)


def make_build_service(
    *, with_consent: bool = True
) -> tuple[MemoryProjectionService, FakeProjectionSession, ProjectionStore]:
    return make_service(with_consent=with_consent)


BUILD_KWARGS = {
    "owner_user_id": OWNER_ID,
    "function_key": "search",
    "purpose": "candidate_filter",
    "data_category": "personal_profile",
}


async def test_build_uses_only_confirmed_claims() -> None:
    service, _, store = make_build_service()
    seed_claim(store, "c_ok")
    seed_claim(store, "c_proposed", status="proposed")
    seed_claim(store, "c_superseded", status="superseded")
    row = await service.build(**BUILD_KWARGS)
    claim_ids = [entry["claim_id"] for entry in row["entries"]]
    assert claim_ids == ["c_ok"]


async def test_build_personal_profile_ignores_ideal_partner_claims() -> None:
    service, _, store = make_build_service()
    seed_claim(store, "c_personal")
    seed_claim(store, "c_ideal", subject="ideal_partner")
    row = await service.build(**BUILD_KWARGS)
    assert [entry["claim_id"] for entry in row["entries"]] == ["c_personal"]


async def test_build_ideal_partner_preference_maps_subject() -> None:
    service, _, store = make_build_service()
    seed_claim(store, "c_personal")
    seed_claim(
        store,
        "c_ideal",
        subject="ideal_partner",
        dimension="relationship_boundaries",
        canonical_key=None,
        value="希望对方稳重",
    )
    row = await service.build(
        owner_user_id=OWNER_ID,
        function_key="search",
        purpose="candidate_filter",
        data_category="ideal_partner_preference",
    )
    assert row["subject"] == "ideal_partner"
    assert [entry["claim_id"] for entry in row["entries"]] == ["c_ideal"]


async def test_build_compatibility_features_merges_both_subjects() -> None:
    service, _, store = make_build_service()
    seed_claim(store, "c_personal")
    seed_claim(store, "c_ideal", subject="ideal_partner", dimension="lifestyle", value="爱运动")
    row = await service.build(
        owner_user_id=OWNER_ID,
        function_key="compatibility",
        purpose="candidate_rank",
        data_category="compatibility_features",
    )
    assert row["subject"] == "personal"
    assert {entry["claim_id"] for entry in row["entries"]} == {"c_personal", "c_ideal"}


async def test_build_entry_shape_is_minimal_and_sorted() -> None:
    service, _, store = make_build_service()
    seed_claim(store, "c_b")
    seed_claim(store, "c_a", dimension="values", value="诚实", canonical_key=None)
    row = await service.build(**BUILD_KWARGS)
    assert len(row["entries"]) == 2
    keys = [(entry["field_key"], entry["claim_id"]) for entry in row["entries"]]
    assert keys == sorted(keys)
    for entry in row["entries"]:
        assert set(entry) == set(MEMORY_PROJECTION_ENTRY_ALLOWLIST)
        assert "source_quote" not in entry
        assert entry["evidence_ref"] == f"memory:claim:{entry['claim_id']}"


async def test_build_recovers_structured_field_key() -> None:
    service, _, store = make_build_service()
    canonical = structured_canonical("personal", "lifestyle", "height_cm")
    seed_claim(
        store,
        "c_height",
        canonical_key=canonical,
        value=175,
    )
    row = await service.build(**BUILD_KWARGS)
    (entry,) = row["entries"]
    assert entry["field_key"] == "height_cm"
    assert entry["value_type"] == "number"
    assert entry["value"] == 175


async def test_build_recovers_entry_category_field_key() -> None:
    service, _, store = make_build_service()
    content = "我每天早上都要喝一杯咖啡"
    canonical = entry_canonical("personal", "lifestyle", "interests", content)
    seed_claim(store, "c_coffee", canonical_key=canonical, value=content)
    row = await service.build(**BUILD_KWARGS)
    (entry,) = row["entries"]
    assert entry["field_key"] == "interests"
    assert entry["value_type"] == "string"
    assert entry["value"] == content


async def test_build_skips_unrepresentable_structured_dict_value() -> None:
    service, _, store = make_build_service()
    canonical = structured_canonical("personal", "lifestyle", "income_band")
    seed_claim(store, "c_income", canonical_key=canonical, value={"min": 10, "max": 20})
    row = await service.build(**BUILD_KWARGS)
    assert row["entries"] == []


async def test_build_requires_consent() -> None:
    service, _, store = make_build_service(with_consent=False)
    seed_claim(store, "c_ok")
    with pytest.raises(ProjectionGrantDenied):
        await service.build(**BUILD_KWARGS)


async def test_build_consumer_key_public_profile_summary_enabled() -> None:
    """Phase 3 启用后：persona_context/public_profile_summary 可构建投影。"""

    service, _, store = make_build_service()
    built = await service.build(
        owner_user_id=OWNER_ID,
        function_key="persona_context",
        purpose="session_context",
        data_category="public_profile_summary",
    )
    assert built["status"] == "active"
    assert built["subject"] == "personal"


async def test_build_version_increments_and_supersedes_old() -> None:
    service, _, store = make_build_service()
    seed_claim(store, "c_v1")
    first = await service.build(**BUILD_KWARGS)
    assert first["projection_version"] == 1
    seed_claim(store, "c_v2", dimension="values", value="看重诚实")
    second = await service.build(**BUILD_KWARGS)
    assert second["projection_version"] == 2
    key = (OWNER_ID, "search", "candidate_filter", "personal_profile")
    statuses = {row["projection_version"]: row["status"] for row in store.projections[key]}
    assert statuses == {1: "invalidated", 2: "active"}


async def test_build_same_input_hash_is_idempotent() -> None:
    service, _, store = make_build_service()
    seed_claim(store, "c_ok")
    first = await service.build(**BUILD_KWARGS)
    again = await service.build(**BUILD_KWARGS)
    assert again["projection_id"] == first["projection_id"]
    key = (OWNER_ID, "search", "candidate_filter", "personal_profile")
    assert len(store.projections[key]) == 1


async def test_invalidate_for_claim_hits_only_referencing_projections() -> None:
    service, _, store = make_build_service()
    seed_claim(store, "c_hit")
    await service.build(**BUILD_KWARGS)
    seed_claim(store, "c_other", dimension="values", value="看重家庭")
    await service.build(
        owner_user_id=OWNER_ID,
        function_key="recommend",
        purpose="candidate_rank",
        data_category="personal_profile",
    )
    invalidated = await service.invalidate_for_claim(
        OWNER_ID, "c_hit", reason="claim_corrected"
    )
    # 两个维度构建时都引用了 c_hit（build 收全量 confirmed claims）→ 都失效。
    assert invalidated == 2
    key_hit = (OWNER_ID, "search", "candidate_filter", "personal_profile")
    key_other = (OWNER_ID, "recommend", "candidate_rank", "personal_profile")
    assert store.projections[key_hit][0]["status"] == "invalidated"
    assert store.projections[key_other][0]["status"] == "invalidated"


async def test_build_rejects_contract_violation_loudly(monkeypatch) -> None:
    """对抗性审查回归：坏 entries 必须在构建期响亮失败（零落库、消息无原文），
    而不是静默落库后读取端 fail closed 退化成 100% 回退。"""

    from app.schemas.ai_memory_projection import ProjectionDocument  # noqa: F401
    from app.services.ai.memory import projections as projections_mod
    from app.services.ai.memory.projections import ProjectionContractError

    service, session, store = make_build_service()
    seed_claim(store, "c_ok", value="这条原文绝不能出现在错误信息里")

    def _broken_entry(claim):
        entry = _orig_claim_to_entry(claim)
        if entry is None:
            return None
        broken = dict(entry)
        broken["stability"] = 1.5  # 越界（allowlist 字段但违反 0..1 约束）
        return broken

    _orig_claim_to_entry = projections_mod._claim_to_entry
    monkeypatch.setattr(projections_mod, "_claim_to_entry", _broken_entry)
    with pytest.raises(ProjectionContractError) as excinfo:
        await service.build(**BUILD_KWARGS)
    assert "这条原文" not in str(excinfo.value), "契约错误消息不得携带字段原文"
    assert not store.projections, "契约违规不得落库"
    monkeypatch.setattr(projections_mod, "_claim_to_entry", _orig_claim_to_entry)


async def test_build_document_validation_happy_path_unchanged() -> None:
    """接线契约校验后，正常构建行为不变（同 hash 幂等照旧）。"""

    service, _, store = make_build_service()
    seed_claim(store, "c_ok")
    first = await service.build(**BUILD_KWARGS)
    again = await service.build(**BUILD_KWARGS)
    assert again["projection_id"] == first["projection_id"]
    key = (OWNER_ID, "search", "candidate_filter", "personal_profile")
    assert len(store.projections[key]) == 1

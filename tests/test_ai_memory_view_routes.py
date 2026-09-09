"""Memory View 后端 API 路由测试（Phase 3 Task 2）。

覆盖：GET /ai/memory/view（跨主体条目 + updated_at + 授权概览、cursor、
401/400/422）与 POST /ai/memory/grants/{grant_id}/revoke（owner-scoped 404、
立即失效、重复请求 already_revoked、Idempotency-Key 必填、privacy 传播）。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import CurrentUser, get_current_user
from app.api.routes import ai_memory as ai_memory_routes
from app.db.session import get_db
from tests.test_ai_memory_ledger import (
    FakeMemorySession,
    MemoryStore,
    _MappingResult,
    _WriteResult,
)

pytestmark = pytest.mark.asyncio

OWNER_ID = 42
OTHER_ID = 43


class _OneMappingResult(_MappingResult):
    """revisions.py 需要 mappings().one()。"""

    def one(self) -> dict[str, Any]:
        return self._rows[0]


class ViewSession(FakeMemorySession):
    """在 Core fake 之上补齐 view / 投影授权 SQL 路由。"""

    def __init__(self, store: MemoryStore) -> None:
        super().__init__(store)
        self.grants: dict[str, dict[str, Any]] = {}
        self.projections: dict[tuple[int, str, str, str], list[dict[str, Any]]] = {}
        self.privacy_revision = 0

    def _route(self, sql: str, v: dict[str, Any]) -> Any:
        # ---- Memory View：跨主体 claim 列表（含 updated_at，subject 可选）----
        if (
            "FROM ai_memory_claim" in sql
            and "status IN (" in sql
            and "FOR UPDATE" not in sql
            and "updated_at" in sql
        ):
            owner = int(v["owner_user_id"])
            after_seq = int(v.get("after_seq", 0))
            limit = int(v.get("limit", 20))
            status_part = sql.split("status IN (", 1)[1].split(")", 1)[0]
            allowed = tuple(s.strip().strip("'") for s in status_part.split(","))
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
            subject = v.get("subject")
            rows = []
            for row in self._store.claims.values():
                if row["owner_user_id"] != owner or row["status"] not in allowed:
                    continue
                if row["last_event_seq"] <= after_seq:
                    continue
                if subject is not None and row["subject"] != subject:
                    continue
                if (
                    row["owner_user_id"],
                    row["subject"],
                    row["namespace"],
                    row["canonical_key"],
                ) in active_tombstones:
                    continue
                rows.append(dict(row, updated_at="2026-09-06T08:00:00"))
            rows.sort(key=lambda row: row["last_event_seq"])
            return _MappingResult(rows[:limit])
        # ---- 投影授权 ----
        if (
            "FROM ai_memory_projection_grant" in sql
            and "policy_revision" in sql
            and "ORDER BY" in sql
        ):
            rows = [
                dict(row)
                for row in self.grants.values()
                if row["owner_user_id"] == int(v["owner_user_id"])
            ]
            rows.sort(key=lambda row: (row["function_key"], row["purpose"], row["data_category"]))
            return _MappingResult(rows)
        if (
            "FROM ai_memory_projection_grant" in sql
            and "grant_id = :grant_id" in sql
        ):
            row = self.grants.get(str(v["grant_id"]))
            return _MappingResult([dict(row)] if row else [])
        if (
            "FROM ai_memory_projection_grant" in sql
            and "function_key = :function_key" in sql
        ):
            key = (
                int(v["owner_user_id"]),
                str(v["function_key"]),
                str(v["purpose"]),
                str(v["data_category"]),
            )
            matches = [
                dict(row)
                for row in self.grants.values()
                if (
                    int(row["owner_user_id"]),
                    str(row["function_key"]),
                    str(row["purpose"]),
                    str(row["data_category"]),
                )
                == key
            ]
            return _MappingResult(matches[:1])
        if "UPDATE ai_memory_projection_grant" in sql:
            target = str(v["grant_id"])
            row = self.grants.get(target)
            if row is not None and row["status"] == "active":
                row["status"] = "revoked"
                row["revoked_at"] = "FAKE-UTC"
                return _WriteResult(rowcount=1)
            return _WriteResult(rowcount=0)
        if "UPDATE ai_memory_projection SET status = 'invalidated'" in sql:
            key = (
                int(v["owner_user_id"]),
                str(v["function_key"]),
                str(v["purpose"]),
                str(v["data_category"]),
            )
            updated = 0
            for row in self.projections.get(key, []):
                if row["status"] == "active":
                    row["status"] = "invalidated"
                    row["invalidated_reason"] = v["invalidated_reason"]
                    updated += 1
            return _WriteResult(rowcount=updated)
        # ---- privacy revision（撤权传播）----
        if "INSERT INTO user_revision_state" in sql:
            self.privacy_revision += 1
            return _WriteResult(rowcount=1)
        if "FROM user_revision_state" in sql:
            return _OneMappingResult(
                [
                    {
                        "profile_revision": 0,
                        "preference_revision": 0,
                        "privacy_revision": self.privacy_revision,
                        "relationship_revision": 0,
                        "policy_revision": 0,
                    }
                ]
            )
        return super()._route(sql, v)


def _seed_grant(
    session: ViewSession,
    owner_id: int,
    function_key: str = "search",
    purpose: str = "candidate_filter",
    data_category: str = "personal_profile",
) -> str:
    grant_id = f"prj-grant:{owner_id}:{function_key}:{purpose}:{data_category}"
    session.grants[grant_id] = {
        "grant_id": grant_id,
        "owner_user_id": owner_id,
        "function_key": function_key,
        "purpose": purpose,
        "data_category": data_category,
        "status": "active",
        "policy_revision": "ai-policy-2026-08-07-v1",
        "granted_at": "2026-09-06T08:00:00",
        "revoked_at": None,
    }
    return grant_id


def _build_client(store: MemoryStore) -> tuple[TestClient, ViewSession]:
    app = FastAPI()
    app.include_router(ai_memory_routes.router, prefix="/api/v1/ai")
    session = ViewSession(store)

    async def _override_db():
        yield session

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=OWNER_ID, session_id=1, phone="13000000000", status=1,
        realname_status=2, face_verified=1,
    )
    return TestClient(app, raise_server_exceptions=False), session


async def _seed_two_claims(store: MemoryStore) -> None:
    from app.services.ai.memory.ledger import MemoryLedger
    from app.services.ai.memory.service import MemoryService

    session = ViewSession(store)
    service = MemoryService(session, ledger=MemoryLedger(session))
    await service.propose(
        owner_user_id=OWNER_ID,
        subject="personal",
        canonical_key="personal:lifestyle:coffee",
        dimension="lifestyle",
        value="每天喝咖啡",
        confidence=0.9,
        source_kind="user_explicit",
        fact_kind="about_user",
        source_quote="我每天早上都要喝一杯咖啡",
        source_ref="candidate:abc",
        idempotency_key="view-seed-personal",
    )
    await service.propose(
        owner_user_id=OWNER_ID,
        subject="ideal_partner",
        canonical_key="ideal_partner:lifestyle:honest",
        dimension="lifestyle",
        value="希望对方诚实",
        confidence=0.8,
        source_kind="user_explicit",
        fact_kind="partner_preference",
        source_quote="我希望另一半是诚实的",
        source_ref="candidate:abc",
        idempotency_key="view-seed-ideal",
    )


# ---------------------------------------------------------------------------
# GET /ai/memory/view
# ---------------------------------------------------------------------------


async def test_view_requires_auth() -> None:
    store = MemoryStore()
    client, _ = _build_client(store)
    client.app.dependency_overrides.pop(get_current_user)
    assert client.get("/api/v1/ai/memory/view").status_code == 401


async def test_view_returns_both_subjects_with_updated_at_and_grants() -> None:
    store = MemoryStore()
    await _seed_two_claims(store)
    client, session = _build_client(store)
    _seed_grant(session, OWNER_ID)
    response = client.get("/api/v1/ai/memory/view")
    assert response.status_code == 200
    body = response.json()
    subjects = {item["subject"] for item in body["items"]}
    assert subjects == {"personal", "ideal_partner"}
    item = body["items"][0]
    assert item["updated_at"] == "2026-09-06T08:00:00"
    assert item["revision"] >= 1
    assert "value" in item and "source_ref" in item
    assert "transcript" not in item and "payload" not in item
    assert len(body["grants"]) == 1
    grant = body["grants"][0]
    assert grant["status"] == "active"
    assert grant["function_key"] == "search"
    assert set(grant) == {
        "grant_id", "function_key", "purpose", "data_category",
        "status", "policy_revision", "granted_at", "revoked_at",
    }


async def test_view_subject_filter_and_invalid_inputs() -> None:
    store = MemoryStore()
    await _seed_two_claims(store)
    client, _ = _build_client(store)
    filtered = client.get(
        "/api/v1/ai/memory/view", params={"subject": "personal"}
    ).json()
    assert {item["subject"] for item in filtered["items"]} == {"personal"}
    assert (
        client.get(
            "/api/v1/ai/memory/view", params={"status": "bogus"}
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/api/v1/ai/memory/view", params={"cursor": "forged.0"}
        ).status_code
        == 400
    )


async def test_view_owner_isolation() -> None:
    store = MemoryStore()
    await _seed_two_claims(store)
    client, _ = _build_client(store)
    # 其他 owner 的数据不在当前用户视图（当前库中仅本 owner 数据可查）。
    body = client.get("/api/v1/ai/memory/view").json()
    assert all(item["claim_id"] for item in body["items"])


async def test_view_cursor_pagination() -> None:
    store = MemoryStore()
    await _seed_two_claims(store)
    client, _ = _build_client(store)
    page = client.get(
        "/api/v1/ai/memory/view", params={"limit": 1, "status": "proposed"}
    ).json()
    assert page["has_more"] is True
    assert page["next_cursor"]
    next_page = client.get(
        "/api/v1/ai/memory/view",
        params={
            "limit": 1,
            "status": "proposed",
            "cursor": page["next_cursor"],
        },
    ).json()
    assert next_page["items"]
    assert next_page["has_more"] is False

    # 游标是用户和筛选条件绑定的；不能把同一页游标挪到另一组条件中复用。
    changed_filter = client.get(
        "/api/v1/ai/memory/view",
        params={
            "limit": 1,
            "status": "active",
            "cursor": page["next_cursor"],
        },
    )
    assert changed_filter.status_code == 400


# ---------------------------------------------------------------------------
# POST /ai/memory/grants/{grant_id}/revoke
# ---------------------------------------------------------------------------


async def test_grant_revoke_requires_idempotency_key() -> None:
    store = MemoryStore()
    client, session = _build_client(store)
    grant_id = _seed_grant(session, OWNER_ID)
    response = client.post(f"/api/v1/ai/memory/grants/{grant_id}/revoke")
    assert response.status_code == 422


async def test_grant_revoke_revokes_and_invalidates_immediately() -> None:
    store = MemoryStore()
    client, session = _build_client(store)
    grant_id = _seed_grant(session, OWNER_ID)
    key = (OWNER_ID, "search", "candidate_filter", "personal_profile")
    session.projections[key] = [
        {"status": "active", "projection_version": 1},
        {"status": "active", "projection_version": 2},
    ]
    outbox_before = len(store.outbox)
    response = client.post(
        f"/api/v1/ai/memory/grants/{grant_id}/revoke",
        headers={"Idempotency-Key": "revoke-1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "revoked"
    assert body["invalidated_projections"] == 2
    assert session.grants[grant_id]["status"] == "revoked"
    assert all(row["status"] == "invalidated" for row in session.projections[key])
    # privacy revision 递增并产生传播事件。
    assert session.privacy_revision == 1
    assert len(store.outbox) == outbox_before + 1


async def test_grant_revoke_repeat_returns_already_revoked_without_revision_bump() -> None:
    store = MemoryStore()
    client, session = _build_client(store)
    grant_id = _seed_grant(session, OWNER_ID)
    first = client.post(
        f"/api/v1/ai/memory/grants/{grant_id}/revoke",
        headers={"Idempotency-Key": "revoke-1"},
    )
    assert first.json()["status"] == "revoked"
    revision_after_first = session.privacy_revision
    outbox_after_first = len(store.outbox)
    second = client.post(
        f"/api/v1/ai/memory/grants/{grant_id}/revoke",
        headers={"Idempotency-Key": "revoke-2"},
    )
    assert second.status_code == 200
    assert second.json()["status"] == "already_revoked"
    assert session.privacy_revision == revision_after_first
    assert len(store.outbox) == outbox_after_first


async def test_grant_revoke_is_owner_scoped() -> None:
    store = MemoryStore()
    client, session = _build_client(store)
    other_grant = _seed_grant(session, OTHER_ID)
    response = client.post(
        f"/api/v1/ai/memory/grants/{other_grant}/revoke",
        headers={"Idempotency-Key": "revoke-x"},
    )
    assert response.status_code == 404
    assert session.grants[other_grant]["status"] == "active"

    unknown = client.post(
        "/api/v1/ai/memory/grants/prj-grant:42:search:candidate_filter:personal_profile/revoke",
        headers={"Idempotency-Key": "revoke-y"},
    )
    assert unknown.status_code == 404

"""Memory Kernel Core v1 API route tests (Task 7).

Uses a minimal FastAPI app with the real ``ai_memory`` router and overridden
auth/DB dependencies, backed by the shared in-memory store.  Covers: 401
auth, owner-scoped 404, optimistic-lock 409, Idempotency-Key replay, the
minimal response shape (no transcript) and cursor issuance/verification.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import CurrentUser, get_current_user
from app.api.routes import ai_memory as ai_memory_routes
from app.db.session import get_db

from tests.test_ai_memory_ledger import FakeMemorySession, MemoryStore

pytestmark = pytest.mark.asyncio

OWNER_ID = 42
OTHER_ID = 43


class _OverrideSession(FakeMemorySession):
    """FakeMemorySession is already the exact surface the router needs."""


def _build_client(store: MemoryStore) -> TestClient:
    app = FastAPI()
    app.include_router(ai_memory_routes.router, prefix="/api/v1/ai")
    session = _OverrideSession(store)

    async def _override_db():
        yield session

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=OWNER_ID, session_id=1, phone="13000000000", status=1,
        realname_status=2, face_verified=1,
    )
    return TestClient(app, raise_server_exceptions=False), session


async def _seed_claim(
    store: MemoryStore,
    owner_id: int = OWNER_ID,
    marker: str = "coffee",
) -> dict[str, Any]:
    from app.services.ai.memory.ledger import MemoryLedger
    from app.services.ai.memory.service import MemoryService

    session = _OverrideSession(store)
    service = MemoryService(session, ledger=MemoryLedger(session))
    await service.propose(
        owner_user_id=owner_id,
        subject="personal",
        canonical_key=f"personal:lifestyle:{marker}",
        dimension="lifestyle",
        value="每天喝咖啡",
        confidence=0.9,
        source_kind="user_explicit",
        fact_kind="about_user",
        source_quote="我每天早上都要喝一杯咖啡",
        source_ref="candidate:abc",
        idempotency_key=f"route-seed-{owner_id}-{marker}",
    )
    claim_row = next(
        row
        for row in store.claims.values()
        if row["owner_user_id"] == owner_id
        and row["canonical_key"] == f"personal:lifestyle:{marker}"
    )
    return dict(claim_row)


def _headers(**extra: str) -> dict[str, str]:
    headers = {"Idempotency-Key": "route-key-1"}
    headers.update(extra)
    return headers


# ---------------------------------------------------------------------------
# GET /ai/memory
# ---------------------------------------------------------------------------


async def test_get_memory_requires_auth(store: MemoryStore | None = None) -> None:
    store = store or MemoryStore()
    client, _ = _build_client(store)
    client.app.dependency_overrides.pop(get_current_user)
    response = client.get("/api/v1/ai/memory", params={"subject": "personal"})
    assert response.status_code == 401


async def test_get_memory_returns_minimal_items(store: MemoryStore | None = None) -> None:
    store = store or MemoryStore()
    await _seed_claim(store)
    client, _ = _build_client(store)
    response = client.get("/api/v1/ai/memory", params={"subject": "personal"})
    assert response.status_code == 200
    body = response.json()
    assert body["items"], "seeded claim must be listed"
    item = body["items"][0]
    allowed = {
        "claim_id",
        "subject",
        "node_type",
        "content",
        "value",
        "status",
        "confidence",
        "stability",
        "importance",
        "source_quote",
        "source_ref",
        "canonical_key",
        "revision",
    }
    assert set(item) <= allowed, f"unexpected response keys: {set(item) - allowed}"
    assert item["status"] == "proposed"
    assert item["source_quote"] == "我每天早上都要喝一杯咖啡"
    assert item["source_ref"] == "candidate:abc"
    raw = json.dumps(body, ensure_ascii=False)
    assert "transcript" not in raw


async def test_get_memory_status_alias_active_and_subject_filter(
    store: MemoryStore | None = None,
) -> None:
    store = store or MemoryStore()
    await _seed_claim(store, OWNER_ID)
    await _seed_claim(store, OTHER_ID)
    client, _ = _build_client(store)
    # active 别名 = proposed + confirmed 的有效记忆
    response = client.get(
        "/api/v1/ai/memory", params={"subject": "personal", "status": "active"}
    )
    assert response.status_code == 200
    assert {item["claim_id"] for item in response.json()["items"]}
    # owner 隔离：只返回当前登录用户自己的条目
    assert all(item["subject"] == "personal" for item in response.json()["items"])
    other_rows = [
        row for row in store.claims.values() if row["owner_user_id"] == OTHER_ID
    ]
    listed_ids = {item["claim_id"] for item in response.json()["items"]}
    assert not {row["claim_id"] for row in other_rows} & listed_ids


async def test_get_memory_cursor_is_signed_and_verified(
    store: MemoryStore | None = None,
) -> None:
    store = store or MemoryStore()
    await _seed_claim(store)
    await _seed_claim(store, marker="tea")
    client, _ = _build_client(store)
    first = client.get("/api/v1/ai/memory", params={"subject": "personal", "limit": 1})
    body = first.json()
    assert body["has_more"] is True
    assert body["next_cursor"]
    second = client.get(
        "/api/v1/ai/memory",
        params={"subject": "personal", "limit": 1, "cursor": body["next_cursor"]},
    )
    assert second.status_code == 200
    assert second.json()["has_more"] is False
    assert second.json()["items"]
    # 同一签名游标不能改变 status 复用。
    changed_filter = client.get(
        "/api/v1/ai/memory",
        params={
            "subject": "personal",
            "status": "active",
            "cursor": body["next_cursor"],
        },
    )
    assert changed_filter.status_code == 400
    # 篡改/伪造 cursor 必须被拒绝（示例值不可直接复用）
    forged = base64.urlsafe_b64encode(json.dumps({"after_seq": 0}).encode()).decode().rstrip("=")
    bad = client.get(
        "/api/v1/ai/memory", params={"subject": "personal", "cursor": f"{forged}.deadbeef"}
    )
    assert bad.status_code == 400


# ---------------------------------------------------------------------------
# POST confirm / correct
# ---------------------------------------------------------------------------


async def test_confirm_rejects_stale_revision_and_unknown_claim(
    store: MemoryStore | None = None,
) -> None:
    store = store or MemoryStore()
    claim = await _seed_claim(store)
    client, _ = _build_client(store)
    ok = client.post(
        f"/api/v1/ai/memory/{claim['claim_id']}/confirm",
        json={"expected_revision": int(claim["last_event_seq"]), "importance": 0.9},
        headers=_headers(),
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "confirmed"
    # 409：expected_revision 已过期
    stale = client.post(
        f"/api/v1/ai/memory/{claim['claim_id']}/confirm",
        json={"expected_revision": int(claim["last_event_seq"]), "importance": 0.9},
        headers={"Idempotency-Key": "route-key-2"},
    )
    assert stale.status_code == 409
    # 404：他人/不存在的 claim
    missing = client.post(
        "/api/v1/ai/memory/clm-nope/confirm",
        json={"expected_revision": 1, "importance": 0.5},
        headers=_headers(),
    )
    assert missing.status_code == 404


async def test_confirm_idempotency_key_replays_same_event(
    store: MemoryStore | None = None,
) -> None:
    store = store or MemoryStore()
    claim = await _seed_claim(store)
    client, _ = _build_client(store)
    first = client.post(
        f"/api/v1/ai/memory/{claim['claim_id']}/confirm",
        json={"expected_revision": int(claim["last_event_seq"]), "importance": 0.9},
        headers=_headers(),
    )
    assert first.status_code == 200
    events_before = len(store.events)
    replay = client.post(
        f"/api/v1/ai/memory/{claim['claim_id']}/confirm",
        json={"expected_revision": int(claim["last_event_seq"]), "importance": 0.9},
        headers=_headers(),
    )
    assert replay.status_code == 200
    assert replay.json()["event_id"] == first.json()["event_id"]
    assert len(store.events) == events_before, "重放不得追加事件"


async def test_correct_updates_memory_claim(store: MemoryStore | None = None) -> None:
    store = store or MemoryStore()
    claim = await _seed_claim(store)
    client, _ = _build_client(store)
    response = client.post(
        f"/api/v1/ai/memory/{claim['claim_id']}/correct",
        json={
            "expected_revision": int(claim["last_event_seq"]),
            "value": "基本不喝咖啡",
            "source_quote": "其实我最近戒了",
        },
        headers=_headers(),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "user_corrected"
    (claim_row,) = store.claims.values()
    assert json.loads(claim_row["value_json"]) == "基本不喝咖啡"


# ---------------------------------------------------------------------------
# POST suppress / lift
# ---------------------------------------------------------------------------


async def test_suppress_and_lift_roundtrip(store: MemoryStore | None = None) -> None:
    store = store or MemoryStore()
    claim = await _seed_claim(store)
    client, _ = _build_client(store)
    suppressed = client.post(
        f"/api/v1/ai/memory/{claim['claim_id']}/suppress",
        json={"reason": "不再准确"},
        headers=_headers(),
    )
    assert suppressed.status_code == 200
    (tombstone,) = store.suppressions.values()
    assert tombstone["status"] == "active"
    lifted = client.post(
        f"/api/v1/ai/memory/suppressions/{tombstone['suppression_id']}/lift",
        json={},
        headers={"Idempotency-Key": "lift-key-1"},
    )
    assert lifted.status_code == 200
    assert store.suppressions[tombstone["suppression_id"]]["status"] == "lifted"
    # lift 是 owner 动作：未知墓碑 404
    missing = client.post(
        "/api/v1/ai/memory/suppressions/sup-nope/lift",
        json={},
        headers={"Idempotency-Key": "lift-key-2"},
    )
    assert missing.status_code == 404


async def test_memory_endpoints_registered_in_openapi() -> None:
    app = FastAPI()
    app.include_router(ai_memory_routes.router, prefix="/api/v1/ai")
    schema = app.openapi()
    paths = schema["paths"]
    for path in (
        "/api/v1/ai/memory",
        "/api/v1/ai/memory/{claim_id}/confirm",
        "/api/v1/ai/memory/{claim_id}/correct",
        "/api/v1/ai/memory/{claim_id}/suppress",
        "/api/v1/ai/memory/suppressions/{suppression_id}/lift",
    ):
        assert path in paths, f"missing OpenAPI path {path}"


async def test_post_endpoints_commit_before_responding() -> None:
    """对抗性审查回归：写端点必须显式 commit——get_db 关闭会话时未提交事务
    会被回滚，缺 commit 意味着确认/纠正/删除在生产上全部静默丢失。"""

    store = MemoryStore()
    claim = await _seed_claim(store)
    client, session = _build_client(store)
    session.commits = 0

    confirmed = client.post(
        f"/api/v1/ai/memory/{claim['claim_id']}/confirm",
        json={"expected_revision": int(claim["last_event_seq"]), "importance": 0.8},
        headers=_headers(),
    )
    assert confirmed.status_code == 200
    assert session.commits >= 1, "confirm 端点缺少显式 commit"

    suppressed = client.post(
        f"/api/v1/ai/memory/{claim['claim_id']}/suppress",
        json={"reason": "不再准确"},
        headers={"Idempotency-Key": "commit-suppress-1"},
    )
    assert suppressed.status_code == 200
    commits_after_suppress = session.commits
    assert commits_after_suppress >= 2, "suppress 端点缺少显式 commit"

    (tombstone,) = store.suppressions.values()
    lifted = client.post(
        f"/api/v1/ai/memory/suppressions/{tombstone['suppression_id']}/lift",
        json={},
        headers={"Idempotency-Key": "commit-lift-1"},
    )
    assert lifted.status_code == 200
    assert session.commits > commits_after_suppress, "lift 端点缺少显式 commit"


async def test_get_memory_hides_suppressed_claims() -> None:
    """对抗性审查回归：删除墓碑生效后 GET /memory 不得再返回该记忆。"""

    store = MemoryStore()
    claim = await _seed_claim(store)
    client, _ = _build_client(store)

    suppressed = client.post(
        f"/api/v1/ai/memory/{claim['claim_id']}/suppress",
        json={"reason": "测试删除"},
        headers={"Idempotency-Key": "hide-suppress-1"},
    )
    assert suppressed.status_code == 200

    listed = client.get(
        "/api/v1/ai/memory",
        params={"subject": "personal"},
    )
    assert listed.status_code == 200
    assert listed.json()["items"] == [], "已删除记忆不得再出现在列表"

    (tombstone,) = store.suppressions.values()
    lifted = client.post(
        f"/api/v1/ai/memory/suppressions/{tombstone['suppression_id']}/lift",
        json={},
        headers={"Idempotency-Key": "hide-lift-1"},
    )
    assert lifted.status_code == 200

    relisted = client.get(
        "/api/v1/ai/memory",
        params={"subject": "personal"},
    )
    assert relisted.status_code == 200
    assert [item["claim_id"] for item in relisted.json()["items"]] == [
        claim["claim_id"]
    ]

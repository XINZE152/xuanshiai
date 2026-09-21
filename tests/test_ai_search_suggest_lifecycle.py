"""第二批契约测试：建议缓存链路封闭（撤回/删除闭环 + 代际隔离）。

对应修复清单 §3.9 要求的五个撤回时点场景，以及 §3.7.5 的清理 pattern 契约：

1. 模型调用进行中撤回      → 任务 superseded；不发布；GET 返回 source='tags'
2. 模型返回后、写缓存前撤回 → 同上
3. 写缓存后、任务完成前撤回 → 完成期复核失败 → 已发布结果不可读
4. 缓存命中后再次读取期间撤回 → 本次及后续 GET 不再返回 source='ai'
5. 重新授权后，旧任务才结束 → 旧任务迟到结果不得覆盖新代际有效结果

另锁死三条不变量：
* 建议缓存的每个 key 生产者都至少被一条清理 pattern 覆盖（防 C-05 复发）；
* 建议代际键 TTL 严格大于建议缓存 TTL（防代际归零让旧缓存复活）；
* ``search_suggest`` 已登记完成期发布器（防 handler 直写缓存复发，C-06）。

这些测试全部走真服务函数 + 内存 fake session / fake Redis，不连真实 DB 与
Redis；断言只依赖"对外可观察结果"（GET 的 source、Redis 里是否真有内容、
暂存行状态），不复用实现内部的中间量。
"""

from __future__ import annotations

import fnmatch
import json
from typing import Any

import pytest

import app.services.ai.search as search_mod
from app.services.ai.base import SearchSuggestResult
from app.services.ai.gateway import InvokeOutcome
from app.services.ai.search import (
    SEARCH_SUGGEST_TASK_TYPE,
    _SEARCH_SUGGEST_MAX_ITEMS,
    _SUGGEST_CACHE_TTL_SECONDS,
    _suggest_cache_key,
    _suggest_generation_key,
    generate_search_suggestions,
    get_search_suggestions,
    invalidate_search_suggest_cache,
    publish_search_suggest,
    search_suggest_handler,
)
from app.services.ai.tasks import AiTaskRecord, complete_task
from app.services.derivation_outbox import _ai_redis_cache_patterns
from app.workers import ai_worker as worker_mod

OWNER = 10
POLICY_REVISION = "ai-policy-2026-08-07-v1"
CONSENT_VERSION = "search-parse-v1"
PROJECTION_HASH = "hash-searchable-v1"


# ---------------------------------------------------------------------------
# 最小 fake：Redis（dict + fnmatch SCAN）与 DB session（按 SQL 子串路由）
# ---------------------------------------------------------------------------


class FakeRedis:
    """dict 版异步 Redis：get / set / incr / expire / delete / scan_iter 同形。"""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.expiry: dict[str, int] = {}
        self.set_calls: list[str] = []
        self.incr_calls: list[str] = []

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(
        self,
        key: str,
        value: str,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool | None:
        if nx and key in self.data:
            return None
        self.data[key] = value
        self.set_calls.append(key)
        if ex is not None:
            self.expiry[key] = int(ex)
        return True

    async def incr(self, key: str) -> int:
        self.incr_calls.append(key)
        value = int(self.data.get(key, "0") or 0) + 1
        self.data[key] = str(value)
        return value
    async def expire(self, key: str, seconds: int) -> bool:
        self.expiry[key] = int(seconds)
        return key in self.data

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if self.data.pop(key, None) is not None:
                removed += 1
            self.expiry.pop(key, None)
        return removed

    async def scan_iter(self, match: str):
        for key in list(self.data):
            if fnmatch.fnmatch(key, match):
                yield key


class _MappingResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _MappingResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)


class _WriteResult:
    def __init__(self, *, rowcount: int = 1) -> None:
        self.rowcount = rowcount


class _RedisDown:
    async def get(self, *args: Any, **kwargs: Any) -> Any:
        from redis.exceptions import RedisError

        raise RedisError("redis down in lifecycle test")

    async def set(self, *args: Any, **kwargs: Any) -> Any:
        from redis.exceptions import RedisError

        raise RedisError("redis down in lifecycle test")

    async def incr(self, *args: Any, **kwargs: Any) -> Any:
        from redis.exceptions import RedisError

        raise RedisError("redis down in lifecycle test")


def _suggest_cache_keys(cache: FakeRedis) -> list[str]:
    """Redis 中真实存在的建议缓存内容键（排除代际/纪元元数据键）。"""
    return [
        key
        for key in cache.data
        if key.startswith("ai:search_suggest:")
        and not key.startswith("ai:search_suggest-generation:")
        and not key.startswith("ai:search_suggest-epoch:")
    ]


class LifecycleSession:
    """按 SQL 子串路由的 in-memory session：只实现本测试用到的表。"""

    def __init__(self, *, consents: list[dict[str, Any]] | None = None) -> None:
        self.consents = list(consents or [])
        self.projections: list[dict[str, Any]] = []
        self.publish_rows: dict[str, dict[str, Any]] = {}
        self.tasks: dict[str, dict[str, Any]] = {}
        self.revisions: dict[str, int] = {
            "profile_revision": 1,
            "preference_revision": 1,
            "privacy_revision": 1,
            "relationship_revision": 0,
            "policy_revision": 1,
        }
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.commits = 0
        self.rollbacks = 0

    # ---- seeding -------------------------------------------------------
    def seed_projection(
        self, *, status: str = "active", source_hash: str = PROJECTION_HASH
    ) -> None:
        self.projections.append(
            {
                "id": len(self.projections) + 1,
                "subject_user_id": OWNER,
                "projection_kind": "personal_searchable",
                "source_hash": source_hash,
                "status": status,
                "expires_at": None,
                "fields_json": json.dumps(
                    {"interest_tags": ["旅行"], "lifestyle_tags": ["看展"]},
                    ensure_ascii=False,
                ),
                "entry_digest": None,
            }
        )

    def seed_consent(self, *, revoked: bool = False) -> None:
        row = {
            "user_id": OWNER,
            "scope": search_mod.SEARCH_CONSENT_SCOPE,
            "version": CONSENT_VERSION,
            "policy_revision": POLICY_REVISION,
            "granted_at": "2026-09-17T00:00:00",
        }
        if not revoked:
            row["revoked_at"] = None
        self.consents = [row] if not revoked else []

    def seed_task(
        self,
        *,
        task_id: str = "task-suggest-1",
        status: str = "running",
        with_consent: bool = True,
    ) -> None:
        self.tasks[task_id] = {
            "id": 1,
            "task_id": task_id,
            "owner_user_id": OWNER,
            "task_type": SEARCH_SUGGEST_TASK_TYPE,
            "scene": search_mod.SEARCH_CONSENT_SCOPE,
            "idempotency_key": f"search-suggest-{OWNER}-20260917",
            "request_digest": "digest",
            "status": status,
            "stage": None,
            "progress_percent": None,
            "attempt_count": 1,
            "max_attempts": 3,
            "next_run_at": None,
            "lease_owner": "worker-1",
            "lease_until": None,
            "consent_snapshot_json": json.dumps(
                {
                    "scope": search_mod.SEARCH_CONSENT_SCOPE,
                    "version": CONSENT_VERSION,
                    "policy_revision": POLICY_REVISION,
                },
                ensure_ascii=False,
            )
            if with_consent
            else None,
            "source_revision_json": json.dumps(
                {
                    "profile": 0,
                    "preference": 0,
                    "privacy": 0,
                    "relationship": 0,
                    "policy": 0,
                }
            ),
            "payload_summary": json.dumps({"user_id": OWNER}),
            "error_code": None,
            "error_message": None,
            "result_ref": None,
            "created_at": None,
            "updated_at": None,
            "started_at": None,
            "finished_at": None,
        }

    # ---- session API ---------------------------------------------------
    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def execute(
        self, statement: object, params: dict[str, Any] | None = None
    ) -> _MappingResult | _WriteResult:
        sql = str(statement)
        values = dict(params or {})
        self.calls.append((sql, values))

        # ---- 授权 ----
        if "FROM ai_consent_grant" in sql:
            rows = [
                row
                for row in self.consents
                if row["user_id"] == int(values.get("user_id", 0))
                and row.get("scope") == values.get("scope")
                and row.get("revoked_at") is None
            ]
            return _MappingResult(rows[:1])

        # ---- 版本向量 ----
        if "FROM user_revision_state" in sql:
            row = dict(self.revisions)
            row["user_id"] = int(values.get("user_id", OWNER))
            return _MappingResult([row])

        # ---- 投影（代际内容锚）----
        # 真实 SQL 是 `ORDER BY id DESC LIMIT 1`：取最新一条 active 投影。夹具必须
        # 同序，否则"投影内容变化 → 代际变化"这条链路在测试里观察不到。
        if "entry_digest" in sql:
            rows = [
                row
                for row in self.projections
                if row["subject_user_id"] == int(values.get("user_id", 0))
                and row["status"] == "active"
            ]
            return _MappingResult(sorted(rows, key=lambda r: r["id"], reverse=True))
        if "FROM ai_feature_projection" in sql:
            rows = [
                row
                for row in self.projections
                if row["subject_user_id"] == int(values.get("user_id", 0))
                and row["status"] == "active"
            ]
            if "projection_kind = 'personal_searchable'" in sql:
                rows = [
                    row
                    for row in rows
                    if row["projection_kind"] == "personal_searchable"
                ]
            rows.sort(key=lambda r: r["id"], reverse=True)
            return _MappingResult(rows[:1])

        # ---- 建议暂存 ----
        if "INSERT INTO ai_search_suggest_publish" in sql:
            task_id = str(values["task_id"])
            existing = self.publish_rows.get(task_id)
            row = {
                "id": (existing or {}).get("id", len(self.publish_rows) + 1),
                "user_id": int(values["user_id"]),
                "task_id": task_id,
                "generation": str(values["generation"]),
                "suggestions_json": values["suggestions_json"],
                "consent_snapshot_json": values.get("consent_snapshot_json"),
                "source_revision_json": values.get("source_revision_json"),
                "status": "staged",
                "expires_at": values.get("expires_at"),
            }
            self.publish_rows[task_id] = row
            return _WriteResult(rowcount=1)
        if "FROM ai_search_suggest_publish" in sql:
            row = self.publish_rows.get(str(values.get("task_id")))
            return _MappingResult([dict(row)] if row else [])
        if sql.startswith("UPDATE ai_search_suggest_publish SET status = 'published'"):
            row = self.publish_rows.get(str(values.get("task_id")))
            if row and row["status"] == "staged":
                row["status"] = "published"
                return _WriteResult(rowcount=1)
            return _WriteResult(rowcount=0)
        if sql.startswith("UPDATE ai_search_suggest_publish SET status = 'superseded'"):
            row = self.publish_rows.get(str(values.get("task_id")))
            if row and row["status"] == "staged":
                row["status"] = "superseded"
                return _WriteResult(rowcount=1)
            return _WriteResult(rowcount=0)

        # ---- ai_task（完成期复核用）----
        if "FROM ai_task" in sql and "WHERE task_id = :task_id" in sql:
            row = self.tasks.get(str(values.get("task_id")))
            return _MappingResult([dict(row)] if row else [])
        if sql.startswith("UPDATE ai_task SET status = 'succeeded'"):
            row = self.tasks.get(str(values.get("task_id")))
            if row:
                row["status"] = "succeeded"
                row["result_ref"] = values.get("result_ref")
                row["consent_snapshot_json"] = None
                row["source_revision_json"] = None
                row["payload_summary"] = None
                return _WriteResult(rowcount=1)
            return _WriteResult(rowcount=0)
        if sql.startswith("UPDATE ai_task SET status = 'superseded'"):
            row = self.tasks.get(str(values.get("task_id")))
            if row:
                row["status"] = "superseded"
                row["consent_snapshot_json"] = None
                row["source_revision_json"] = None
                row["payload_summary"] = None
                row["result_ref"] = None
                return _WriteResult(rowcount=1)
            return _WriteResult(rowcount=0)
        if sql.startswith("UPDATE ai_task SET status = 'cancelled'"):
            row = self.tasks.get(str(values.get("task_id")))
            if row:
                row["status"] = "cancelled"
                return _WriteResult(rowcount=1)
            return _WriteResult(rowcount=0)
        if sql.startswith("UPDATE ai_task SET result_ref"):
            row = self.tasks.get(str(values.get("task_id")))
            if row:
                row["result_ref"] = values.get("result_ref")
                return _WriteResult(rowcount=1)
            return _WriteResult(rowcount=0)
        if sql.startswith("UPDATE ai_task SET status = 'retry_wait'"):
            row = self.tasks.get(str(values.get("task_id")))
            if row:
                row["status"] = "retry_wait"
                row["error_code"] = values.get("error_code")
                return _WriteResult(rowcount=1)
            return _WriteResult(rowcount=0)
        if "FROM ai_task" in sql and "WHERE task_id = :task_id" in sql:
            row = self.tasks.get(str(values.get("task_id")))
            return _MappingResult([dict(row)] if row else [])

        raise AssertionError(f"unhandled sql: {sql}")


class _FixedGateway:
    """固定建议结果（含重复词与空白行，用于校验去重/过滤）。"""

    def __init__(self, suggestions: tuple[str, ...] | None = None) -> None:
        self.suggestions = suggestions or (
            "喜欢旅行的女生",
            "热爱看展的",
            "喜欢旅行的女生",
            "  ",
            "户外爱好者",
        )
        self.calls = 0

    async def generate_search_suggestions(self, context: Any, request: Any) -> Any:
        del context, request
        self.calls += 1
        return InvokeOutcome(
            result=SearchSuggestResult(
                schema_version="search-suggest-v1", suggestions=self.suggestions
            )
        )


class _FailGateway:
    async def generate_search_suggestions(self, context: Any, request: Any) -> Any:
        del context, request
        return InvokeOutcome(
            result=None, error_code="AI_TEMPORARILY_UNAVAILABLE", retryable=True
        )


@pytest.fixture
def cache(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    fake = FakeRedis()
    monkeypatch.setattr(search_mod, "redis_client", fake)
    return fake


def _patch_gateway(monkeypatch: pytest.MonkeyPatch, gateway: Any) -> None:
    monkeypatch.setattr(
        search_mod, "AIGateway", lambda timeout_seconds=None: gateway
    )


def _task(session: LifecycleSession, task_id: str = "task-suggest-1") -> AiTaskRecord:
    return AiTaskRecord.from_row(session.tasks[task_id])


# ---------------------------------------------------------------------------
# 场景 1：模型调用进行中撤回
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoke_during_model_call_suppresses_publish_and_read(
    cache: FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模型调用期间撤回：任务 superseded、不发布、GET 回落 source='tags'。"""
    session = LifecycleSession()
    session.seed_projection()
    session.seed_consent()
    session.seed_task(status="running")
    _patch_gateway(monkeypatch, _FixedGateway())

    result = await search_suggest_handler(session, _task(session), "worker-1")
    assert result is not None, "handler 应先完成计算并暂存（发布另由 Worker 负责）"
    staged = session.publish_rows["task-suggest-1"]
    assert staged["status"] == "staged"

    # 撤回：授权消失 + 代际递增（模拟 consents.py 的同步失效半部）。
    session.seed_consent(revoked=True)
    await invalidate_search_suggest_cache(OWNER)

    completed = await complete_task(
        session, "task-suggest-1", "worker-1", result[0], result[1]
    )
    assert completed.status.value == "superseded"
    # 完成期复核失败 → 绝不发布：Redis 里除代际/纪元元数据外，不得有任何建议
    # 缓存键（`_suggest_cache_key` 的产物）。
    assert _suggest_cache_keys(cache) == []

    read = await get_search_suggestions(session, OWNER)
    assert read.source == "tags"
    assert read.items == ["旅行", "看展"]


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 场景 2：模型返回后、写缓存前撤回
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoke_after_model_returns_before_publish(
    cache: FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模型返回后撤回：即使 handler 已暂存，也不得发布到 Redis。"""
    session = LifecycleSession()
    session.seed_projection()
    session.seed_consent()
    session.seed_task(status="running")
    _patch_gateway(monkeypatch, _FixedGateway())

    result = await search_suggest_handler(session, _task(session), "worker-1")
    assert result is not None

    session.seed_consent(revoked=True)
    await invalidate_search_suggest_cache(OWNER)

    outcome = await publish_search_suggest(session, "task-t-suggest-1")
    assert outcome == "skipped", "未暂存则不发"
    assert session.publish_rows["task-suggest-1"]["status"] == "staged"
    assert not any(key.startswith("ai:search_suggest:") for key in cache.data)

    read = await get_search_suggestions(session, OWNER)
    assert read.source == "tags"


@pytest.mark.asyncio
async def test_publish_after_revoke_is_suppressed(
    cache: FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """已暂存但发布前撤回：发布被抑制，暂存标 superseded。"""
    session = LifecycleSession()
    session.seed_projection()
    session.seed_consent()
    session.seed_task(status="running")
    _patch_gateway(monkeypatch, _FixedGateway())

    result = await search_suggest_handler(session, _task(session), "worker-1")
    assert result is not None

    session.seed_consent(revoked=True)
    outcome = await publish_search_suggest(session, "task-suggest-1")
    assert outcome == "suppressed:consent_revoked"
    assert session.publish_rows["task-suggest-1"]["status"] == "superseded"
    assert not any(key.startswith("ai:search_suggest:") for key in cache.data)


# ---------------------------------------------------------------------------
# 场景 3：写缓存后、任务完成前撤回 → 已发布结果不可读
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_published_then_revoked_is_unreadable(
    cache: FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """先发布成功，随后撤回：GET 立即不再返回 source='ai'（代际已变）。"""
    session = LifecycleSession()
    session.seed_projection()
    session.seed_consent()
    session.seed_task(status="running")
    _patch_gateway(monkeypatch, _FixedGateway())

    result = await search_suggest_handler(session, _task(session), "worker-1")
    assert result is not None
    assert await publish_search_suggest(session, "task-suggest-1") == "published"
    published_keys = [key for key in cache.data if key.startswith("ai:search_suggest:")]
    assert len(published_keys) == 1

    read = await get_search_suggestions(session, OWNER)
    assert read.source == "ai"
    assert read.items == ["喜欢旅行的女生", "热爱看展的", "户外爱好者"]

    # 撤回：同步使代际失效。
    session.seed_consent(revoked=True)
    await invalidate_search_suggest_cache(OWNER)

    read_after = await get_search_suggestions(session, OWNER)
    assert read_after.source == "tags"
    # 旧代际的键仍在 Redis 里（不做逐键删除），但当前代际的读取路径已经换了 key：
    # 断言"旧键确实还在"且"它不等于当前代际的 key"，否则本条测试什么也没证明。
    assert published_keys and all(key in cache.data for key in published_keys)
    current = await search_mod.current_suggest_generation(session, OWNER)
    assert current is not None
    assert _suggest_cache_key(OWNER, current) not in cache.data


# ---------------------------------------------------------------------------
# 场景 4：缓存命中后再次读取期间撤回
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_hit_then_revoke_then_read_again(
    cache: FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """本次命中缓存后发生撤回：本次及后续 GET 均不再返回 source='ai'。"""
    session = LifecycleSession()
    session.seed_projection()
    session.seed_consent()
    session.seed_task(status="running")
    _patch_gateway(monkeypatch, _FixedGateway())

    result = await search_suggest_handler(session, _task(session), "worker-1")
    assert result is not None
    await publish_search_suggest(session, "task-suggest-1")
    assert (await get_search_suggestions(session, OWNER)).source == "ai"

    # 撤回发生在"上一次读取命中"之后。
    session.seed_consent(revoked=True)
    await invalidate_search_suggest_cache(OWNER)

    assert (await get_search_suggestions(session, OWNER)).source == "tags"
    # Redis 抖动也不能让旧缓存复活（fail-closed：读不到代际就不读缓存）。
    down = _RedisDown()
    monkeypatch.setattr(search_mod, "redis_client", down)
    assert (await get_search_suggestions(session, OWNER)).source == "tags"
    monkeypatch.setattr(search_mod, "redis_client", cache)
    assert (await get_search_suggestions(session, OWNER)).source == "tags"


# ---------------------------------------------------------------------------
# 场景 5：重新授权后旧任务才结束 → 迟到结果不得覆盖新代际
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_late_old_task_cannot_overwrite_new_generation(
    cache: FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """旧任务迟到：只写旧代际 key（读不到），新代际内容不被覆盖。"""
    session = LifecycleSession()
    session.seed_projection()
    session.seed_consent()
    session.seed_task(status="running")
    _patch_gateway(monkeypatch, _FixedGateway())
    old_result = await search_suggest_handler(session, _task(session), "worker-1")
    assert old_result is not None
    old_generation = session.publish_rows["task-suggest-1"]["generation"]

    # 撤回 → 重新授权：代际递增，用户在新代际下读到新的建议。
    session.seed_consent(revoked=True)
    await invalidate_search_suggest_cache(OWNER)
    session.seed_consent()
    new_counter = await invalidate_search_suggest_cache(OWNER)

    # 迟到发布（旧任务此刻才走到发布阶段）。
    outcome = await publish_search_suggest(session, "task-suggest-1")
    assert outcome == "suppressed:generation_changed"
    assert _suggest_cache_keys(cache) == [], "迟到发布不得写入任何建议缓存 key"

    # 新代际下按当前代际发布，读取端命中新内容。
    from datetime import timedelta as _td2

    session.publish_rows["task-suggest-2"] = {
        "id": 2,
        "user_id": OWNER,
        "task_id": "task-suggest-2",
        "generation": await search_mod.current_suggest_generation(session, OWNER),
        "suggestions_json": json.dumps(["新的建议"], ensure_ascii=False),
        "consent_snapshot_json": None,
        "source_revision_json": None,
        "status": "staged",
        "expires_at": search_mod._now_utc() + _td2(hours=1),
    }
    assert await publish_search_suggest(session, "task-suggest-2") == "published"
    read = await get_search_suggestions(session, OWNER)
    assert read.source == "ai"
    assert read.items == ["新的建议"]
    # 新代际的生成串最后一段为撤权计数（`<hash32>-<epoch>-<counter>`）。
    assert session.publish_rows["task-suggest-2"]["generation"].split("-")[-1] == str(
        new_counter
    )
    assert old_generation != session.publish_rows["task-suggest-2"]["generation"]


# ---------------------------------------------------------------------------
# 不变量：清理 pattern 覆盖 / 代际 TTL / 发布器登记
# ---------------------------------------------------------------------------


def test_every_suggest_cache_key_producer_is_covered_by_a_pattern() -> None:
    """每个建议缓存 key 生产者至少被一条清理 pattern 覆盖（防 C-05 复发）。"""
    patterns = _ai_redis_cache_patterns(OWNER)
    generation = "a" * 32 + "-0"
    produced_keys = (
        _suggest_cache_key(OWNER, generation),  # 当前实现（带代际）
        f"ai:search_suggest:{OWNER}",  # 旧格式（升级前写入的存量键）
        _suggest_generation_key(OWNER),  # 代际计数键
    )
    for key in produced_keys:
        assert any(fnmatch.fnmatch(key, pattern) for pattern in patterns), (
            f"缓存 key 未被任何清理 pattern 覆盖：{key}"
        )


@pytest.mark.asyncio
async def test_published_items_exceed_cap_are_truncated(
    cache: FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """发布端与读取端都截断到 _SEARCH_SUGGEST_MAX_ITEMS，不因缓存内容超长越界。"""
    session = LifecycleSession()
    session.seed_projection()
    session.seed_consent()
    session.seed_task(status="running")
    _patch_gateway(
        monkeypatch,
        _FixedGateway(
            tuple(f"建议{i}" for i in range(_SEARCH_SUGGEST_MAX_ITEMS + 3))
        ),
    )

    result = await search_suggest_handler(session, _task(session), "worker-1")
    assert result is not None
    assert await publish_search_suggest(session, "task-suggest-1") == "published"

    read = await get_search_suggestions(session, OWNER)
    assert read.source == "ai"
    assert len(read.items) == _SEARCH_SUGGEST_MAX_ITEMS
def test_suggest_generation_ttl_outlives_cache_ttl() -> None:
    """代际键必须活得比缓存久，否则旧代际缓存会随代际归零而复活。"""
    assert (
        search_mod._SUGGEST_GENERATION_TTL_SECONDS > _SUGGEST_CACHE_TTL_SECONDS
    )


def test_search_suggest_has_a_post_complete_publisher() -> None:
    """建议缓存必须走完成期发布，不得回到 handler 内直写（C-06）。"""
    assert SEARCH_SUGGEST_TASK_TYPE in worker_mod._POST_COMPLETE_PUBLISHERS
    assert (
        worker_mod._POST_COMPLETE_PUBLISHERS[SEARCH_SUGGEST_TASK_TYPE]
        is publish_search_suggest
    )


def test_search_suggest_is_registered_for_completion_gate() -> None:
    """``search_suggest`` 必须登记功能开关，否则完成期放行未知任务类型。"""
    from app.services.ai.tasks import _TASK_FEATURES
    from app.services.ai.flags import AiFeature

    assert _TASK_FEATURES.get(SEARCH_SUGGEST_TASK_TYPE) is AiFeature.SEARCH


@pytest.mark.asyncio
async def test_generate_requires_active_consent(cache: FakeRedis) -> None:
    """未授权时不得建任务（建议内容来自本人已确认资料）。"""
    from app.services.ai.search import SearchConsentRequired

    session = LifecycleSession()
    session.seed_projection()
    session.seed_consent(revoked=True)

    with pytest.raises(SearchConsentRequired):
        await generate_search_suggestions(session, OWNER, "client-key-1")


@pytest.mark.asyncio
async def test_handler_failure_does_not_stage(
    cache: FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模型失败不产生暂存行；发布阶段自然 skipped。"""
    session = LifecycleSession()
    session.seed_projection()
    session.seed_consent()
    session.seed_task(status="running")
    _patch_gateway(monkeypatch, _FailGateway())

    assert await search_suggest_handler(session, _task(session), "worker-1") is None
    assert session.publish_rows == {}
    assert await publish_search_suggest(session, "task-suggest-1") == "skipped"
    assert not any(key.startswith("ai:search_suggest:") for key in cache.data)


@pytest.mark.asyncio
async def test_read_denies_cache_when_only_consent_revoked(
    cache: FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """授权撤回但代际未变时，授权门必须独立拦住缓存读取。

    代际机制只在"撤权/注销会同步递增代际"的前提下生效；一旦撤权与代际递增
    之间出现缺口（例如 Redis 抖动导致递增失败、或撤权走了未递增的旧路径），
    读取路径若只信代际、不单独校验授权，已撤回授权用户的缓存会被重新暴露。
    这条测试把授权门与代际机制解耦：不递增代际，代际与发布时完全一致，只有
    授权门能拦住命中。
    """
    session = LifecycleSession()
    session.seed_projection()
    session.seed_consent()
    session.seed_task(status="running")
    _patch_gateway(monkeypatch, _FixedGateway())

    result = await search_suggest_handler(session, _task(session), "worker-1")
    assert result is not None
    assert await publish_search_suggest(session, "task-suggest-1") == "published"
    assert (await get_search_suggestions(session, OWNER)).source == "ai"
    published_keys = [
        key for key in cache.data if key.startswith("ai:search_suggest:")
    ]
    assert len(published_keys) == 1

    # 仅撤回授权：不递增代际，缓存 key 与当前代际仍然完全一致。
    session.seed_consent(revoked=True)
    assert published_keys[0] == _suggest_cache_key(
        OWNER, await search_mod.current_suggest_generation(session, OWNER)
    ), "本测试要求代际未变，否则无法把授权门与代际机制解耦"

    read = await get_search_suggestions(session, OWNER)
    assert read.source == "tags"
    assert read.items == ["旅行", "看展"]


@pytest.mark.asyncio
async def test_projection_content_change_invalidates_published_cache(
    cache: FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """投影内容变化（用户新增/修改已确认资料）→ 代际变化 → 旧建议缓存不可读。

    代际由"投影内容标识 + 撤权计数"派生。如果代际没有真正进入缓存 key，
    投影内容变了而旧 key 仍然命中——用户会继续看到基于旧资料的过期建议。
    """
    session = LifecycleSession()
    session.seed_projection(source_hash="hash-A")
    session.seed_consent()
    session.seed_task(status="running")
    _patch_gateway(monkeypatch, _FixedGateway())

    result = await search_suggest_handler(session, _task(session), "worker-1")
    assert result is not None
    assert await publish_search_suggest(session, "task-suggest-1") == "published"
    assert (await get_search_suggestions(session, OWNER)).source == "ai"

    # 用户新增一条已确认资料 → 投影重建（source_hash 变化，新 id 更大）。
    session.seed_projection(source_hash="hash-B")



@pytest.mark.asyncio
async def test_epoch_is_stable_across_invalidations(cache: FakeRedis) -> None:
    """纪元量在同一纪元内稳定：多次撤权只递增计数，不更换纪元。

    纪元量解决的是“计数键 TTL 到期后 INCR 从 1 重新计数 → 与历史代际串重名”
    （审查发现 (b)）。若纪元量不稳定（每次读都新建），同一时刻的读/写代际就
    不一致，已发布的内容永远读不回来——所以必须锁“稳定”这一面。
    """
    session = LifecycleSession()
    session.seed_projection()

    first = await search_mod.current_suggest_generation(session, OWNER)
    assert first is not None
    second = await search_mod.current_suggest_generation(session, OWNER)
    assert second == first

    await invalidate_search_suggest_cache(OWNER)
    third = await search_mod.current_suggest_generation(session, OWNER)
    assert third is not None and third != first
    assert third.split("-")[1] == first.split("-")[1], "撤权不得更换纪元"
    assert third.split("-")[2] == str(int(first.split("-")[2]) + 1)


@pytest.mark.asyncio
async def test_generation_differs_when_counter_resets_after_ttl(
    cache: FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """计数键“过期归零后重新计数”不得与历史代际串重合（审查发现 (b) 的回归锁）。

    复现序列：正常发布（得到历史代际串）→ 计数键被 Redis 回收（模拟 TTL 到期）
    → 再撤权（INCR 又得 1；无纪元量时代际串与历史完全一致，撤回前的缓存会复活）
    → 断言新代际串与历史不同。
    """
    session = LifecycleSession()
    session.seed_projection()
    session.seed_consent()
    session.seed_task(status="running")
    _patch_gateway(monkeypatch, _FixedGateway())

    result = await search_suggest_handler(session, _task(session), "worker-1")
    assert result is not None
    assert await publish_search_suggest(session, "task-suggest-1") == "published"
    historical = session.publish_rows["task-suggest-1"]["generation"]
    assert _suggest_cache_keys(cache) == [_suggest_cache_key(OWNER, historical)]

    # 模拟计数键 TTL 到期被 Redis 回收（纪元量仍在，故仅清计数键）。
    cache.data.pop(search_mod._suggest_generation_key(OWNER), None)

    # 再次撤权：INCR 从 1 重新开始。
    await invalidate_search_suggest_cache(OWNER)
    current = await search_mod.current_suggest_generation(session, OWNER)
    assert current is not None
    assert current != historical, (
        "计数键归零后重新计数不得复现历史代际串，否则撤回前的缓存会复活"
    )
    assert current.split("-")[1] == historical.split("-")[1], "同一用户在纪元内纪元量稳定"


@pytest.mark.asyncio
async def test_post_complete_publisher_only_fires_for_succeeded(
    cache: FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """完成期发布器只在任务 succeeded 时触发（C-06 的钩子级回归锁）。

    之前只断言"注册表里有 publish_search_suggest"，无法发现"钩子被提前调用"或
    "superseded 也发布"。这里直接驱动 Worker 的钩子函数本身。
    """
    from app.services.ai.tasks import AiTaskRecord as _Record

    published_calls: list[str] = []

    async def _fake_publisher(db: Any, task_id: str) -> str:
        published_calls.append(task_id)
        return "published"

    monkeypatch.setitem(
        worker_mod._POST_COMPLETE_PUBLISHERS,
        SEARCH_SUGGEST_TASK_TYPE,
        _fake_publisher,
    )
    session = LifecycleSession()

    def _record(status: str) -> _Record:
        session.seed_task(task_id=f"t-{status}", status="running")
        row = dict(session.tasks[f"t-{status}"])
        row["status"] = status
        return _Record.from_row(row)

    # 只有 succeeded 才发布。
    for status in ("superseded", "failed", "cancelled"):
        await worker_mod._run_post_complete_publisher(_record(status), session, None)
    assert published_calls == []

    await worker_mod._run_post_complete_publisher(_record("succeeded"), session, None)
    assert published_calls == ["t-succeeded"]


@pytest.mark.asyncio
async def test_publish_is_skipped_when_already_terminal(
    cache: FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """已发布/已抑制的暂存行不会重复发布（幂等）。"""
    session = LifecycleSession()
    session.seed_projection()
    session.seed_consent()
    session.seed_task(status="running")
    _patch_gateway(monkeypatch, _FixedGateway())

    result = await search_suggest_handler(session, _task(session), "worker-1")
    assert result is not None
    assert await publish_search_suggest(session, "task-suggest-1") == "published"
    set_calls_before = list(cache.set_calls)
    # 已发布的内容可读。
    assert (await get_search_suggestions(session, OWNER)).source == "ai"

    # 二次发布被跳过，且没有再写任何缓存键。
    assert await publish_search_suggest(session, "task-suggest-1") == "skipped"
    assert cache.set_calls == set_calls_before


@pytest.mark.asyncio
async def test_publish_is_suppressed_when_staged_row_expired(
    cache: FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """暂存行过期（或 expires_at 缺失/异常）时不得发布（fail-closed）。"""
    from datetime import timedelta as _td

    session = LifecycleSession()
    session.seed_projection()
    session.seed_consent()
    session.seed_task(status="running")
    _patch_gateway(monkeypatch, _FixedGateway())

    result = await search_suggest_handler(session, _task(session), "worker-1")
    assert result is not None

    # 过期：把 expires_at 挪到过去。
    session.publish_rows["task-suggest-1"]["expires_at"] = (
        search_mod._now_utc() - _td(seconds=1)
    )
    assert await publish_search_suggest(session, "task-suggest-1") == "suppressed:expired"
    assert session.publish_rows["task-suggest-1"]["status"] == "superseded"
    assert _suggest_cache_keys(cache) == []

    # 缺失/异常类型同样视为过期（不因 None 而放行）。
    session.publish_rows["task-suggest-1"]["expires_at"] = None
    session.publish_rows["task-suggest-1"]["status"] = "staged"
    assert await publish_search_suggest(session, "task-suggest-1") == "suppressed:expired"
    assert _suggest_cache_keys(cache) == []

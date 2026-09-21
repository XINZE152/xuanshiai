"""推荐读取期资格复验与刷新语义测试（第一批收尾）。

覆盖四件事，全部确定性（不依赖真实时钟流动、不依赖"同日幂等"的推断）：

1. **等价性**：`_pool_eligible_candidate_ids`（读取期复验）与
   `load_candidate_pool`（物化候选池）在相同事实下产生**相同的用户集合**——
   两者 SQL 已共享 `_POOL_PROJECTION_FROM` 片段，本测试锁住行为等价。
2. **同日改资料的刷新时序**：读取入口复用旧快照（不逐读重算）→
   入队同日至多一个重建任务（两次调用返回同一 task_id）→ 重建执行后
   新 generation 顶替旧代，读取可见新分数。
3. **请求人授权链**：请求人撤回 `profile_text_extract` 后，缓存期内
   旧快照仍可读（保留政策），快照过期后 miss 且入队被拒（任务不创建），
   路由语义为空页 + regenerating=false。
4. **连续失效候选的分页**：前段连续失效、后段仍有有效候选时，
   短页填补继续取到有效候选。

候选"当前可见性"（拉黑/隐藏/封禁/账号/审核）在 tests/test_ai_recommend_visibility.py
冻结；本文件聚焦资格等价性与快照刷新/保留语义。
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.services.ai import recommend as recommend_mod

pytestmark = pytest.mark.asyncio

VIEWER = 101
VIEWER_CONSENT = {
    "user_id": VIEWER,
    "scope": "profile_text_extract",
    "granted_at": "2026-09-01T00:00:00",
    "revoked_at": None,
}


def _visible_candidate_flags(user_id: int) -> dict[str, Any]:
    return {
        "candidate_id": user_id,
        "viewer_realname_status": 2,
        "viewer_is_vip": 1,
        "who_can_see_me": 1,
        "account_active": True,
        "profile_visible": True,
        "match_active": True,
        "not_restricted": True,
        "profile_complete": True,
        "media_approved": True,
        "blocked": False,
    }


def _consent(scope: str = "profile_text_extract") -> dict[str, Any]:
    return {"scope": scope, "version": "v1"}


class _MappingResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> "_MappingResult":
        return self

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def scalar_one(self) -> Any:
        row = self._rows[0]
        return row[next(iter(row))]


class _WriteResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _RefreshStore:
    """推荐读取/物化/任务链路的内存事实库。"""

    def __init__(self) -> None:
        self.snapshot_rows: list[dict[str, Any]] = []
        self.candidates: dict[int, dict[str, Any]] = {}
        self.projections: list[dict[str, Any]] = []
        self.consents: list[dict[str, Any]] = [dict(VIEWER_CONSENT)]
        self.revision_rows: dict[int, dict[str, Any]] = {}
        self.task_rows: dict[str, dict[str, Any]] = {}
        self._next_projection_id = 1

    # ---- 种子 ----

    def add_candidate(
        self,
        user_id: int,
        *,
        profile_fields: dict[str, Any] | None = None,
        preference_fields: dict[str, Any] | None = None,
        consent: dict[str, Any] | None = None,
    ) -> None:
        self.candidates[user_id] = _visible_candidate_flags(user_id)
        fields = profile_fields if profile_fields is not None else _rich_profile()
        for kind in ("personal_compatibility", "ideal_partner_preference"):
            self.projections.append(
                {
                    "id": self._next_projection_id,
                    "subject_user_id": user_id,
                    "projection_kind": kind,
                    "consent_snapshot_json": consent if consent is not None else _consent(),
                    "status": "active",
                    "ps_status": "active",
                    "expires_at": None,
                    "fields_json": (
                        fields if kind == "personal_compatibility"
                        else (preference_fields if preference_fields is not None else _rich_preference())
                    ),
                }
            )
            self._next_projection_id += 1

    def republish_candidate(self, user_id: int, new_fields: dict[str, Any]) -> None:
        """模拟"同一天修改资料"：旧投影行失活，新代投影（id 更大）生效。"""
        for row in self.projections:
            if row["subject_user_id"] == user_id:
                row["status"] = "inactive"
        self.add_candidate(user_id, profile_fields=new_fields)

    def seeded_snapshot(
        self,
        viewer_id: int,
        view_kind: str,
        targets: list[int],
        *,
        generation: int = 1,
        expires_at: datetime | None = None,
        engine: str = "rule-v1",
    ) -> None:
        for rank, target in enumerate(targets, start=1):
            self.snapshot_rows.append(
                {
                    "snapshot_id": f"rc_seed_{generation}",
                    "viewer": viewer_id,
                    "view_kind": view_kind,
                    "target": target,
                    "score": 90.0 - rank,
                    "coverage": 0.8,
                    "direction_json": None,
                    "score_detail_json": None,
                    "reason_codes": json.dumps(["INTEREST_OVERLAP"]),
                    "rank_no": rank,
                    "generation": generation,
                    "engine": engine,
                    "status": "ready",
                    "expires_at": expires_at,
                }
            )


class _RefreshSession:
    """按 SQL 子串路由到 _RefreshStore（同 test_ai_search.py 约定）。"""

    def __init__(self, store: _RefreshStore) -> None:
        self._store = store
        self.flushes = 0

    async def flush(self) -> None:
        self.flushes += 1

    async def execute(
        self, statement: Any, params: Any = None
    ) -> _MappingResult | _WriteResult:
        sql = str(statement)
        if isinstance(params, list):
            rowcount = 0
            for single in params:
                result = await self.execute(statement, single)
                rowcount += getattr(result, "rowcount", 1)
            return _WriteResult(rowcount=rowcount)
        values = dict(params or {})
        store = self._store

        # ---- 推荐快照：读 / 插入 / 顶代 / 取代数 ----
        if "FROM ai_recommendation_snapshot" in sql and "COALESCE(MAX(generation)" in sql:
            gen = max(
                (
                    int(row["generation"])
                    for row in store.snapshot_rows
                    if row["viewer"] == int(values["viewer"])
                    and row["view_kind"] == str(values["view_kind"])
                ),
                default=0,
            )
            return _MappingResult([{"gen": gen + 1}])
        if "INSERT INTO ai_recommendation_snapshot" in sql:
            row = dict(values)
            # _RECOMMEND_INSERT 将 status='ready'、calculated_at 硬编码在 SQL 中
            row["status"] = "ready"
            store.snapshot_rows.append(row)
            return _WriteResult(rowcount=1)
        if "SET status = 'superseded'" in sql:
            changed = 0
            for row in store.snapshot_rows:
                if (
                    row["viewer"] == int(values["viewer"])
                    and row["view_kind"] == str(values["view_kind"])
                    and int(row["generation"]) < int(values["generation"])
                    and row["status"] == "ready"
                ):
                    row["status"] = "superseded"
                    changed += 1
            return _WriteResult(rowcount=changed)
        if "FROM ai_recommendation_snapshot" in sql:
            key_view = int(values["viewer"])
            kind = str(values["view_kind"])
            rows = [
                {
                    # SELECT 列名与 INSERT 参数名的映射
                    "target_user_id": row["target"],
                    "score": row["score"],
                    "coverage": row["coverage"],
                    "rank_no": row["rank_no"],
                    "engine": row["engine"],
                    "reason_codes": row["reason_codes"],
                    "direction_json": row["direction_json"],
                }
                for row in store.snapshot_rows
                if row["viewer"] == key_view
                and row["view_kind"] == kind
                and row["status"] == "ready"
                and (row["expires_at"] is None or row["expires_at"] > datetime.now(UTC).replace(tzinfo=None))
            ]
            rows.sort(key=lambda r: int(r["rank_no"]))
            return _MappingResult(rows[: int(values["limit"])])

        # ---- 投影池（物化与读取期复验共享同一 WHERE 片段）----
        if "FROM ai_feature_projection p" in sql:
            id_list = None
            match = re.search(r"IN \(([\d,]+)\)", sql)
            if match:
                id_list = [int(item) for item in match.group(1).split(",")]
            rows = [
                {
                    "subject_user_id": row["subject_user_id"],
                    "projection_kind": row["projection_kind"],
                    "consent_snapshot_json": row["consent_snapshot_json"],
                    "id": row["id"],
                    "fields_json": row["fields_json"],
                }
                for row in store.projections
                if row["status"] == "active"
                and row["ps_status"] == "active"
                and row["expires_at"] is None
                and row["subject_user_id"] != int(values["viewer"])
                and (id_list is None or row["subject_user_id"] in id_list)
            ]
            rows.sort(key=lambda r: int(r["id"]), reverse=True)
            if ":limit" in sql:
                rows = rows[: int(values["limit"])]
            return _MappingResult(rows)

        # ---- 可见性 decide ----
        if "FROM users candidate" in sql:
            candidate = store.candidates.get(int(values["candidate_id"]))
            if candidate is None:
                return _MappingResult([])
            return _MappingResult([dict(candidate)])

        # ---- 授权 / 版本 ----
        if "FROM ai_consent_grant" in sql:
            rows = [
                row
                for row in store.consents
                if row["user_id"] == int(values["user_id"])
                and row["scope"] == values.get("scope")
                and row.get("revoked_at") is None
            ]
            return _MappingResult(rows[:1])
        if "FROM user_revision_state" in sql:
            row = store.revision_rows.get(int(values["user_id"]))
            return _MappingResult([row] if row else [])

        # ---- ai_task ----
        if "INSERT INTO ai_task" in sql:
            task_id = str(values["task_id"])
            # 全列存储（镜像 tasks.py 的 _task_row_to_record 契约）
            store.task_rows[task_id] = {
                "id": len(store.task_rows) + 1,
                "task_id": task_id,
                "owner_user_id": int(values["owner_user_id"]),
                "task_type": str(values["task_type"]),
                "scene": str(values.get("scene") or values["task_type"]),
                "idempotency_key": str(values["idempotency_key"]),
                "request_digest": values.get("request_digest"),
                "status": "queued",
                "stage": None,
                "progress_percent": None,
                "attempt_count": 0,
                "max_attempts": 3,
                "next_run_at": None,
                "lease_owner": None,
                "lease_until": None,
                "consent_snapshot_json": values.get("consent_snapshot_json"),
                "source_revision_json": values.get("source_revision_json"),
                "payload_summary": None,
                "error_code": None,
                "error_message": None,
                "result_ref": None,
                "created_at": None,
                "updated_at": None,
                "started_at": None,
                "finished_at": None,
            }
            return _WriteResult(rowcount=1)
        if "FROM ai_task" in sql and "task_id = :task_id" in sql:
            row = store.task_rows.get(str(values["task_id"]))
            return _MappingResult([row] if row else [])
        if "FROM ai_task" in sql and "idempotency_key = :idempotency_key" in sql:
            found = [
                row
                for row in store.task_rows.values()
                if row["owner_user_id"] == int(values["owner_user_id"])
                and row["task_type"] == str(values["task_type"])
                and row["idempotency_key"] == str(values["idempotency_key"])
            ]
            return _MappingResult(found[:1])

        raise AssertionError(f"unhandled sql: {sql}")


# ---------------------------------------------------------------------------
# 1. 物化池与读取期复验的等价性
# ---------------------------------------------------------------------------


async def test_pool_and_read_eligibility_are_equivalent(monkeypatch) -> None:
    """load_candidate_pool 与 _pool_eligible_candidate_ids 在同一事实库上
    产生相同用户集合（含 consent 失效、仅 ideal 投影、状态失活、viewer 自身）。"""
    store = _RefreshStore()
    store.add_candidate(201)  # 完整有效
    store.add_candidate(202, consent=_consent("other_scope"))  # 授权失效
    store.add_candidate(203)  # 后续失活
    for row in store.projections:
        if row["subject_user_id"] == 203:
            row["status"] = "inactive"
    # 204 只有 ideal 投影（personal 缺失）——两口径都必须排除
    store.candidates[204] = _visible_candidate_flags(204)
    store.projections.append(
        {
            "id": store._next_projection_id,
            "subject_user_id": 204,
            "projection_kind": "ideal_partner_preference",
            "consent_snapshot_json": _consent(),
            "status": "active",
            "ps_status": "active",
            "expires_at": None,
            "fields_json": {},
        }
    )
    store._next_projection_id += 1
    store.add_candidate(VIEWER)  # viewer 自身永不入池

    session = _RefreshSession(store)
    pool = await recommend_mod.load_candidate_pool(session, VIEWER, 50)
    pool_ids = {entry["user_id"] for entry in pool}
    eligible_ids = await recommend_mod._pool_eligible_candidate_ids(
        session, VIEWER, [201, 202, 203, 204, VIEWER]
    )

    assert pool_ids == {201}
    assert eligible_ids == {201}


# ---------------------------------------------------------------------------
# 2. 同日改资料的刷新时序（读取复用 → 入队幂等 → 重建顶替）
# ---------------------------------------------------------------------------


async def _empty_llm_directions(*_args: Any, **_kwargs: Any) -> dict[int, dict[str, Any]]:
    return {}


async def _inputs_loader(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    return _inputs()


def _rich_profile() -> dict[str, Any]:
    return {
        "age": 30,
        "city_code": "3100000",
        "interest_tags": ["hiking", "摄影"],
        "height_cm": 175,
        "education_level": 6,
        "marriage_status": 1,
        "relationship_goal": "dating",
    }


def _rich_preference() -> dict[str, Any]:
    return {
        "age": {"min": 25, "max": 35},
        "city_code": ["3100000"],
        "interest_tags": ["hiking"],
        "height_cm": {"min": 170, "max": 185},
        "education_level": {"min": 5},
        "marriage_status": 1,
        "relationship_goal": "dating",
    }


def _inputs() -> dict[str, Any]:
    return {
        "preference": _rich_preference(),
        "profile": _rich_profile(),
        "source_hash": "hash-v1",
    }


async def test_same_day_profile_change_refresh_timeline(monkeypatch) -> None:
    """生成 → 同日改资料 → 读取仍是旧分（保留政策）→ 入队同日一单 →
    重建后新代顶替、读取可见新分。不靠推断，逐步断言。"""
    store = _RefreshStore()
    store.add_candidate(201)
    store.add_candidate(202)
    store.revision_rows[VIEWER] = {
        "user_id": VIEWER,
        "profile_revision": 7,
        "preference_revision": 3,
        "privacy_revision": 1,
        "relationship_revision": 1,
        "policy_revision": 1,
    }
    monkeypatch.setattr(recommend_mod, "load_recommendation_inputs", _inputs_loader)
    monkeypatch.setattr(
        recommend_mod, "_load_fresh_llm_directions", _empty_llm_directions
    )
    session = _RefreshSession(store)

    # 1) 首次物化（worker 执行路径等价物）
    snapshot_id = await recommend_mod.materialize_recommendations(session, VIEWER)
    assert snapshot_id != ""
    gen1_scores = {
        int(row["target"]): float(row["score"])
        for row in store.snapshot_rows
        if row["generation"] == 1
        and row["status"] == "ready"
        and row["view_kind"] == "i_like"
    }
    assert set(gen1_scores) == {201, 202}

    # 2) 同日改资料：202 换城市重发布
    store.republish_candidate(202, {**_rich_profile(), "city_code": "4401000"})

    # 3) 读取入口：缓存保留期内仍返回旧分（无逐读重算——契约明确）
    items = await recommend_mod.read_recommendations(session, VIEWER, "i_like", 20)
    old_scores = {item["target_user_id"]: item["score"] for item in items}
    assert old_scores == gen1_scores

    # 4) 同日入队：两次调用同一 task（确定性验证，非推断）
    task_a = await recommend_mod.enqueue_recommendation_rebuild(session, VIEWER)
    assert task_a is not None
    task_b = await recommend_mod.enqueue_recommendation_rebuild(session, VIEWER)
    assert task_b is not None
    assert str(getattr(task_a, "task_id", "")) == str(getattr(task_b, "task_id", ""))
    same_day_tasks = [
        row
        for row in store.task_rows.values()
        if row["task_type"] == "recommend_rebuild"
    ]
    assert len(same_day_tasks) == 1
    assert same_day_tasks[0]["idempotency_key"].endswith(
        datetime.now(UTC).strftime("%Y%m%d")
    )

    # 5) 重建执行（worker 领取后的物化等价物）：新代顶替旧代
    snapshot_id_2 = await recommend_mod.materialize_recommendations(session, VIEWER)
    assert snapshot_id_2 != snapshot_id
    ready_rows = [row for row in store.snapshot_rows if row["status"] == "ready"]
    assert {int(row["generation"]) for row in ready_rows} == {2}
    gen2_202 = next(
        float(row["score"])
        for row in ready_rows
        if int(row["target"]) == 202 and row["view_kind"] == "i_like"
    )
    assert gen2_202 != gen1_scores[202]

    # 6) 读取入口：新分可见
    items_after = await recommend_mod.read_recommendations(session, VIEWER, "i_like", 20)
    new_scores = {item["target_user_id"]: item["score"] for item in items_after}
    assert new_scores[202] == gen2_202


# ---------------------------------------------------------------------------
# 3. 请求人授权链：撤回后缓存保留、过期后拒绝入队
# ---------------------------------------------------------------------------


async def test_viewer_consent_revocation_chain(monkeypatch) -> None:
    store = _RefreshStore()
    store.add_candidate(201)
    monkeypatch.setattr(recommend_mod, "load_recommendation_inputs", _inputs_loader)
    monkeypatch.setattr(
        recommend_mod, "_load_fresh_llm_directions", _empty_llm_directions
    )
    session = _RefreshSession(store)

    await recommend_mod.materialize_recommendations(session, VIEWER)

    # 撤回请求人授权：缓存保留期内旧快照仍可读（保留政策，不逐读重算）
    store.consents = []
    items = await recommend_mod.read_recommendations(session, VIEWER, "i_like", 20)
    assert [item["target_user_id"] for item in items] == [201]

    # 快照过期 → miss → 入队被既有授权检查拒绝（任务不创建）
    for row in store.snapshot_rows:
        row["expires_at"] = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
    items_after = await recommend_mod.read_recommendations(session, VIEWER, "i_like", 20)
    assert items_after == []
    task = await recommend_mod.enqueue_recommendation_rebuild(session, VIEWER)
    assert task is None
    assert store.task_rows == {}


# ---------------------------------------------------------------------------
# 4. 连续失效候选：短页继续取到后段有效候选
# ---------------------------------------------------------------------------


async def test_consecutive_invalidated_candidates_still_fill_page() -> None:
    store = _RefreshStore()
    targets = [201, 202, 203, 204, 205]
    for user_id in targets:
        store.candidates[user_id] = _visible_candidate_flags(user_id)
        store.add_candidate(user_id)
    store.seeded_snapshot(VIEWER, "i_like", targets)
    session = _RefreshSession(store)

    # 201/202/203 连续失效（拉黑+撤权+投影失效各一）
    store.candidates[201]["blocked"] = True
    for row in store.projections:
        if row["subject_user_id"] == 202:
            row["consent_snapshot_json"] = _consent("other_scope")
    store.projections = [
        row for row in store.projections if row["subject_user_id"] != 203
    ]

    items = await recommend_mod.read_recommendations(session, VIEWER, "i_like", 2)

    assert [item["target_user_id"] for item in items] == [204, 205]
    assert [item["rank_no"] for item in items] == [4, 5]

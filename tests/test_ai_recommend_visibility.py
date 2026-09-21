"""推荐读取期可见性/资格复验时序测试（WP 读取期门禁）。

时序语义：先按"物化完成"seed 快照行（视为 materialize_recommendations 的
产物），再变更候选侧状态（双向拉黑/隐藏/封禁/授权撤回/投影失效），随后
调用 ``read_recommendations``，断言不应展示的候选不再出现在任何返回卡片
与解释中。可见性语义本身（decide 真值表）由 tests/test_candidate_visibility.py
冻结；本文件用与 test_ai_search.py 相同的 fake-session SQL 路由约定，把
decide 行镜像为内存候选标志，锁定的是"读取路径确实复检、且发生在返回之前"。
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from app.services.ai import recommend as recommend_mod

pytestmark = pytest.mark.asyncio


class _MappingResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> "_MappingResult":
        return self

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


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


class _RecommendReadStore:
    """推荐读取链路的内存事实库：快照行 + 候选可见性 + 投影/授权。"""

    def __init__(self) -> None:
        # (viewer, view_kind) -> ready 快照行（视为已物化代）
        self.snapshot_rows: dict[tuple[int, str], list[dict[str, Any]]] = {}
        self.candidates: dict[int, dict[str, Any]] = {}
        self.projections: list[dict[str, Any]] = []

    def seed_materialized(
        self,
        viewer_id: int,
        view_kind: str,
        targets: list[int],
        *,
        engine: str = "rule-v1",
    ) -> None:
        self.snapshot_rows[(viewer_id, view_kind)] = [
            {
                "target_user_id": target,
                "score": 90.0 - rank,
                "coverage": 0.8,
                "rank_no": rank,
                "engine": engine,
                "reason_codes": json.dumps(["INTEREST_OVERLAP"]),
                "direction_json": json.dumps(
                    {"score": 90.0 - rank, "reason_texts": [f"理由{rank}"]},
                    ensure_ascii=False,
                ),
            }
            for rank, target in enumerate(targets, start=1)
        ]

    def seed_valid_candidate(self, user_id: int) -> None:
        self.candidates[user_id] = _visible_candidate_flags(user_id)
        self.projections.append(
            {
                "subject_user_id": user_id,
                "projection_kind": "personal_compatibility",
                "consent_snapshot_json": _consent(),
                "status": "active",
                "ps_status": "active",
                "expires_at": None,
            }
        )
        self.projections.append(
            {
                "subject_user_id": user_id,
                "projection_kind": "ideal_partner_preference",
                "consent_snapshot_json": _consent(),
                "status": "active",
                "ps_status": "active",
                "expires_at": None,
            }
        )

    def block(self, user_id: int) -> None:
        self.candidates[user_id]["blocked"] = True

    def hide(self, user_id: int) -> None:
        self.candidates[user_id]["profile_visible"] = False

    def ban(self, user_id: int) -> None:
        self.candidates[user_id]["not_restricted"] = False

    def withdraw_consent(self, user_id: int) -> None:
        for row in self.projections:
            if row["subject_user_id"] == user_id:
                row["consent_snapshot_json"] = _consent("other_scope")

    def invalidate_projection(self, user_id: int) -> None:
        self.projections = [
            row
            for row in self.projections
            if row["subject_user_id"] != user_id
        ]


class _FakeReadSession:
    """按 SQL 子串路由到 _RecommendReadStore（同 test_ai_search.py 约定）。"""

    def __init__(self, store: _RecommendReadStore) -> None:
        self._store = store

    async def execute(self, statement: Any, params: Any = None) -> _MappingResult:
        sql = str(statement)
        values = dict(params or {})
        store = self._store

        if "FROM ai_recommendation_snapshot" in sql:
            key = (int(values["viewer"]), str(values["view_kind"]))
            rows = sorted(
                store.snapshot_rows.get(key, []), key=lambda r: int(r["rank_no"])
            )
            return _MappingResult(rows[: int(values["limit"])])

        if "FROM ai_feature_projection p" in sql:
            id_list = [
                int(item)
                for item in re.search(r"IN \(([\d,]+)\)", sql).group(1).split(",")
            ]
            rows = [
                {
                    "subject_user_id": row["subject_user_id"],
                    "projection_kind": row["projection_kind"],
                    "consent_snapshot_json": row["consent_snapshot_json"],
                }
                for row in store.projections
                if row["subject_user_id"] in id_list
                and row["subject_user_id"] != int(values["viewer"])
                and row["status"] == "active"
                and row["ps_status"] == "active"
                and row["expires_at"] is None
            ]
            return _MappingResult(rows)

        if "FROM users candidate" in sql:
            candidate = store.candidates.get(int(values["candidate_id"]))
            if candidate is None:
                return _MappingResult([])
            return _MappingResult([dict(candidate)])

        raise AssertionError(f"unhandled sql: {sql}")


VIEWER = 101
CANDIDATES = [201, 202, 203]


def _seed_three_valid(store: _RecommendReadStore) -> None:
    for user_id in CANDIDATES:
        store.seed_valid_candidate(user_id)
    store.seed_materialized(VIEWER, "i_like", CANDIDATES)


async def _read(store: _RecommendReadStore, limit: int = 20) -> list[dict[str, Any]]:
    return await recommend_mod.read_recommendations(
        _FakeReadSession(store), VIEWER, "i_like", limit
    )


async def test_valid_candidates_return_in_rank_order_with_payload() -> None:
    store = _RecommendReadStore()
    _seed_three_valid(store)

    items = await _read(store)

    assert [item["target_user_id"] for item in items] == CANDIDATES
    assert [item["rank_no"] for item in items] == [1, 2, 3]
    assert items[0]["reason_texts"] == ["理由1"]
    assert items[0]["reason_codes"] == ["INTEREST_OVERLAP"]
    assert items[0]["engine"] == "rule-v1"
    assert items[0]["score"] == 89.0


async def test_candidate_blocked_after_materialization_disappears() -> None:
    """双向拉黑（任一方向）在读取期即时生效，正常候选不受影响。"""
    store = _RecommendReadStore()
    _seed_three_valid(store)
    store.block(202)  # decide 的 blocked 覆盖 viewer→candidate 与 candidate→viewer

    items = await _read(store)

    assert [item["target_user_id"] for item in items] == [201, 203]


async def test_hidden_candidate_disappears() -> None:
    store = _RecommendReadStore()
    _seed_three_valid(store)
    store.hide(201)  # show_profile=0

    items = await _read(store)

    assert [item["target_user_id"] for item in items] == [202, 203]


async def test_banned_candidate_disappears() -> None:
    store = _RecommendReadStore()
    _seed_three_valid(store)
    store.ban(203)  # TOTAL_BAN 生效中

    items = await _read(store)

    assert [item["target_user_id"] for item in items] == [201, 202]


async def test_consent_withdrawn_candidate_disappears() -> None:
    """``profile_text_extract`` 撤回后其分数/解释不再返回。"""
    store = _RecommendReadStore()
    _seed_three_valid(store)
    store.withdraw_consent(202)

    items = await _read(store)

    assert [item["target_user_id"] for item in items] == [201, 203]


async def test_projection_invalidated_candidate_disappears() -> None:
    """投影失效（删除/状态非 active）的候选即时从列表消失。"""
    store = _RecommendReadStore()
    _seed_three_valid(store)
    store.invalidate_projection(203)

    items = await _read(store)

    assert [item["target_user_id"] for item in items] == [201, 202]


async def test_all_candidates_filtered_returns_empty_page() -> None:
    store = _RecommendReadStore()
    _seed_three_valid(store)
    for user_id in CANDIDATES:
        store.block(user_id)

    items = await _read(store)

    assert items == []


async def test_page_fills_next_visible_candidate_and_reads_are_stable() -> None:
    """短页填补：被过滤行由后续可见候选顶上；连续两次读取结果一致。"""
    store = _RecommendReadStore()
    targets = [201, 202, 203, 204]
    for user_id in targets:
        store.seed_valid_candidate(user_id)
    store.seed_materialized(VIEWER, "i_like", targets)
    store.block(202)

    first = await _read(store, limit=2)
    second = await _read(store, limit=2)

    assert [item["target_user_id"] for item in first] == [201, 203]
    assert [item["rank_no"] for item in first] == [1, 3]
    assert first == second

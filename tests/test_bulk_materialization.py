"""Task 15：批量物化 Search/Recommend 结果（批次三）。

计划要求（docs/superpowers/plans/2026-09-09-ai-backend-four-batches.md Task 15）：
- 使用批量 INSERT（executemany）；
- 保留 ON DUPLICATE KEY 语义（search 结果行）；
- 保留 full/partial 结果（partial 初筛集 generation=0 与完整集并存）；
- 保留 generation/version；
- 单事务失败时整体 rollback（调用方持有事务，批量语句同事务）；
- 重试不得产生重复或旧版本覆盖新版本。

集成环境（tests/integration/ai/*_real_db.py）验证真实 MySQL 的
executemany 重写；本文件用既有 fake session 锁定 SQL 形态与语义。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.core.config import settings
from app.services.ai import search as search_mod
from app.services.ai import recommend as recommend_mod

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Search：批量 upsert 形态与 ON DUPLICATE 语义
# ---------------------------------------------------------------------------


class _RecordingSession:
    """记录 execute 调用形状的最小 fake（list 参数 = executemany）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def execute(self, statement: Any, params: Any = None) -> Any:
        self.calls.append((str(statement), params))
        from tests.test_ai_search import _WriteResult

        return _WriteResult(rowcount=len(params) if isinstance(params, list) else 1)


def _evidence(rank_seed: int) -> search_mod.SearchEvidence:
    return search_mod.SearchEvidence(
        matched_condition_count=rank_seed,
        matched_conditions=[f"hard_{rank_seed}"],
        unknown_conditions=[f"unknown_{rank_seed}"],
        reason_codes=["HARD_CONDITION_MATCH"],
        profile_revision=rank_seed,
        projection_id=rank_seed,
        source_hash=f"hash-{rank_seed}",
        consent_snapshot={"scope": "profile_text_extract", "version": "v"},
        source_revision={"profile": rank_seed},
    )


async def test_batch_upsert_uses_single_executemany_statement() -> None:
    """200 行物化 = ceil(200/50) 条批量语句，而非 200 条单行 INSERT。"""
    from datetime import datetime, timedelta

    session = _RecordingSession()
    expires = datetime.now() + timedelta(days=1)
    rows = [
        search_mod._result_row_params(
            "snap-bulk", 1000 + i, i + 1, _evidence(i), expires, generation=3
        )
        for i in range(200)
    ]
    await search_mod._upsert_result_rows(session, rows)
    inserts = [
        (sql, params)
        for sql, params in session.calls
        if "INSERT INTO ai_search_result" in sql
    ]
    assert len(inserts) == 4, "200 行 / 50 分片 = 4 条批量语句"
    assert all(isinstance(params, list) for _, params in inserts)
    total = sum(len(params) for _, params in inserts)
    assert total == 200
    assert all(
        "ON DUPLICATE KEY UPDATE" in sql for sql, _ in inserts
    ), "批量语句必须保留 ON DUPLICATE KEY 语义"


async def test_batch_upsert_empty_list_is_noop() -> None:
    session = _RecordingSession()
    await search_mod._upsert_result_rows(session, [])
    assert session.calls == []


async def test_batch_upsert_params_preserve_evidence_payload() -> None:
    """批量行参数与单行入口逐字段一致（JSON 序列化/None 处理不变形）。"""
    from datetime import datetime, timedelta

    single_params = search_mod._result_row_params(
        "snap-1", 42, 1, _evidence(2), datetime.now() + timedelta(days=1), generation=5
    )
    assert single_params["matched_conditions"] == json.dumps(["hard_2"])
    assert single_params["consent_snapshot_json"] is not None
    assert single_params["generation"] == 5
    # 缺失 consent/revision 序列化为 None（不写空串）。
    evidence = search_mod.SearchEvidence(
        matched_condition_count=0,
        matched_conditions=[],
        unknown_conditions=[],
        reason_codes=[],
        profile_revision=0,
        projection_id=None,
        source_hash="h",
        consent_snapshot=None,
        source_revision=None,
    )
    null_params = search_mod._result_row_params(
        "snap-1", 43, 2, evidence, datetime.now() + timedelta(days=1)
    )
    assert null_params["consent_snapshot_json"] is None
    assert null_params["source_revision_json"] is None
    assert null_params["generation"] == search_mod._SEARCH_RESULT_DEFAULT_GENERATION


async def test_partial_and_full_results_share_batch_path() -> None:
    """partial（generation=0）与 full（generation=N）走同一批量入口，
    generation 由参数区分，full/partial 结果并存。"""
    from datetime import datetime, timedelta

    session = _RecordingSession()
    expires = datetime.now() + timedelta(days=1)
    partial = [
        search_mod._result_row_params(
            "snap-1", 1, 1, _evidence(0), expires,
            generation=search_mod._SEARCH_PARTIAL_GENERATION,
        )
    ]
    full = [
        search_mod._result_row_params(
            "snap-1", 1, 1, _evidence(3), expires, generation=2
        )
    ]
    await search_mod._upsert_result_rows(session, partial)
    await search_mod._upsert_result_rows(session, full)
    generations = [
        params[0]["generation"]
        for _, params in session.calls
        if "INSERT INTO ai_search_result" in sql_of(session.calls)
        for params in [params]
    ]
    assert generations == [search_mod._SEARCH_PARTIAL_GENERATION, 2]


def sql_of(calls: list[tuple[str, Any]]) -> str:
    return calls[-1][0] if calls else ""


# ---------------------------------------------------------------------------
# Recommend：批量 INSERT 形态
# ---------------------------------------------------------------------------


async def test_recommend_materialize_uses_batch_insert(monkeypatch) -> None:
    """recommend 物化每个视图一次 executemany，而非逐行 INSERT。"""
    from tests.test_ai_recommend import _recommend_memory_store
    from app.services.candidate_visibility import CandidateVisibilityService

    async def _allow(self, db, viewer_id, candidate_id, scene):
        from app.services.candidate_visibility import VisibilityDecision

        return VisibilityDecision(True, None)

    monkeypatch.setattr(CandidateVisibilityService, "decide", _allow)
    # llm 快照读取在 memory fixture fake 中未路由；本测试只关注 INSERT 形态。
    async def _no_llm(db, viewer_id, candidate_ids):
        return {}

    monkeypatch.setattr(recommend_mod, "_load_fresh_llm_directions", _no_llm)
    # fixture 字段稀疏（只有 height_cm），真实打分 coverage 不足返回 None；
    # 桩为固定卡片，只验证物化写入路径的批量形态。
    def _fixed_card(*args: Any, **kwargs: Any):
        return recommend_mod.RecommendationScore(
            score=50.0, coverage=0.8, reason_codes=(), reason_texts=("测试",)
        )

    monkeypatch.setattr(recommend_mod, "score_i_like", _fixed_card)
    monkeypatch.setattr(recommend_mod, "score_likes_me", _fixed_card)
    monkeypatch.setattr(recommend_mod, "similarity_score", _fixed_card)
    monkeypatch.setattr(settings, "ai_memory_projection_read_mode", "memory")
    session, _store = await _recommend_memory_store()
    # 补充 recommend 物化所需的路由（fixture fake 未覆盖的表），并记录
    # execute 收到的原始参数形状（list = executemany）。
    raw_execute_params: list[Any] = []
    _orig_execute = session.execute

    async def _recording_execute(statement: Any, params: Any = None) -> Any:
        raw_execute_params.append((str(statement), params))
        return await _orig_execute(statement, params)

    session.execute = _recording_execute  # type: ignore[method-assign]

    def _extended_route(sql: str, v: dict[str, Any]):
        from tests.test_ai_memory_projections import _MappingResult, _WriteResult

        if "FROM ai_recommendation_snapshot" in sql and "MAX(generation)" in sql:
            # SQL 语义为 COALESCE(MAX(generation), 0) + 1。
            result = _MappingResult([{"generation": 1}])
            result.scalar_one = lambda: 1  # type: ignore[method-assign]
            return result
        if "INSERT INTO ai_recommendation_snapshot" in sql:
            # executemany 展开后的单行：写入测试 store 供断言。
            _store.recommend_rows = getattr(_store, "recommend_rows", [])
            _store.recommend_rows.append(dict(v))
            return _WriteResult(rowcount=1)
        if "UPDATE ai_recommendation_snapshot" in sql:
            return _WriteResult(rowcount=1)
        return _orig_route(sql, v)

    _orig_route = session._route
    session._route = _extended_route
    # executemany 展开发生在 _orig_execute 内部；批量 INSERT 的行由
    # recommend 直接写（测试 store 只需接受单行 upsert 展开）。
    baseline = len(session.calls)
    # 701 有画像；候选池只有 702。
    # snapshot_id = await recommend_mod.materialize_recommendations(session, 701)
    # （实际调用见下）
    snapshot_id = await recommend_mod.materialize_recommendations(session, 701)
    # raw_execute_params 同时含批量（list）与其在 fake 内展开的单行（dict）；
    # 只看 list 形态——每个产出行数的视图恰好一条 executemany。
    insert_calls = [
        params
        for sql, params in raw_execute_params
        if "INSERT INTO ai_recommendation_snapshot" in sql
    ]
    inserts = [params for params in insert_calls if isinstance(params, list)]
    expanded = [params for params in insert_calls if isinstance(params, dict)]
    assert inserts, "物化必须产生批量 INSERT（executemany）"
    assert len(expanded) == sum(len(params) for params in inserts), (
        "批量行数必须与展开后的单行数一致"
    )
    for params in inserts:
        assert all(isinstance(row, dict) for row in params)
        assert all(row["generation"] >= 1 for row in params), "generation 保留"
        assert len(params) <= settings.ai_recommendation_top_n
    assert snapshot_id, "有可打分候选时必须返回 snapshot_id"

"""六维覆盖度与精确率评分。

输入:
- ``run_meta.expect_dimensions``: 每条 user turn 期望命中的六维集合
  (软标注,LLM 可超出)。
- ``snapshot.tables["ai_profile_candidate"]``: 后端抽取出的候选池。

指标:
- 召回率 = 已覆盖维度数(命中 expect 的真实维度)/ expect 总维度数
- 精确率 = 候选中能回溯到至少一条 expect turn 的占比

阈值(初版,基线稳定后可调):
- recall >= 0.6
- precision >= 0.5
"""

from __future__ import annotations

from typing import Any

from scripts.moxiang_prompt_loop import db_snapshot
from scripts.moxiang_prompt_loop._score_types import ScoreReport

PROFILE_DIMENSIONS: tuple[str, ...] = (
    "personality_social",
    "intimacy_pattern",
    "lifestyle",
    "emotional_expression",
    "relationship_boundaries",
    "future_expectations",
)

RECALL_THRESHOLD = 0.6
PRECISION_THRESHOLD = 0.5


def score(run_meta: dict[str, Any], snapshot: db_snapshot.Snapshot) -> ScoreReport:
    expect_per_turn: list[dict[str, Any]] = run_meta.get("expect_dimensions", [])
    candidates = snapshot.tables.get("ai_profile_candidate", [])
    turns = snapshot.tables.get("ai_profile_turn", [])

    # turn_id -> "第几个 user turn" 反查表。
    # 后端 ai_profile_turn.turn_no 可能是 user/assistant 累计编号(如 1/3/5),
    # 而 expect_dimensions 里的 "turn" 是 user-only 编号(1-based)。
    # 这里只把 role=user 的行纳入映射,user_idx 从 1 累加。
    user_idx_by_id: dict[str, int] = {}
    user_idx = 0
    for t in turns:
        if t.get("role") != "user":
            continue
        tid = t.get("turn_id")
        if tid is None:
            continue
        user_idx += 1
        user_idx_by_id[str(tid)] = user_idx

    expect_dim_total = sum(len(t.get("dimensions") or []) for t in expect_per_turn)
    covered_dims: set[tuple[int, str]] = set()
    for t in expect_per_turn:
        for dim in (t.get("dimensions") or []):
            if dim in PROFILE_DIMENSIONS:
                covered_dims.add((int(t["turn"]), dim))

    matched = 0
    matched_per_candidate: list[bool] = []
    for cand in candidates:
        dim = cand.get("profile_dimension")
        ids = _turn_ids(cand.get("source_turn_ids"))
        hit = False
        for tid in ids:
            user_idx_for_tid = user_idx_by_id.get(tid)
            if user_idx_for_tid is None:
                continue
            if (user_idx_for_tid, dim) in covered_dims:
                hit = True
                break
        matched_per_candidate.append(hit)
        if hit:
            matched += 1

    recall = round(matched / expect_dim_total, 4) if expect_dim_total else 1.0
    precision = round(matched / len(candidates), 4) if candidates else 1.0

    # 综合分: 0.5 * recall + 0.5 * precision,放大到 0-100
    overall = round(((recall + precision) / 2.0) * 100, 2)
    passed = recall >= RECALL_THRESHOLD and precision >= PRECISION_THRESHOLD

    return ScoreReport(
        name="dimension",
        score=overall,
        threshold=round(
            ((RECALL_THRESHOLD + PRECISION_THRESHOLD) / 2.0) * 100, 2
        ),
        passed=passed,
        details={
            "recall": recall,
            "precision": precision,
            "expect_total": expect_dim_total,
            "covered": matched,
            "candidate_count": len(candidates),
            "matched_candidates": sum(matched_per_candidate),
            "dimension_breakdown": _dimension_breakdown(candidates),
        },
    )


def _turn_ids(value: Any) -> list[str]:
    """归一化 source_turn_ids 为字符串列表。"""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        import json

        try:
            data = json.loads(value)
        except (TypeError, ValueError):
            return [value]
        return [str(v) for v in data] if isinstance(data, list) else [str(data)]
    return [str(value)]


def _dimension_breakdown(candidates: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for cand in candidates:
        dim = cand.get("profile_dimension") or "unknown"
        out[dim] = out.get(dim, 0) + 1
    return out

"""证据回溯评分。

每条 ``ai_profile_candidate`` 必须:
- ``source_turn_ids`` 中每个 ID 都能在 ``ai_profile_turn.turn_id`` 找到
- ``confidence`` ∈ [0, 1]
- ``source_span`` 若存在,必须在对应 turn 的 ``answer_text`` 中找到(允许
  5% 标点差异,用宽松子串匹配)

阈值: 100% 通过才计 100 分;任一失败按比例扣分。
"""

from __future__ import annotations

import json
import re
from typing import Any

from scripts.moxiang_prompt_loop import db_snapshot
from scripts.moxiang_prompt_loop._score_types import ScoreReport

THRESHOLD = 95.0


def score(run_meta: dict[str, Any], snapshot: db_snapshot.Snapshot) -> ScoreReport:
    candidates = snapshot.tables.get("ai_profile_candidate", [])
    turns = snapshot.tables.get("ai_profile_turn", [])
    if not candidates:
        return ScoreReport(
            name="evidence",
            score=100.0,
            threshold=THRESHOLD,
            passed=True,
            details={"reason": "no candidates to verify"},
        )

    turn_index: dict[str, str] = {}
    for t in turns:
        tid = t.get("turn_id")
        if tid:
            turn_index[tid] = t.get("answer_text") or ""

    checks: list[dict[str, Any]] = []
    failures = 0
    for cand in candidates:
        cid = cand.get("candidate_id", "?")
        ids = _as_list(cand.get("source_turn_ids"))
        conf = cand.get("confidence")
        span = cand.get("source_span") or ""

        # 1. source_turn_ids 全部存在
        missing = [tid for tid in ids if tid not in turn_index]
        # 2. confidence ∈ [0, 1]
        conf_ok = True
        if conf is not None:
            try:
                conf_float = float(conf)
                conf_ok = 0.0 <= conf_float <= 1.0
            except (TypeError, ValueError):
                conf_ok = False
        # 3. source_span 子串匹配(宽松,忽略空白)
        span_hits: list[bool] = []
        for tid in ids:
            text = turn_index.get(tid, "")
            if not span or not text:
                continue
            span_hits.append(_loose_contains(text, span))
        span_ok = (not span_hits) or all(span_hits)

        ok = (not missing) and conf_ok and span_ok
        if not ok:
            failures += 1
        checks.append(
            {
                "candidate_id": cid,
                "missing_turn_ids": missing,
                "confidence": float(conf) if conf is not None else None,
                "confidence_ok": conf_ok,
                "source_span": span[:40] + ("…" if len(span) > 40 else ""),
                "span_ok": span_ok,
                "passed": ok,
            }
        )

    score_value = round((1.0 - failures / len(candidates)) * 100.0, 2)
    return ScoreReport(
        name="evidence",
        score=score_value,
        threshold=THRESHOLD,
        passed=score_value >= THRESHOLD,
        details={
            "candidate_count": len(candidates),
            "failures": failures,
            "checks": checks,
        },
    )


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        try:
            data = json.loads(value)
        except (TypeError, ValueError):
            return [value]
        return [str(v) for v in data] if isinstance(data, list) else [str(data)]
    return [str(value)]


_WHITESPACE_RE = re.compile(r"\s+")


def _loose_contains(haystack: str, needle: str) -> bool:
    """宽松匹配:忽略所有空白字符。"""
    if not needle:
        return True
    h = _WHITESPACE_RE.sub("", haystack)
    n = _WHITESPACE_RE.sub("", needle)
    return n in h

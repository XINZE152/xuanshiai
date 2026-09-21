"""离线评分对照工具的口径回归锁定（第四批 C2/C3/C5）。

工具本身只调用现有 compute 纯函数；这里锁定其输出行的关键不变量：
覆盖率 0.50 阈值边界、真实零分与缺失的区分、方向互换对称性、行字段完备性。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.compare_scores_offline import (  # noqa: E402
    _AI_EDU_TO_STORAGE,
    build_rows,
    _semantic_correspondence_checks,
)

REQUIRED_FIELDS = (
    "sample_id",
    "legacy_display_score",
    "legacy_display_score_swapped",
    "legacy_reason",
    "dir_a2b",
    "dir_b2a",
    "pair_score",
    "cov_a2b",
    "cov_b2a",
    "coverage",
    "status",
    "version_legacy",
    "version_pair",
    "missing_reasons",
    "notes",
)


def _row(sample_id: str) -> dict:
    rows = {row["sample_id"]: row for row in build_rows()}
    assert set(rows) == {
        "complete_pair",
        "asymmetric_preference",
        "explicit_unrestricted",
        "missing_fields",
        "real_zero",
        "coverage_boundary_050",
        "coverage_below_threshold",
        "direction_swap",
    }
    return rows[sample_id]


def test_rows_carry_all_contract_fields() -> None:
    for row in build_rows():
        for field in REQUIRED_FIELDS:
            assert field in row, f"{row['sample_id']} 缺字段 {field}"
        assert row["version_legacy"] == "legacy-rule-v1"
        assert row["version_pair"] == "compatibility-rule-v2"


def test_coverage_boundary_exactly_050_is_ready() -> None:
    row = _row("coverage_boundary_050")
    assert row["cov_a2b"] == 0.5 and row["cov_b2a"] == 0.5
    assert row["status"] == "ready" and row["pair_score"] is not None


def test_coverage_below_threshold_is_blocked() -> None:
    row = _row("coverage_below_threshold")
    assert row["cov_a2b"] < 0.5
    assert row["status"] == "coverage_insufficient"
    assert "COVERAGE_INSUFFICIENT" in row["missing_reasons"]


def test_real_zero_is_scored_zero_not_unknown() -> None:
    """学历硬冲突：方向分必须给 0（维度可用），不得与 DIMENSION_UNKNOWN 混淆。"""
    row = _row("real_zero")
    assert row["dir_a2b"] is not None and row["dir_a2b"] < 100.0
    assert row["dir_b2a"] == 100.0
    # a→b 方向的分低于满分 = 学历维度计了 0 分而非缺失。


def test_missing_fields_carry_unknown_reason() -> None:
    row = _row("missing_fields")
    assert "DIMENSION_UNKNOWN" in row["missing_reasons"]
    assert row["status"] == "coverage_insufficient"


def test_direction_swap_keeps_pair_score_symmetric() -> None:
    straight = _row("complete_pair")
    swapped = _row("direction_swap")
    assert swapped["pair_score"] == straight["pair_score"]
    assert swapped["dir_a2b"] == straight["dir_b2a"]
    assert swapped["dir_b2a"] == straight["dir_a2b"]


def test_semantic_correspondence_maps_hold() -> None:
    """raw 抽取刻度 → 存储域映射与 AI 学历方向语义（C4 修复的回归面）。"""
    for check in _semantic_correspondence_checks():
        assert check["map_holds"], check
        assert check["compat_pref_min_ai4_accepts"], check
    assert _AI_EDU_TO_STORAGE[4] == 3, "AI 本科(4) 必须映射存储域本科(3)"

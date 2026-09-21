"""四个评分器的统一接口 + 单点调用入口。

每个 scorer 接受 ``run_meta``(transcript + 回放事件流)与
``snapshot``(db_snapshot.Snapshot),返回 :class:`ScoreReport`。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from scripts.moxiang_prompt_loop import db_snapshot
from scripts.moxiang_prompt_loop._score_types import ScoreReport
from scripts.moxiang_prompt_loop.scorers import (
    dimension,
    evidence,
    state_dedup,
    style_safety,
)


@dataclass
class ScoreBundle:
    """四维度汇总。"""

    reports: list[ScoreReport] = field(default_factory=list)

    @property
    def total(self) -> float:
        if not self.reports:
            return 0.0
        return round(sum(r.score for r in self.reports) / len(self.reports), 2)

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.reports)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "all_passed": self.all_passed,
            "reports": [r.to_dict() for r in self.reports],
        }


def run_all(
    run_meta: dict[str, Any],
    snapshot: db_snapshot.Snapshot,
) -> ScoreBundle:
    """按设计稿顺序跑四个 scorer。"""
    bundle = ScoreBundle()
    bundle.reports.append(dimension.score(run_meta, snapshot))
    bundle.reports.append(style_safety.score(run_meta, snapshot))
    bundle.reports.append(evidence.score(run_meta, snapshot))
    bundle.reports.append(state_dedup.score(run_meta, snapshot))
    return bundle

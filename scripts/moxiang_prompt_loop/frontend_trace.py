"""前端 dev-only trace 导出。

把 :class:`ReplayResult` 转成前端折叠层消费的 JSON,写到
``xuanshiai-vue/static/dev/moxiang-trace.json``。
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.moxiang_prompt_loop.ws_client import ReplayResult


def export_trace(
    result: ReplayResult,
    score_total: float,
    out_path: Path,
    score_breakdown: dict[str, float] | None = None,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = result.to_frontend_trace(run_id=out_path.stem)
    payload["summary"] = {
        "score_total": score_total,
        "score_breakdown": score_breakdown or {},
    }
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

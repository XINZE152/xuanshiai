"""按版本聚合画像指标（只读，不写任何新列）。

指标完全来自已冻结的 ``ai_profile_revision_field``，不依赖快照表或加列。
六维覆盖度沿用共享的 ``calculate_journey_progress`` 口径，保证与既有
进度面板一致；其余计数均为库内真实行数，无出处的数值一律返回 None。
"""

from __future__ import annotations

from typing import Any

from app.schemas.ai_moxiang import CandidateRecord
from app.services.ai.journey_progress import calculate_journey_progress


def compute_metric_snapshot(fields: list[dict[str, Any]]) -> dict[str, Any]:
    """从某一版本的冻结字段计算一次指标；字段为空时返回空维度。"""
    records = [
        CandidateRecord(
            candidate_id=str(field.get("field_key") or ""),
            session_id="",
            user_id=0,
            subject=str(field.get("subject") or ""),
            profile_dimension=str(field.get("profile_dimension") or ""),
            field_kind=str(field.get("field_kind") or "structured"),
            field_key=field.get("field_key"),
            category=field.get("category"),
            content=field.get("content"),
            value=None,
            confidence=float(field.get("confidence") or 0),
            source_turn_ids=(),
            source_span=None,
            consent_version="",
            policy_revision="",
            status="promoted",
            content_hash=str(field.get("content_hash") or ""),
        )
        for field in fields
        if field.get("content_hash")
    ]
    progress = calculate_journey_progress(records)
    return {
        "overall_percent": progress.overall_percent,
        "confirmed_field_count": sum(
            f.get("field_kind") == "structured" for f in fields
        ),
        "entry_count": sum(f.get("field_kind") == "entry" for f in fields),
        "dimensions": [
            {"key": key, "percent": value.percent, "evidence_count": value.evidence_count}
            for key, value in progress.dimensions.items()
        ],
    }

"""Owner-only published profile metrics history.

按版本实时聚合 ``ai_profile_revision`` + ``ai_profile_revision_field``，
不依赖任何新增列、快照表或迁移。旧版本同样能返回真实统计。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, get_current_user
from app.api.routes.ai_moxiang import _error_response
from app.core.config import settings
from app.db.session import get_db
from app.schemas.ai_common import AiErrorResponse
from app.schemas.ai_profile import (
    ProfileMetricHistoryResponse,
    ProfileMetricPoint,
    ProfileSubject,
)
from app.services.ai.continuous import _require_consent
from app.services.ai.flags import AiFeature, AiFeatureDisabledError, require_ai_feature
from app.services.ai.metric_snapshot import compute_metric_snapshot

router = APIRouter()


@router.get(
    "/profile-metrics/history",
    response_model=ProfileMetricHistoryResponse,
    responses={
        403: {"model": AiErrorResponse, "description": "未授权"},
        503: {"model": AiErrorResponse, "description": "AI 画像功能未开放"},
    },
)
async def get_profile_metrics_history(
    subject: ProfileSubject = Query(..., description="personal 或 ideal_partner"),
    limit: int = Query(20, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProfileMetricHistoryResponse:
    """返回按时间升序的版本指标点；无字段的版本返回空维度，不补零。"""
    try:
        require_ai_feature(AiFeature.PROFILE, settings)
    except AiFeatureDisabledError as exc:
        raise _error_response("AI_FEATURE_DISABLED", "AI 功能暂未开放", 503) from exc
    try:
        await _require_consent(db, current_user.id)
    except PermissionError as exc:
        raise _error_response("AI_CONSENT_REQUIRED", "请先完成画像文本抽取授权", 403) from exc

    revisions = (await db.execute(text(
        "SELECT id, revision_no, published_at FROM ai_profile_revision "
        "WHERE user_id=:user_id AND subject=:subject "
        "ORDER BY revision_no DESC, id DESC LIMIT :limit"
    ), {"user_id": current_user.id, "subject": subject.value, "limit": limit + 1})
    ).mappings().all()
    has_more = len(revisions) > limit
    points: list[ProfileMetricPoint] = []
    for row in reversed(revisions[:limit]):
        fields = (await db.execute(text(
            "SELECT field_key, subject, field_kind, profile_dimension, category, content, "
            "display_value, confidence, content_hash "
            "FROM ai_profile_revision_field WHERE revision_id=:revision_id ORDER BY id"
        ), {"revision_id": row["id"]})).mappings().all()
        snapshot = compute_metric_snapshot([dict(item) for item in fields])
        published_at = row["published_at"]
        points.append(ProfileMetricPoint(
            revision_id=int(row["id"]),
            revision_no=int(row["revision_no"]),
            published_at=published_at.isoformat() if isinstance(published_at, datetime) else None,
            overall_percent=snapshot["overall_percent"] if fields else None,
            confirmed_field_count=snapshot["confirmed_field_count"],
            entry_count=snapshot["entry_count"],
            dimensions=snapshot["dimensions"],
        ))
    return ProfileMetricHistoryResponse(
        subject=subject, points=points, has_more=has_more,
        notice="数值来自已确认的画像版本，仅供参考；无数据的版本留空，不代表 0 分或完成度。",
    )

"""情感实验室 E1.5：把端侧测出的 MBTI 同步进个人资料。

范围刻意只有「写 `user_profile.mbti`」这一件事：

- 不建会话/不存答题/不做合拍（E2 才需要）；
- 不写 `user_mbti_result`：该表只在 legacy 的 ``database_setup_marriage.py`` 里定义，
  不在 ``app/db/business_schema.py``，新环境并不存在；等 E2 落库时再进 schema + 迁移；
- 写资料复用 ``profile.update_profile``，因此完整度重算、修订号入队、锁定顺序都与其他
  资料字段完全一致，不另开一条旁路。
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.auth import ProfileUpdateRequest
from app.schemas.emotion_lab import (
    EmotionLabProfileSourceRequest,
    EmotionLabProfileSourceResponse,
)
from app.services.profile import update_profile


def _unprocessable(code: str, message: str) -> HTTPException:
    """带业务码的 422（与 ``_forbidden`` 同形，前端可按 code 分流）。"""
    return HTTPException(422, detail={"code": code, "message": message, "retryable": False})


async def _profile_row(db: AsyncSession, user_id: int) -> dict | None:
    result = await db.execute(
        text("SELECT mbti, updated_at FROM user_profile WHERE user_id = :user_id"),
        {"user_id": user_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def sync_profile_source(
    db: AsyncSession, user_id: int, request: EmotionLabProfileSourceRequest
) -> EmotionLabProfileSourceResponse:
    """把 MBTI 写进资料；同一结果重复提交不产生任何副作用。"""
    if not request.confirmed:
        raise _unprocessable("PROFILE_SYNC_CONFIRMATION_REQUIRED", "同步资料前需要明确确认")
    if request.source == "assessment" and not (request.result_id or "").strip():
        raise _unprocessable("ASSESSMENT_RESULT_REQUIRED", "同步测评结果需要有效结果记录")

    row = await _profile_row(db, user_id)
    if row is None:
        raise HTTPException(404, detail="资料不存在，请先完成注册")

    confirmed_at: datetime | None = row.get("updated_at")
    if row.get("mbti") != request.mbti_type:
        # 复用资料更新：完整度重算（mbti 是 18 项之一）、修订号入队、锁序都走既有路径。
        await update_profile(db, user_id, ProfileUpdateRequest(mbti=request.mbti_type))
        refreshed = await _profile_row(db, user_id)
        confirmed_at = (refreshed or {}).get("updated_at") or datetime.now(UTC).replace(tzinfo=None)
    else:
        # 幂等：资料没变就不写库，返回上次变更时间。
        confirmed_at = confirmed_at or datetime.now(UTC).replace(tzinfo=None)

    return EmotionLabProfileSourceResponse(
        mbti_type=request.mbti_type,
        source=request.source,
        assessment_version=None,
        result_id=request.result_id,
        confirmed_at=confirmed_at,
    )

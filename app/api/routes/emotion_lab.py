"""情感实验室（MBTI）路由。

E1.5 只提供 ``PUT /emotion-lab/profile-source``：把端侧测出的结果同步进资料。
前端 ``api/emotionLab.uts`` 已经按这个路径与 camelCase 出入参写好，因此本接口
落地后前端不需要改路径。
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas.emotion_lab import (
    EmotionLabProfileSourceRequest,
    EmotionLabProfileSourceResponse,
)
from app.services.emotion_lab import sync_profile_source

router = APIRouter()


@router.put(
    "/emotion-lab/profile-source",
    response_model=EmotionLabProfileSourceResponse,
    summary="同步情感实验室 MBTI 结果到个人资料",
)
async def put_profile_source(
    body: EmotionLabProfileSourceRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EmotionLabProfileSourceResponse:
    return await sync_profile_source(db, current.id, body)

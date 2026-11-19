"""情感实验室（MBTI）真实会话、计分、资料同步和合拍推荐接口。"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas.emotion_lab import (
    EmotionLabMatchesResponse,
    EmotionLabProfileSourceRequest,
    EmotionLabProfileSourceResponse,
    EmotionLabAnswersRequest,
    EmotionLabSession,
    EmotionLabSummaryResponse,
)
from app.services.emotion_lab import (
    discard_session,
    matches,
    save_answers,
    start_session,
    submit_session,
    summary,
    sync_profile_source,
)

router = APIRouter()


@router.get("/emotion-lab/summary", response_model=EmotionLabSummaryResponse, summary="读取情感实验室状态")
async def get_summary(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EmotionLabSummaryResponse:
    return await summary(db, current.id)


@router.post("/emotion-lab/sessions", response_model=EmotionLabSession, summary="开始或恢复 MBTI 测试")
async def post_session(
    body: dict,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EmotionLabSession:
    return await start_session(db, current.id, str(body.get("assessmentId") or body.get("assessment_id") or "mbti-core"))


@router.put("/emotion-lab/sessions/{session_id}/answers", response_model=EmotionLabSession, summary="保存 MBTI 答案")
async def put_answers(
    session_id: str,
    body: EmotionLabAnswersRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EmotionLabSession:
    return await save_answers(db, current.id, session_id, body)


@router.post("/emotion-lab/sessions/{session_id}/submit", response_model=EmotionLabSession, summary="提交阶段或完整 MBTI 结果")
async def post_submit(
    session_id: str,
    body: EmotionLabAnswersRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EmotionLabSession:
    return await submit_session(db, current.id, session_id, body)


@router.post("/emotion-lab/sessions/{session_id}/discard", response_model=EmotionLabSession, summary="放弃 MBTI 草稿")
async def post_discard(
    session_id: str,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EmotionLabSession:
    return await discard_session(db, current.id, session_id)


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


@router.get(
    "/emotion-lab/matches",
    response_model=EmotionLabMatchesResponse,
    summary="查询与当前用户最合拍的人",
)
async def get_matches(
    limit: int = Query(6, ge=1, le=20, description="返回条数上限"),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EmotionLabMatchesResponse:
    return await matches(db, current.id, limit)

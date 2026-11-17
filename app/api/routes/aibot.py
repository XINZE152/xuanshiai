"""恋 AI BOT 专属红娘会话路由。"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas.aibot import (
    AibotAssignmentResponse,
    AibotAssignmentUpdate,
    AibotReportListItem,
    AibotReportListResponse,
    AibotReportRequest,
    AibotReportResponse,
    AibotSessionCreate,
    AibotSessionResponse,
    AibotTurnRequest,
    AibotTurnResponse,
    AibotWelcomeResponse,
)
from app.schemas.discovery import DiscoveryFilters, DiscoveryPage
from app.services.aibot_chat import (
    ensure_session,
    get_assignment,
    post_turn,
    post_welcome,
    put_assignment,
)
from app.services.aibot_report import generate_report, get_report, list_reports, latest_report
from app.services.discovery import get_aibot_recommendations_page

router = APIRouter(prefix="/ai/aibot", tags=["恋 AI BOT"])


@router.get("/assignment", response_model=AibotAssignmentResponse, summary="读取已选专属红娘")
async def read_assignment(
    current: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AibotAssignmentResponse:
    return await get_assignment(db, current.id)


@router.put("/assignment", response_model=AibotAssignmentResponse, summary="绑定专属红娘")
async def update_assignment(
    body: AibotAssignmentUpdate,
    current: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AibotAssignmentResponse:
    return await put_assignment(db, current.id, body.agent_id)


@router.post(
    "/sessions",
    response_model=AibotSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建或复用专属红娘会话",
)
async def create_or_reuse_session(
    body: AibotSessionCreate = Body(default=AibotSessionCreate()),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AibotSessionResponse:
    return await ensure_session(db, current.id, body.agent_id)


@router.get("/sessions/current", response_model=AibotSessionResponse, summary="读取当前红娘会话与历史")
async def current_session(
    current: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AibotSessionResponse:
    return await ensure_session(db, current.id)


@router.post(
    "/sessions/{session_id}/welcome",
    response_model=AibotWelcomeResponse,
    summary="下发欢迎语并落库",
)
async def welcome(
    session_id: Annotated[str, Path(min_length=8, max_length=64)],
    current: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AibotWelcomeResponse:
    return await post_welcome(db, current.id, session_id)


@router.post(
    "/sessions/{session_id}/turns",
    response_model=AibotTurnResponse,
    status_code=status.HTTP_201_CREATED,
    summary="用户发一句，红娘真实回复并落库",
)
async def create_turn(
    session_id: Annotated[str, Path(min_length=8, max_length=64)],
    body: AibotTurnRequest,
    current: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> AibotTurnResponse:
    return await post_turn(
        db,
        current.id,
        session_id,
        body.text,
        idempotency_key=idempotency_key,
    )


@router.get(
    "/recommendations",
    response_model=DiscoveryPage,
    summary="BOT 推荐对象（资料可后置，复用现网会员池）",
)
async def aibot_recommendations(
    filters: Annotated[DiscoveryFilters, Query()],
    current: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DiscoveryPage:
    return await get_aibot_recommendations_page(db, current.id, filters)


@router.post(
    "/reports",
    response_model=AibotReportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="生成恋爱报告（资料 + AI 画像 + 聊天情绪）并落库",
)
async def create_love_report(
    body: Annotated[AibotReportRequest, Body()] = AibotReportRequest(),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AibotReportResponse:
    payload = await generate_report(
        db,
        current.id,
        client_score=body.score.model_dump() if body.score is not None else None,
        love_index=body.love_index.model_dump() if body.love_index is not None else None,
        agent_id=body.agent_id,
    )
    return AibotReportResponse(**payload)


@router.get(
    "/reports/latest",
    response_model=AibotReportResponse | None,
    summary="读取最新一版恋爱报告（无版本时返回 null）",
)
async def read_latest_love_report(
    current: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AibotReportResponse | None:
    payload = await latest_report(db, current.id)
    return AibotReportResponse(**payload) if payload else None


@router.get(
    "/reports",
    response_model=AibotReportListResponse,
    summary="恋爱报告历史版本列表（回看 / 下载）",
)
async def read_love_reports(
    current: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> AibotReportListResponse:
    items = await list_reports(db, current.id, limit)
    return AibotReportListResponse(items=[AibotReportListItem(**item) for item in items])


@router.get(
    "/reports/{version}",
    response_model=AibotReportResponse,
    summary="按版本号读取一版恋爱报告",
)
async def read_love_report(
    version: Annotated[int, Path(ge=1, le=9999)],
    current: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AibotReportResponse:
    payload = await get_report(db, current.id, version)
    return AibotReportResponse(**payload)

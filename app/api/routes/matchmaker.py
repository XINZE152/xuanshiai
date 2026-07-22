"""First-phase matchmaker and free service request routes."""

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, get_current_admin, get_current_user
from app.db.session import get_db
from app.schemas.matchmaker import (
    MatchmakerAdminServiceRequestUpdate,
    MatchmakerCard,
    MatchmakerPage,
    MatchmakerServiceRequestCreate,
    MatchmakerServiceRequestPage,
    MatchmakerServiceRequestResponse,
    MatchmakerServiceRequestUpdate,
)
from app.services.matchmaker import (
    admin_list_service_requests,
    admin_update_service_request,
    create_service_request,
    get_matchmaker,
    list_matchmakers,
    list_service_requests,
    update_service_request,
)

router = APIRouter(prefix="/matchmakers")


@router.get("", response_model=MatchmakerPage, summary="查询服务红娘列表")
async def matchmaker_list(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
) -> MatchmakerPage:
    return await list_matchmakers(db, page, page_size)


@router.get("/ranking", response_model=MatchmakerPage, summary="查询热心红娘排行榜")
async def matchmaker_ranking(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
) -> MatchmakerPage:
    return await list_matchmakers(db, page, page_size, ranking=True)


@router.get("/{matchmaker_id}", response_model=MatchmakerCard, summary="查询服务红娘详情")
async def matchmaker_detail(
    matchmaker_id: int = Path(..., ge=1), db: AsyncSession = Depends(get_db)
) -> MatchmakerCard:
    return await get_matchmaker(db, matchmaker_id)


requests_router = APIRouter(prefix="/matchmaker/service-requests")


@requests_router.post("", response_model=MatchmakerServiceRequestResponse, status_code=201, summary="提交牵线服务申请")
async def create_request(
    body: MatchmakerServiceRequestCreate,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MatchmakerServiceRequestResponse:
    return await create_service_request(db, current, body)


@requests_router.get("/mine", response_model=MatchmakerServiceRequestPage, summary="查询我提交的牵线申请")
async def mine_requests(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=50),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MatchmakerServiceRequestPage:
    return await list_service_requests(db, current, page, page_size)


@requests_router.get("/assigned", response_model=MatchmakerServiceRequestPage, summary="查询分配给我的牵线申请")
async def assigned_requests(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=50),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MatchmakerServiceRequestPage:
    return await list_service_requests(db, current, page, page_size, assigned=True)


@requests_router.patch("/{service_id}", response_model=MatchmakerServiceRequestResponse, summary="处理牵线服务申请")
async def update_request(
    service_id: int = Path(..., ge=1),
    body: MatchmakerServiceRequestUpdate = ...,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MatchmakerServiceRequestResponse:
    return await update_service_request(db, current, service_id, body)


admin_router = APIRouter(prefix="/admin/matchmaker/service-requests")


@admin_router.get("", response_model=MatchmakerServiceRequestPage, summary="管理员查询牵线服务申请")
async def admin_requests(
    status: int | None = Query(None, ge=0, le=3),
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=50),
    admin: CurrentUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> MatchmakerServiceRequestPage:
    return await admin_list_service_requests(db, page, page_size, status)


@admin_router.patch("/{service_id}", response_model=MatchmakerServiceRequestResponse, summary="管理员分配或处理牵线服务申请")
async def admin_update_request(
    service_id: int = Path(..., ge=1),
    body: MatchmakerAdminServiceRequestUpdate = ...,
    admin: CurrentUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> MatchmakerServiceRequestResponse:
    return await admin_update_service_request(db, admin.id, service_id, body)

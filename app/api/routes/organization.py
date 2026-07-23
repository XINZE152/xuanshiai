"""一期组织、门店和归属接口。"""

from fastapi import APIRouter, Body, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, get_current_admin, get_current_user
from app.db.session import get_db
from app.schemas.organization import (
    PartnerJoinCreate,
    PartnerMembershipResponse,
    PartnerTeamCreate,
    PartnerTeamResponse,
    PromotionAttributionCreate,
    PromotionAttributionResponse,
    PromotionTouchCreate,
    PromotionTouchResponse,
    ResourceAssignmentCreate,
    ResourceAssignmentResponse,
    StoreCreate,
    StoreMemberCreate,
    StoreMemberResponse,
    StoreResponse,
)
from app.services.organization import (
    add_store_member,
    assign_resource,
    attribute_promotion,
    create_partner_team,
    create_store,
    create_touch,
    get_store,
    join_partner_team,
    list_stores,
)

router = APIRouter(prefix="/organizations")
promotion_router = APIRouter(prefix="/promotions")
partner_router = APIRouter(prefix="/partners")


@router.post("/stores", response_model=StoreResponse, status_code=201, summary="创建门店")
async def create_store_route(body: StoreCreate = Body(...), admin: CurrentUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)) -> StoreResponse:
    return await create_store(db, admin, body)


@router.get("/stores", response_model=list[StoreResponse], summary="查询门店")
async def list_stores_route(admin: CurrentUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)) -> list[StoreResponse]:
    return await list_stores(db)


@router.get("/stores/{store_id}", response_model=StoreResponse, summary="查询门店详情")
async def get_store_route(store_id: int = Path(..., ge=1), admin: CurrentUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)) -> StoreResponse:
    return await get_store(db, store_id)


@router.post("/stores/{store_id}/members", response_model=StoreMemberResponse, status_code=201, summary="添加门店成员")
async def add_store_member_route(store_id: int = Path(..., ge=1), body: StoreMemberCreate = Body(...), admin: CurrentUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)) -> StoreMemberResponse:
    return await add_store_member(db, admin, store_id, body)


@router.post("/assignments", response_model=ResourceAssignmentResponse, status_code=201, summary="分派会员资源")
async def assign_resource_route(body: ResourceAssignmentCreate = Body(...), admin: CurrentUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)) -> ResourceAssignmentResponse:
    return await assign_resource(db, admin, body)


@promotion_router.post("/touches", response_model=PromotionTouchResponse, status_code=201, summary="创建推广触点")
async def create_touch_route(body: PromotionTouchCreate = Body(...), current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> PromotionTouchResponse:
    return await create_touch(db, current, body)


@promotion_router.post("/attributions", response_model=PromotionAttributionResponse, status_code=201, summary="确认会员推广归属")
async def attribute_promotion_route(body: PromotionAttributionCreate = Body(...), current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> PromotionAttributionResponse:
    return await attribute_promotion(db, current, body)


@partner_router.post("/teams", response_model=PartnerTeamResponse, status_code=201, summary="创建合伙团队")
async def create_partner_team_route(body: PartnerTeamCreate = Body(...), admin: CurrentUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)) -> PartnerTeamResponse:
    return await create_partner_team(db, admin, body)


@partner_router.post("/memberships", response_model=PartnerMembershipResponse, status_code=201, summary="加入合伙团队")
async def join_partner_team_route(body: PartnerJoinCreate = Body(...), current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> PartnerMembershipResponse:
    return await join_partner_team(db, current, body)

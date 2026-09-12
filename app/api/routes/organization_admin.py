"""Independent back-office store and assignment routes."""

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentMatchmakerAdmin, get_current_matchmaker_admin
from app.db.session import get_db
from app.schemas.organization_admin import (
    AssignmentAdminPage,
    AssignmentAdminItem,
    StoreAdminCreate,
    StoreAdminItem,
    StoreAdminPage,
    StoreAdminUpdate,
    StoreMemberAdminItem,
    StoreMemberAdminPage,
    StoreReport,
    StoreReportMonthly,
    StoreReportSummary,
    StoreStatusUpdate,
)
from app.schemas.organization import StoreMemberResponse
from app.services.organization import add_store_member
from app.services.organization_admin import (
    create_store_admin,
    delete_store_admin,
    end_assignment,
    get_store_admin,
    list_assignments,
    list_store_members,
    list_stores_admin,
    remove_store_member,
    store_report,
    store_report_monthly,
    store_report_summary,
    update_store,
    update_store_status,
)
from app.schemas.organization import StoreMemberCreate

router = APIRouter(prefix="/admin/matchmaker")


@router.get("/stores", response_model=StoreAdminPage, summary="分页查询分站/门店")
async def store_list(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, max_length=64),
    status: int | None = Query(None, ge=1, le=3),
    _: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> StoreAdminPage:
    items, total = await list_stores_admin(db, page, page_size, search, status)
    return StoreAdminPage(items=items, page=page, page_size=page_size, total=total, has_more=page * page_size < total)


@router.post("/stores", response_model=StoreAdminItem, status_code=201, summary="创建分站/门店")
async def store_create(body: StoreAdminCreate = ..., current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> StoreAdminItem:
    return await create_store_admin(db, body, current.account.id)


@router.get("/stores/{store_id}", response_model=StoreAdminItem)
async def store_detail(store_id: int = Path(..., ge=1), _: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)):
    return await get_store_admin(db, store_id)


@router.patch("/stores/{store_id}", response_model=StoreAdminItem)
async def store_update(store_id: int = Path(..., ge=1), body: StoreAdminUpdate = ..., current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)):
    return await update_store(db, store_id, body, current.account.id)


@router.patch("/stores/{store_id}/status", response_model=StoreAdminItem)
async def store_status(store_id: int = Path(..., ge=1), body: StoreStatusUpdate = ..., current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)):
    return await update_store_status(db, store_id, body.status, body.reason, current.account.id)


@router.get("/stores/{store_id}/members", response_model=StoreMemberAdminPage)
async def store_members(store_id: int = Path(..., ge=1), page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), _: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)):
    items, total = await list_store_members(db, store_id, page, page_size)
    return StoreMemberAdminPage(items=items, page=page, page_size=page_size, total=total, has_more=page * page_size < total)


@router.post("/stores/{store_id}/members", response_model=StoreMemberResponse, status_code=201)
async def store_member_add(store_id: int = Path(..., ge=1), body: StoreMemberCreate = ..., current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)):
    return await add_store_member(db, current_user(current), store_id, body)


@router.delete("/store-members/{member_id}", response_model=StoreMemberAdminItem)
async def store_member_remove(member_id: int = Path(..., ge=1), reason: str = Query(..., min_length=1, max_length=255), current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)):
    return await remove_store_member(db, member_id, reason, current.account.id)


@router.delete("/stores/{store_id}", response_model=StoreAdminItem, summary="删除分站/门店")
async def store_delete(store_id: int = Path(..., ge=1), current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> StoreAdminItem:
    return await delete_store_admin(db, store_id, current.account.id)


@router.get("/stores/{store_id}/report", response_model=StoreReport)
async def store_report_route(store_id: int = Path(..., ge=1), _: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)):
    return await store_report(db, store_id)


@router.get("/stores/{store_id}/report/summary", response_model=StoreReportSummary, summary="分店报表统计卡")
async def store_report_summary_route(store_id: int = Path(..., ge=1), _: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> StoreReportSummary:
    return await store_report_summary(db, store_id)


@router.get("/stores/{store_id}/report/monthly", response_model=StoreReportMonthly, summary="分店月度报表")
async def store_report_monthly_route(store_id: int = Path(..., ge=1), months: int = Query(6, ge=1, le=24), _: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> StoreReportMonthly:
    return await store_report_monthly(db, store_id, months)


@router.get("/assignments", response_model=AssignmentAdminPage, operation_id="matchmaker_admin_assignments_page")
async def assignments(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), search: str | None = Query(None, max_length=128), _: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)):
    items, total = await list_assignments(db, page, page_size, search)
    return AssignmentAdminPage(items=items, page=page, page_size=page_size, total=total, has_more=page * page_size < total)


@router.post("/assignments/{assignment_id}/end", response_model=AssignmentAdminItem)
async def assignment_end(assignment_id: int = Path(..., ge=1), reason: str = Query(..., min_length=1, max_length=255), current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)):
    return await end_assignment(db, assignment_id, reason, current.account.id)


def current_user(current: CurrentMatchmakerAdmin):
    from app.api.dependencies import CurrentUser
    return CurrentUser(id=current.account.id, session_id=current.session_id, phone=None, status=1, realname_status=2)

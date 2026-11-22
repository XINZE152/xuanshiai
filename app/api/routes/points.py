from fastapi import APIRouter, Depends, Header, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas.payments import PaidOrderResponse
from app.schemas.points import CheckinResponse, ClaimTaskResponse, CreatePointRechargeOrderRequest, InvitePage, PointLedgerPage, PointProduct, PointRechargePackage, PointsSummary, RedeemRequest, RedeemResponse, TaskItem
from app.schemas.quotas import QuotaSummary
from app.services.payments import get_paid_order
from app.services.points import checkin, checkin_status, claim_task, invites, ledger, products, redeem, summary, tasks
from app.services.points_recharge import create_recharge_order, list_recharge_packages
from app.services.quotas import summary as quota_summary

router = APIRouter()

@router.get("/users/me/quotas", response_model=QuotaSummary, summary="查询当前用户各项剩余次数")
async def quotas(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> QuotaSummary:
    return await quota_summary(db, current.id)

@router.get("/users/me/points", response_model=PointsSummary, summary="查询积分余额")
async def points(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> PointsSummary: return await summary(db, current.id)

@router.get("/users/me/points/ledger", response_model=PointLedgerPage, summary="查询积分流水")
async def point_ledger(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=50), current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> PointLedgerPage: return await ledger(db, current.id, page, page_size)

@router.post("/users/me/checkin", response_model=CheckinResponse, summary="每日签到")
async def daily_checkin(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> CheckinResponse: return await checkin(db, current.id)

@router.get("/users/me/checkin", response_model=CheckinResponse, summary="查询今日签到状态")
async def daily_checkin_status(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> CheckinResponse: return await checkin_status(db, current.id)

@router.get("/users/me/tasks", response_model=list[TaskItem], summary="查询积分任务")
async def task_list(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[TaskItem]: return await tasks(db, current.id)

@router.post("/users/me/tasks/{task_code}/claim", response_model=ClaimTaskResponse, summary="领取任务积分")
async def task_claim(task_code: str, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> ClaimTaskResponse: return await claim_task(db, current.id, task_code)

@router.get("/users/me/invites", response_model=InvitePage, summary="查询邀请记录")
async def invite_list(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=50), current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> InvitePage: return await invites(db, current.id, page, page_size)

@router.get("/points/products", response_model=list[PointProduct], summary="查询积分商品和权益")
async def product_list(db: AsyncSession = Depends(get_db)) -> list[PointProduct]: return await products(db)

@router.post("/users/me/points/redeem", response_model=RedeemResponse, summary="兑换积分商品或权益")
async def product_redeem(body: RedeemRequest, idempotency_key: str | None = Header(None, alias="Idempotency-Key"), current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> RedeemResponse: return await redeem(db, current.id, body, idempotency_key)

@router.get("/points/recharge-packages", response_model=list[PointRechargePackage], summary="查询积分充值商品")
async def recharge_packages(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[PointRechargePackage]: return await list_recharge_packages(db)

@router.post("/points/recharge/orders", response_model=PaidOrderResponse, status_code=201, summary="创建积分充值订单")
async def recharge_order(
    body: CreatePointRechargeOrderRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaidOrderResponse: return await create_recharge_order(db, current.id, body, idempotency_key)

@router.get("/points/recharge/orders/{order_no}", response_model=PaidOrderResponse, summary="查询积分充值订单")
async def recharge_order_detail(order_no: str = Path(..., min_length=8, max_length=64), current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> PaidOrderResponse: return await get_paid_order(db, current.id, order_no, 7)

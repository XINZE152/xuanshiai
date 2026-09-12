"""一期订单、分成、余额和提现接口。"""

from fastapi import APIRouter, Body, Depends, Path, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentMatchmakerAdmin, CurrentUser, get_current_user, get_current_matchmaker_admin
from app.db.session import get_db
from app.schemas.finance import (
    AccountBalanceResponse,
    CommissionEntryResponse,
    CommissionEntryDetailOptions,
    CommissionEntryDetailPage,
    CommissionRuleCreate,
    CommissionRuleResponse,
    CreditGrantRequest,
    CreditGrantResult,
    FinanceOrderCreate,
    FinanceReportRow,
    FinanceRefundRequest,
    FinanceDailyRow,
    ProductCommissionConfigCreate,
    ProductCommissionConfigResponse,
    PaymentOrderResponse,
    WithdrawalCreate,
    WithdrawalResponse,
    WithdrawalReview,
    LedgerEntryPage,
    PaymentOrderAdminPage,
    WithdrawalAdminPage,
    StoreCommissionEntryPage,
    StoreCommissionOptions,
    StoreCommissionSummary,
)
from app.services.finance import (
    create_order,
    create_rule,
    get_balance,
    list_rules,
    list_user_commissions,
    admin_finance_report,
    admin_list_commission_entries,
    admin_list_commission_options,
    admin_list_store_commission_entries,
    admin_store_commission_options,
    admin_store_commission_summary,
    build_store_commission_export,
    refund_order,
    release_commission,
    mark_order_paid_and_settle,
    request_withdrawal,
    review_withdrawal,
    create_product_commission_config,
    admin_list_ledger,
    admin_list_orders,
    admin_list_withdrawals,
    admin_revenue_daily_report,
)

router = APIRouter(prefix="/finance")
admin_router = APIRouter(prefix="/admin/finance")

_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _finance_actor(current: CurrentMatchmakerAdmin) -> CurrentUser:
    return CurrentUser(id=current.account.id, session_id=current.session_id, phone=None, status=1, realname_status=2)


@router.post("/orders", response_model=PaymentOrderResponse, status_code=201, summary="创建待支付订单")
async def order(body: FinanceOrderCreate = Body(...), current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> PaymentOrderResponse:
    return await create_order(db, current, body)


@router.get("/balance", response_model=AccountBalanceResponse, summary="查询我的余额")
async def balance(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> AccountBalanceResponse:
    return await get_balance(db, "user", current.id)


@router.get("/commission-entries", response_model=list[CommissionEntryResponse], summary="查询我的分成明细")
async def commission_entries(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[CommissionEntryResponse]:
    return await list_user_commissions(db, current)


@router.post("/withdrawals", response_model=WithdrawalResponse, status_code=201, summary="申请提现")
async def withdrawal(body: WithdrawalCreate = Body(...), current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> WithdrawalResponse:
    return await request_withdrawal(db, current, body)


@admin_router.post("/commission-rules", response_model=CommissionRuleResponse, status_code=201, summary="创建分成规则")
async def rule(body: CommissionRuleCreate = Body(...), admin: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> CommissionRuleResponse:
    admin.require("finance.write")
    return await create_rule(db, _finance_actor(admin), body)


@admin_router.get("/commission-rules", response_model=list[CommissionRuleResponse], summary="查询分成规则")
async def rules(admin: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> list[CommissionRuleResponse]:
    admin.require("finance.read")
    return await list_rules(db)


@admin_router.post("/product-commission-rules/{product_id}", response_model=ProductCommissionConfigResponse, status_code=201, summary="配置商品分成对象")
async def product_commission_rule(
    product_id: int = Path(..., ge=1), body: ProductCommissionConfigCreate = Body(...),
    admin: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db),
) -> ProductCommissionConfigResponse:
    admin.require("finance.write")
    return await create_product_commission_config(db, _finance_actor(admin), product_id, body)


@admin_router.get("/report", response_model=list[FinanceReportRow], summary="查询分成汇总报表")
async def report(admin: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> list[FinanceReportRow]:
    admin.require("finance.read")
    return await admin_finance_report(db)


@admin_router.get("/daily-report", response_model=list[FinanceDailyRow], summary="查询按日收入/退款统计报表")
async def daily_report(
    start_date: str | None = Query(None, max_length=10, description="开始日期 YYYY-MM-DD（兼容前端空字符串）"),
    end_date: str | None = Query(None, max_length=10, description="结束日期 YYYY-MM-DD（兼容前端空字符串）"),
    admin: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> list[FinanceDailyRow]:
    admin.require("finance.read")
    return await admin_revenue_daily_report(db, start_date, end_date)


@admin_router.get("/orders", response_model=PaymentOrderAdminPage, summary="后台分页查询订单")
async def admin_orders(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    status: int | None = Query(None, ge=0, le=3), user_id: int | None = Query(None, ge=1),
    order_no: str | None = Query(None, min_length=1, max_length=64),
    start_time: str | None = Query(None, max_length=10, description="开始日期 YYYY-MM-DD（兼容前端空字符串）"),
    end_time: str | None = Query(None, max_length=10, description="结束日期 YYYY-MM-DD（兼容前端空字符串）"),
    admin: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> PaymentOrderAdminPage:
    admin.require("finance.read")
    return await admin_list_orders(db, page, page_size, status, user_id, order_no, start_time, end_time)


@admin_router.get("/withdrawals", response_model=WithdrawalAdminPage, summary="后台分页查询提现")
async def admin_withdrawals(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None, max_length=32),
    account_id: int | None = Query(None, ge=1),
    start_time: str | None = Query(None, max_length=10, description="开始日期 YYYY-MM-DD（兼容前端空字符串）"),
    end_time: str | None = Query(None, max_length=10, description="结束日期 YYYY-MM-DD（兼容前端空字符串）"),
    admin: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> WithdrawalAdminPage:
    admin.require("finance.read")
    return await admin_list_withdrawals(db, page, page_size, status, account_id, start_time, end_time)


@admin_router.get("/ledger", response_model=LedgerEntryPage, summary="后台分页查询资金流水")
async def admin_ledger(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    account_type: str | None = Query(None, max_length=32),
    account_id: int | None = Query(None, ge=1),
    start_time: str | None = Query(None, max_length=10, description="开始日期 YYYY-MM-DD（兼容前端空字符串）"),
    end_time: str | None = Query(None, max_length=10, description="结束日期 YYYY-MM-DD（兼容前端空字符串）"),
    admin: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> LedgerEntryPage:
    admin.require("finance.read")
    return await admin_list_ledger(db, page, page_size, account_type, account_id, start_time, end_time)


@admin_router.post("/orders/{order_id}/settle", response_model=list[CommissionEntryResponse], summary="结算已支付订单分成")
async def settle(order_id: int = Path(..., ge=1), admin: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> list[CommissionEntryResponse]:
    admin.require("finance.write")
    return await mark_order_paid_and_settle(db, _finance_actor(admin), order_id)


@admin_router.post("/orders/{order_id}/refund", status_code=204, summary="退款并冲正分成")
async def refund(order_id: int = Path(..., ge=1), body: FinanceRefundRequest = Body(...), admin: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> None:
    admin.require("finance.write")
    await refund_order(db, _finance_actor(admin), order_id, body)


@admin_router.post("/commission-entries/{entry_id}/release", response_model=CommissionEntryResponse, summary="释放待结算分成")
async def release(entry_id: int = Path(..., ge=1), admin: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> CommissionEntryResponse:
    admin.require("finance.write")
    return await release_commission(db, _finance_actor(admin), entry_id)


@admin_router.get("/commission-entries", response_model=CommissionEntryDetailPage, summary="分页查询红娘线上分成明细")
async def admin_commission_entries(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    matchmaker_id: int | None = Query(None, ge=1, description="按红娘 user_id 筛选"),
    rule_id: int | None = Query(None, ge=1, description="按 commission_rule.id 筛选"),
    start_date: str | None = Query(None, max_length=10, description="开始日期 YYYY-MM-DD（兼容前端空字符串）"),
    end_date: str | None = Query(None, max_length=10, description="结束日期 YYYY-MM-DD（兼容前端空字符串）"),
    admin: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> CommissionEntryDetailPage:
    admin.require("finance.read")
    return await admin_list_commission_entries(
        db, page, page_size, matchmaker_id, rule_id, start_date, end_date
    )


@admin_router.get("/commission-entries/options", response_model=CommissionEntryDetailOptions, summary="获取红娘线上分成明细筛选下拉选项")
async def admin_commission_entry_options(
    admin: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> CommissionEntryDetailOptions:
    admin.require("finance.read")
    return await admin_list_commission_options(db)


@admin_router.get("/store-commission-entries", response_model=StoreCommissionEntryPage, summary="分页查询分店线上分成明细")
async def admin_store_commission_entries(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    store_id: int | None = Query(None, ge=1, description="按分店 organization.id 筛选"),
    matchmaker_id: int | None = Query(None, ge=1, description="按订单归属红娘筛选"),
    rule_id: int | None = Query(None, ge=1, description="按 commission_rule.id 筛选"),
    start_date: str | None = Query(None, max_length=10, description="开始日期 YYYY-MM-DD（兼容前端空字符串）"),
    end_date: str | None = Query(None, max_length=10, description="结束日期 YYYY-MM-DD（兼容前端空字符串）"),
    admin: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> StoreCommissionEntryPage:
    admin.require("finance.read")
    return await admin_list_store_commission_entries(db, page, page_size, store_id, matchmaker_id, rule_id, start_date, end_date)


@admin_router.get("/store-commission-entries/options", response_model=StoreCommissionOptions, summary="获取分店线上分成明细筛选下拉选项")
async def admin_store_commission_entry_options(
    admin: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> StoreCommissionOptions:
    admin.require("finance.read")
    return await admin_store_commission_options(db)


@admin_router.get("/store-commission-summary", response_model=StoreCommissionSummary, summary="分店分成统计卡")
async def admin_store_commission_summary_route(
    store_id: int | None = Query(None, ge=1, description="不传则统计全部分店"),
    admin: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> StoreCommissionSummary:
    admin.require("finance.read")
    return await admin_store_commission_summary(db, store_id)


@admin_router.get("/store-commission-entries/export", summary="导出分店线上分成明细 Excel")
async def admin_store_commission_export(
    store_id: int | None = Query(None, ge=1),
    matchmaker_id: int | None = Query(None, ge=1),
    rule_id: int | None = Query(None, ge=1),
    start_date: str | None = Query(None, max_length=10),
    end_date: str | None = Query(None, max_length=10),
    admin: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    admin.require("finance.read")
    content = await build_store_commission_export(db, store_id, matchmaker_id, rule_id, start_date, end_date)
    return Response(
        content=content,
        media_type=_XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": 'attachment; filename="store-commission-entries.xlsx"'},
    )


@admin_router.patch("/withdrawals/{withdrawal_id}", response_model=WithdrawalResponse, summary="审核提现")
async def review(withdrawal_id: int = Path(..., ge=1), body: WithdrawalReview = Body(...), admin: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> WithdrawalResponse:
    admin.require("finance.write")
    return await review_withdrawal(db, _finance_actor(admin), withdrawal_id, body)


@admin_router.post("/credit-grants", response_model=CreditGrantResult, status_code=201, summary="后台手动发放积分（积分明细-发放积分）")
async def grant_credits(body: CreditGrantRequest = Body(...), admin: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> CreditGrantResult:
    """按 target_type 解析账户集 → 写 account_ledger(CREDIT, AVAILABLE, source_type='admin_grant') → 写 business_audit_log。"""
    admin.require("finance.write")
    return await admin_grant_credits(db, _finance_actor(admin), body)

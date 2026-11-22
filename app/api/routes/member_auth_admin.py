"""会员认证（M3-1）管理后台路由。

前缀 ``/admin/members``；所有读接口需 ``matchmaker.member.read``，
写接口需 ``matchmaker.member.manage``（依赖注入已按路径自动映射，这里再显式声明）。
注意：静态路径（/auth/realname-reviews、/auth/types 等）必须声明在
``/auth/{kind}/{review_id}`` 之前，否则会被动态路由抢占。
"""

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    CurrentMatchmakerAdmin,
    get_current_matchmaker_admin,
    require_sensitive_operation,
)
from app.api.routes.auth import RealnamePrivacyRoute
from app.db.session import get_db
from app.schemas.member_auth_admin import (
    AuthTypeCreate,
    AuthTypeItem,
    AuthTypeUpdate,
    CommitmentReviewPage,
    EducationReviewPage,
    HouseReviewPage,
    MarriageReviewPage,
    MarriageStats,
    OtherReviewPage,
    RealnameReviewPage,
    RealnameStats,
    ReviewActionRequest,
)
from app.schemas.member_auth_recharge_admin import (
    QuotaPackageList,
    RechargeCreateRequest,
    RechargeOrder,
)
from app.services import member_auth_admin as service
from app.services import member_auth_recharge_admin as recharge_service
from app.services.sensitive_operations import (
    abort_sensitive_operation,
    begin_sensitive_operation,
    finish_sensitive_operation,
)

router = APIRouter(prefix="/admin/members")


# ─── 实名认证 ─────────────────────────────────────────────────────


@router.get("/auth/realname-reviews", response_model=RealnameReviewPage, summary="实名认证审核列表")
async def realname_reviews(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    status: str = Query("all", pattern="^(all|pending|success|fail)$"),
    keyword: str | None = Query(None, max_length=64),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> RealnameReviewPage:
    current.require("matchmaker.member.read")
    return await service.list_realname_reviews(db, page, page_size, status, keyword)


@router.get("/auth/realname-stats", response_model=RealnameStats, summary="实名认证统计")
async def realname_statistics(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> RealnameStats:
    current.require("matchmaker.member.read")
    return await service.realname_stats(db)


# ─── 核验额度在线充值（A17） ──────────────────────────────────
# 静态路径 /auth/quota-packages、/auth/recharge 必须声明在 /auth/{kind}/{review_id}
# 动态路由之前，否则会被动态路由抢占。provider 由配置 verification_recharge_provider 控制。


@router.get("/auth/quota-packages", response_model=QuotaPackageList, summary="核验额度充值套餐列表")
async def quota_packages(
    kind: str | None = Query(None, pattern="^(realname|marriage)$"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
) -> QuotaPackageList:
    """A17：只读套餐目录 + 当前是否可充值。目录不受 provider 开关影响。"""
    current.require("matchmaker.member.read")
    provider = recharge_service.current_provider()
    return QuotaPackageList(
        packages=recharge_service.list_packages(kind),
        provider=provider,
        recharge_enabled=provider != "disabled",
    )


@router.post("/auth/recharge", response_model=RechargeOrder, status_code=201, summary="发起核验额度充值")
async def auth_recharge(
    body: RechargeCreateRequest,
    idempotency_key: str = Depends(require_sensitive_operation),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> RechargeOrder:
    """A17：下单（幂等 + 二次确认）。provider=disabled→503、mock→待付订单、real→501。"""
    current.require("matchmaker.member.manage")
    reservation, replayed = await begin_sensitive_operation(
        db,
        actor_id=current.account.id,
        operation="member.auth.recharge",
        idempotency_key=idempotency_key,
        payload={"body": body.model_dump(mode="json")},
    )
    if replayed is not None:
        return RechargeOrder(**replayed)
    try:
        order = await recharge_service.create_order(db, current.account.id, body.kind, body.package_id, body.quantity)
    except Exception:
        await abort_sensitive_operation(db, reservation)
        raise
    await finish_sensitive_operation(db, reservation, order.model_dump(mode="json"))
    return order


@router.get("/auth/recharge/{order_no}", response_model=RechargeOrder, summary="查询核验额度充值订单")
async def auth_recharge_status(
    order_no: str = Path(..., min_length=1, max_length=64),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> RechargeOrder:
    """A17：按订单号查状态（支付回调后刷新）。"""
    current.require("matchmaker.member.read")
    return await recharge_service.get_order_status(db, order_no)


@router.post("/auth/recharge/{order_no}/callback", response_model=RechargeOrder, summary="充值支付回调（仅 mock/联调）")
async def auth_recharge_callback(
    order_no: str = Path(..., min_length=1, max_length=64),
    body: dict | None = None,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> RechargeOrder:
    """A17：mock provider 下的手动支付回调，将 pending 订单置为 paid/failed。"""
    current.require("matchmaker.member.manage")
    status = (body or {}).get("status", "paid")
    if status not in ("paid", "failed"):
        status = "paid"
    return await recharge_service.apply_mock_callback(db, order_no, status)


# ─── 会员承诺 ─────────────────────────────────────────────────────


@router.get("/auth/commitment-reviews", response_model=CommitmentReviewPage, summary="会员承诺书审核列表")
async def commitment_reviews(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    status: str = Query("all", pattern="^(all|pass|pending|fail)$"),
    keyword: str | None = Query(None, max_length=64),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> CommitmentReviewPage:
    current.require("matchmaker.member.read")
    return await service.list_commitment_reviews(db, page, page_size, status, keyword)


# ─── 婚姻状况 ─────────────────────────────────────────────────────


@router.get("/auth/marriage-reviews", response_model=MarriageReviewPage, summary="婚姻状况核验列表")
async def marriage_reviews(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    status: str = Query("all", pattern="^(all|married|no_record|divorced)$"),
    keyword: str | None = Query(None, max_length=64),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MarriageReviewPage:
    current.require("matchmaker.member.read")
    return await service.list_marriage_reviews(db, page, page_size, status, keyword)


@router.get("/auth/marriage-stats", response_model=MarriageStats, summary="婚姻状况统计")
async def marriage_statistics(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MarriageStats:
    current.require("matchmaker.member.read")
    return await service.marriage_stats(db)


# ─── 房产认证 ─────────────────────────────────────────────────────


@router.get("/auth/house-reviews", response_model=HouseReviewPage, summary="房产认证审核列表")
async def house_reviews(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    status: str = Query("all", pattern="^(all|pass|pending|fail)$"),
    keyword: str | None = Query(None, max_length=64),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> HouseReviewPage:
    current.require("matchmaker.member.read")
    return await service.list_house_reviews(db, page, page_size, status, keyword)


# ─── 学历认证 ─────────────────────────────────────────────────────


@router.get("/auth/education-reviews", response_model=EducationReviewPage, summary="学历认证审核列表")
async def education_reviews(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    status: str = Query("all", pattern="^(all|pass|pending|fail)$"),
    keyword: str | None = Query(None, max_length=64),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> EducationReviewPage:
    current.require("matchmaker.member.read")
    return await service.list_education_reviews(db, page, page_size, status, keyword)


# ─── 其他认证 ─────────────────────────────────────────────────────


@router.get("/auth/other-reviews", response_model=OtherReviewPage, summary="其他认证审核列表")
async def other_reviews(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    status: str = Query("all", pattern="^(all|pass|pending|fail)$"),
    keyword: str | None = Query(None, max_length=64),
    auth_type_id: int | None = Query(None, ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> OtherReviewPage:
    current.require("matchmaker.member.read")
    return await service.list_other_reviews(db, page, page_size, status, keyword, auth_type_id)


# ─── 认证类型（其他认证） ─────────────────────────────────────────


@router.get("/auth/types", summary="认证类型列表（纯数组，非分页）")
async def auth_types(
    keyword: str | None = Query(None, max_length=64),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> list[AuthTypeItem]:
    current.require("matchmaker.member.read")
    return await service.list_auth_types(db, keyword)


@router.post("/auth/types", response_model=AuthTypeItem, status_code=201, summary="创建认证类型")
async def create_auth_type(
    body: AuthTypeCreate,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> AuthTypeItem:
    current.require("matchmaker.member.manage")
    return await service.create_auth_type(db, body, current.account.id)


@router.put("/auth/types/{type_id}", response_model=AuthTypeItem, summary="更新认证类型")
async def update_auth_type(
    type_id: int = Path(..., ge=1),
    body: AuthTypeUpdate = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> AuthTypeItem:
    current.require("matchmaker.member.manage")
    return await service.update_auth_type(db, type_id, body, current.account.id)


@router.delete("/auth/types/{type_id}", summary="删除认证类型")
async def delete_auth_type(
    type_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    current.require("matchmaker.member.manage")
    return await service.delete_auth_type(db, type_id, current.account.id)


# ─── 审核 / 删除（动态路由，必须放在静态路由之后） ────────────────


async def review_auth(
    kind: str = Path(..., pattern="^(realname|commitment|marriage|house|education|other)$"),
    review_id: int = Path(..., ge=1),
    body: ReviewActionRequest = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    current.require("matchmaker.member.manage")
    return await service.review_auth(db, kind, review_id, body, current.account.id)


router.add_api_route(
    "/auth/{kind}/{review_id}", review_auth, methods=["PATCH"],
    summary="认证审核（通过/未通过）", route_class_override=RealnamePrivacyRoute,
)


@router.delete("/auth/{kind}/{review_id}", summary="删除认证记录")
async def delete_auth_review(
    kind: str = Path(..., pattern="^(realname|commitment|marriage|house|education|other)$"),
    review_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    current.require("matchmaker.member.manage")
    return await service.delete_auth_review(db, kind, review_id, current.account.id)

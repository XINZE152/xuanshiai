"""会员认证（M3-1）管理后台路由。

前缀 ``/admin/members``；所有读接口需 ``matchmaker.member.read``，
写接口需 ``matchmaker.member.manage``（依赖注入已按路径自动映射，这里再显式声明）。
注意：静态路径（/auth/realname-reviews、/auth/types 等）必须声明在
``/auth/{kind}/{review_id}`` 之前，否则会被动态路由抢占。
"""

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentMatchmakerAdmin, get_current_matchmaker_admin
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
from app.services import member_auth_admin as service

router = APIRouter(prefix="/admin/members")


# ─── 实名认证 ─────────────────────────────────────────────────────


@router.get("/auth/realname-reviews", response_model=RealnameReviewPage, summary="实名认证审核列表")
async def realname_reviews(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    status: str = Query("all", pattern="^(all|success|fail)$"),
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


@router.patch("/auth/{kind}/{review_id}", summary="认证审核（通过/未通过）")
async def review_auth(
    kind: str = Path(..., pattern="^(realname|commitment|marriage|house|education|other)$"),
    review_id: int = Path(..., ge=1),
    body: ReviewActionRequest = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    current.require("matchmaker.member.manage")
    return await service.review_auth(db, kind, review_id, body, current.account.id)


@router.delete("/auth/{kind}/{review_id}", summary="删除认证记录")
async def delete_auth_review(
    kind: str = Path(..., pattern="^(realname|commitment|marriage|house|education|other)$"),
    review_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    current.require("matchmaker.member.manage")
    return await service.delete_auth_review(db, kind, review_id, current.account.id)

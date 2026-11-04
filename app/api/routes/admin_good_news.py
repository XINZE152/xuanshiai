"""管理端红娘喜讯审核接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentMatchmakerAdmin, declare_permission, get_current_matchmaker_admin
from app.db.session import get_db
from app.schemas.admin_content import ContentItem, ContentItemPage
from app.schemas.good_news import (
    GoodNewsConsentPage,
    GoodNewsConsentRecordRequest,
    GoodNewsItem,
    GoodNewsReviewRequest,
)
from app.services.good_news import (
    admin_get,
    admin_list,
    list_consents,
    publish,
    record_consent,
    review,
    unpublish,
)

router = APIRouter(prefix="/admin/content/good_news")


@declare_permission("platform.config.read")
@router.get("", response_model=ContentItemPage, summary="喜讯审核列表")
async def admin_good_news_list(
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = Query(None, max_length=64),
    status: int | None = Query(None, ge=1, le=2),
    stage: str | None = Query(None, max_length=32),
    good_news_status: str | None = Query(None, max_length=32, description="兼容旧参数：等同于 stage"),
    review_status: str | None = Query(None, max_length=16),
    public_status: str | None = Query(None, max_length=16),
    matchmaker_id: int | None = Query(None, ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> ContentItemPage:
    current.require("platform.config.read")
    return await admin_list(
        db,
        page=page,
        page_size=page_size,
        keyword=keyword,
        status=status,
        stage=stage or good_news_status,
        review_status=review_status,
        public_status=public_status,
        matchmaker_id=matchmaker_id,
    )


@declare_permission("platform.config.read")
@router.get("/{news_id}", response_model=ContentItem, summary="喜讯审核详情")
async def admin_good_news_detail(
    news_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> ContentItem:
    current.require("platform.config.read")
    return await admin_get(db, news_id)


@declare_permission("platform.config.write")
@router.post("/{news_id}/review", response_model=GoodNewsItem, summary="审核喜讯")
async def admin_good_news_review(
    news_id: int = Path(..., ge=1),
    body: GoodNewsReviewRequest = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> GoodNewsItem:
    current.require("platform.config.write")
    return await review(db, news_id=news_id, admin_account_id=current.account.id, body=body)


@declare_permission("platform.config.write")
@router.post("/{news_id}/publish", response_model=GoodNewsItem, summary="发布喜讯")
async def admin_good_news_publish(
    news_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> GoodNewsItem:
    current.require("platform.config.write")
    return await publish(db, news_id=news_id, admin_account_id=current.account.id)


@declare_permission("platform.config.write")
@router.post("/{news_id}/unpublish", response_model=GoodNewsItem, summary="下架喜讯")
async def admin_good_news_unpublish(
    news_id: int = Path(..., ge=1),
    reason: str | None = Query(None, max_length=255),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> GoodNewsItem:
    current.require("platform.config.write")
    return await unpublish(db, news_id=news_id, admin_account_id=current.account.id, reason=reason)


@declare_permission("platform.config.read")
@router.get("/{news_id}/consents", response_model=GoodNewsConsentPage, summary="查看双方同意记录")
async def admin_good_news_consents(
    news_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> GoodNewsConsentPage:
    current.require("platform.config.read")
    return await list_consents(db, news_id)


@declare_permission("platform.config.write")
@router.post("/{news_id}/consents", response_model=GoodNewsConsentPage, summary="补录双方同意记录")
async def admin_good_news_record_consent(
    news_id: int = Path(..., ge=1),
    body: GoodNewsConsentRecordRequest = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> GoodNewsConsentPage:
    current.require("platform.config.write")
    return await record_consent(
        db,
        news_id=news_id,
        admin_account_id=current.account.id,
        role=body.role,
        user_id=body.user_id,
        scopes=body.scopes,
        consent_version=body.consent_version,
        source=body.source,
    )

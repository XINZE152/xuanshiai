"""红娘喜讯会员端接口。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, File, Header, HTTPException, Path, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, get_current_user, get_realname_verified_user
from app.db.session import get_db
from app.schemas.good_news import (
    GoodNewsBlessingResponse,
    GoodNewsConfig,
    GoodNewsItem,
    GoodNewsPage,
    GoodNewsPhotoResponse,
    GoodNewsReportCreate,
    GoodNewsReportItem,
    GoodNewsReportPage,
)
from app.services.good_news import (
    bless,
    create_report,
    get_my_report,
    get_public,
    get_public_config,
    list_my_reports,
    list_public,
    revoke_consent,
    unbless,
    upload_photo,
)

router = APIRouter(prefix="/good-news")


@router.get("/config", response_model=GoodNewsConfig, summary="读取喜讯栏目配置")
async def good_news_config(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GoodNewsConfig:
    _ = current
    return await get_public_config(db)


@router.post("/photo", response_model=GoodNewsPhotoResponse, status_code=201, summary="上传喜讯图片")
async def member_photo(
    file: UploadFile = File(...),
    current: CurrentUser = Depends(get_realname_verified_user),
) -> GoodNewsPhotoResponse:
    return await upload_photo(file, user_id=current.id)


@router.get("/reports/mine", response_model=GoodNewsReportPage, summary="查询我的报喜")
async def my_reports(
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(20, ge=1, le=100),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GoodNewsReportPage:
    return await list_my_reports(db, submitter_id=current.id, page=page, page_size=page_size)


@router.get("/reports/{report_id}", response_model=GoodNewsReportItem, summary="查询我的报喜详情")
async def my_report_detail(
    report_id: int = Path(..., ge=1),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GoodNewsReportItem:
    return await get_my_report(db, report_id=report_id, submitter_id=current.id)


@router.post("/reports", response_model=GoodNewsReportItem, status_code=201, summary="提交报喜")
async def create_my_report(
    body: GoodNewsReportCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=128),
    current: CurrentUser = Depends(get_realname_verified_user),
    db: AsyncSession = Depends(get_db),
) -> GoodNewsReportItem:
    _item, report_id = await create_report(
        db,
        submitter_id=current.id,
        submitter_role="MEMBER",
        body=body,
        idempotency_key=idempotency_key,
    )
    return await get_my_report(db, report_id=report_id, submitter_id=current.id)


@router.get("", response_model=GoodNewsPage, summary="浏览公开喜讯")
async def public_news(
    stage: str | None = Query(None, max_length=32),
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(20, ge=1, le=50),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GoodNewsPage:
    return await list_public(db, stage=stage, page=page, page_size=page_size, viewer_id=current.id)


@router.get("/{news_id}", response_model=GoodNewsItem, summary="公开喜讯详情")
async def public_news_detail(
    news_id: int = Path(..., ge=1),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GoodNewsItem:
    return await get_public(db, news_id, current.id)


@router.post("/{news_id}/blessings", response_model=GoodNewsBlessingResponse, summary="送祝福")
async def bless_news(
    news_id: int = Path(..., ge=1),
    current: CurrentUser = Depends(get_realname_verified_user),
    db: AsyncSession = Depends(get_db),
) -> GoodNewsBlessingResponse:
    return await bless(db, news_id=news_id, user_id=current.id)


@router.delete("/{news_id}/blessings", response_model=GoodNewsBlessingResponse, summary="撤销祝福")
async def unbless_news(
    news_id: int = Path(..., ge=1),
    current: CurrentUser = Depends(get_realname_verified_user),
    db: AsyncSession = Depends(get_db),
) -> GoodNewsBlessingResponse:
    return await unbless(db, news_id=news_id, user_id=current.id)


@router.post("/{news_id}/consent/revoke", status_code=204, summary="撤回喜讯展示同意")
async def revoke_news_consent(
    news_id: int = Path(..., ge=1),
    role: Literal["FROM", "TO"] = Query(...),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    if role not in {"FROM", "TO"}:
        raise HTTPException(422, detail="角色不合法")
    await revoke_consent(db, news_id=news_id, user_id=current.id, role=role)

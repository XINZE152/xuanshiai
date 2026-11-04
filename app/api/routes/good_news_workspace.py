"""红娘工作台喜讯接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Header, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas.good_news import (
    GoodNewsItem,
    GoodNewsPage,
    GoodNewsPennantPage,
    GoodNewsPhotoResponse,
    GoodNewsReportCreate,
)
from app.services.good_news import create_report, list_pennants, list_workspace, upload_photo
from app.services.matchmaker_workspace import require_workspace

router = APIRouter(prefix="/matchmaker/workbench/good-news")


@router.get("/pennants", response_model=GoodNewsPennantPage, summary="查询锦旗墙")
async def workspace_pennants(
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(20, ge=1, le=100),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GoodNewsPennantPage:
    actor = await require_workspace(db, current)
    return await list_pennants(db, actor=actor, page=page, page_size=page_size)


@router.post("/photo", response_model=GoodNewsPhotoResponse, status_code=201, summary="上传喜讯图片")
async def workspace_photo(
    file: UploadFile = File(...),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GoodNewsPhotoResponse:
    actor = await require_workspace(db, current)
    return await upload_photo(file, user_id=actor.user_id)


@router.get("", response_model=GoodNewsPage, summary="查询工作范围喜讯")
async def workspace_news(
    stage: str | None = Query(None, max_length=32),
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(20, ge=1, le=100),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GoodNewsPage:
    actor = await require_workspace(db, current)
    return await list_workspace(db, actor=actor, stage=stage, page=page, page_size=page_size)


@router.post("", response_model=GoodNewsItem, status_code=201, summary="红娘代会员登记喜讯")
async def workspace_create(
    body: GoodNewsReportCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=128),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GoodNewsItem:
    actor = await require_workspace(db, current)
    item, _report_id = await create_report(
        db,
        submitter_id=actor.user_id,
        submitter_role="MATCHMAKER",
        body=body,
        matchmaker_id=actor.user_id,
        organization_id=actor.organization_id,
        idempotency_key=idempotency_key,
        actor=actor,
    )
    return item

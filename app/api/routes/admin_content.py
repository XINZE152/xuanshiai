"""Administration APIs for the generic operational content items."""

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentMatchmakerAdmin, get_current_matchmaker_admin
from app.db.session import get_db
from app.schemas.admin_content import ContentItem, ContentItemCreate, ContentItemPage, ContentItemUpdate
from app.services.admin_content import create_item, delete_item, list_items, update_item

router = APIRouter(prefix="/admin/content")


@router.get("/{domain}", response_model=ContentItemPage, summary="查询内容项列表")
async def content_list(
    domain: str = Path(..., min_length=2, max_length=64),
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = Query(None, max_length=64),
    status: int | None = Query(None, ge=1, le=2),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> ContentItemPage:
    current.require("platform.config.read")
    return await list_items(db, domain, page, page_size, keyword, status)


@router.post("/{domain}", response_model=ContentItem, status_code=201, summary="新增内容项")
async def content_create(
    body: ContentItemCreate = ...,
    domain: str = Path(..., min_length=2, max_length=64),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> ContentItem:
    current.require("platform.config.write")
    return await create_item(db, current.account.id, domain, body)


@router.patch("/{domain}/{item_id}", response_model=ContentItem, summary="更新内容项")
async def content_update(
    body: ContentItemUpdate = ...,
    domain: str = Path(..., min_length=2, max_length=64),
    item_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> ContentItem:
    current.require("platform.config.write")
    return await update_item(db, domain, item_id, body)


@router.delete("/{domain}/{item_id}", status_code=204, summary="删除内容项")
async def content_delete(
    domain: str = Path(..., min_length=2, max_length=64),
    item_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    current.require("platform.config.write")
    await delete_item(db, domain, item_id)

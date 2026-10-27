"""兴趣标签目录（分类 + 标签）后台 CRUD 路由。

契约：
- 分类：GET/POST /admin/interest-tags/categories、POST /categories/reorder、
  PATCH/DELETE /categories/{item_id}
- 标签：GET/POST /admin/interest-tags/tags、POST /tags/reorder、
  PATCH/DELETE /tags/{item_id}

目录为全局配置，不做数据范围收敛；均需 `interest_tag.manage` 权限。每次写库成功后
由服务刷新进程内缓存（单进程部署即时生效）。
"""

from fastapi import APIRouter, Depends, Path, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentMatchmakerAdmin, declare_permission, get_current_matchmaker_admin
from app.db.session import get_db
from app.schemas.interest_tag_admin import (
    TagCategoryCreate,
    TagCategoryItem,
    TagCategoryUpdate,
    TagCreate,
    TagItem,
    TagOrder,
    TagUpdate,
)
from app.services import interest_tag_admin as service

router = APIRouter(prefix="/admin/interest-tags")


# ───────────────────────────── 分类 ─────────────────────────────
@declare_permission("interest_tag.manage")
@router.get("/categories", response_model=list[TagCategoryItem], summary="兴趣标签分类列表")
async def category_list(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> list[TagCategoryItem]:
    current.require("interest_tag.manage")
    return await service.list_categories(db)


@declare_permission("interest_tag.manage")
@router.post("/categories/reorder", status_code=204, summary="兴趣标签分类重排")
async def category_reorder(
    body: TagOrder,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    current.require("interest_tag.manage")
    await service.reorder_categories(db, body)


@declare_permission("interest_tag.manage")
@router.post("/categories", response_model=TagCategoryItem, status_code=201, summary="新增兴趣标签分类")
async def category_create(
    body: TagCategoryCreate,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> TagCategoryItem:
    current.require("interest_tag.manage")
    return await service.create_category(db, body)


@declare_permission("interest_tag.manage")
@router.patch("/categories/{item_id}", response_model=TagCategoryItem, summary="修改兴趣标签分类")
async def category_update(
    body: TagCategoryUpdate,
    item_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> TagCategoryItem:
    current.require("interest_tag.manage")
    return await service.update_category(db, item_id, body)


@declare_permission("interest_tag.manage")
@router.delete("/categories/{item_id}", status_code=204, summary="删除兴趣标签分类")
async def category_delete(
    item_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    current.require("interest_tag.manage")
    await service.delete_category(db, item_id)
    return Response(status_code=204)


# ───────────────────────────── 标签 ─────────────────────────────
@declare_permission("interest_tag.manage")
@router.get("/tags", response_model=list[TagItem], summary="兴趣标签列表")
async def tag_list(
    category_key: str | None = Query(None, max_length=64, description="按分类 key 过滤，留空返回全部"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> list[TagItem]:
    current.require("interest_tag.manage")
    return await service.list_tags(db, category_key)


@declare_permission("interest_tag.manage")
@router.post("/tags/reorder", status_code=204, summary="兴趣标签重排")
async def tag_reorder(
    body: TagOrder,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    current.require("interest_tag.manage")
    await service.reorder_tags(db, body)


@declare_permission("interest_tag.manage")
@router.post("/tags", response_model=TagItem, status_code=201, summary="新增兴趣标签")
async def tag_create(
    body: TagCreate,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> TagItem:
    current.require("interest_tag.manage")
    return await service.create_tag(db, body)


@declare_permission("interest_tag.manage")
@router.patch("/tags/{item_id}", response_model=TagItem, summary="修改兴趣标签")
async def tag_update(
    body: TagUpdate,
    item_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> TagItem:
    current.require("interest_tag.manage")
    return await service.update_tag(db, item_id, body)


@declare_permission("interest_tag.manage")
@router.delete("/tags/{item_id}", status_code=204, summary="删除兴趣标签")
async def tag_delete(
    item_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    current.require("interest_tag.manage")
    await service.delete_tag(db, item_id)
    return Response(status_code=204)

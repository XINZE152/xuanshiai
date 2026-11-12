"""原站资源镜像 后台路由。

前缀 ``/admin/ref``；全部只读，走 ``matchmaker.system.read`` 权限。
服务：应用中心 / 婚创学苑 / 红娘课堂 / 用好系统 / 云端图库。
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    CurrentMatchmakerAdmin,
    get_current_matchmaker_admin,
)
from app.db.session import get_db
from app.schemas.admin_ref import (
    RefGuideCategories,
    RefGuideDetail,
    RefGuidePage,
    RefInstructionItem,
    RefMaterialPage,
    RefProduct,
    RefTeacher,
)
from app.services import admin_ref as service


router = APIRouter(prefix="/admin/ref")


@router.get("/products", response_model=list[RefProduct], summary="应用中心插件列表")
async def list_products(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> list[RefProduct]:
    current.require("matchmaker.system.read")
    return await service.list_products(db)


@router.get("/guide-meta", response_model=RefGuideCategories, summary="课程栏目与讲师")
async def guide_meta(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> RefGuideCategories:
    current.require("matchmaker.system.read")
    return await service.guide_categories(db)


@router.get("/guides", response_model=RefGuidePage, summary="课程/资料列表")
async def list_guides(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    category: str | None = Query(None, max_length=64),
    teacher_ref_id: int | None = Query(None, ge=1),
    guide_type: str | None = Query(None, max_length=16),
    keyword: str | None = Query(None, max_length=64),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> RefGuidePage:
    current.require("matchmaker.system.read")
    return await service.list_guides(
        db,
        page=page,
        page_size=page_size,
        category=category,
        teacher_ref_id=teacher_ref_id,
        guide_type=guide_type,
        keyword=keyword,
    )


@router.get("/guides/{guide_id}", response_model=RefGuideDetail, summary="课程详情")
async def get_guide(
    guide_id: int,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> RefGuideDetail:
    current.require("matchmaker.system.read")
    return await service.get_guide(db, guide_id)


@router.get("/instructions", response_model=list[RefInstructionItem], summary="用好系统手册列表")
async def list_instructions(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> list[RefInstructionItem]:
    current.require("matchmaker.system.read")
    return await service.list_instructions(db)


@router.get("/materials", response_model=RefMaterialPage, summary="云端图库素材列表")
async def list_materials(
    page: int = Query(1, ge=1, le=100000),
    page_size: int = Query(20, ge=1, le=100),
    category: str | None = Query(None, max_length=64),
    sub_category: str | None = Query(None, max_length=64),
    keyword: str | None = Query(None, max_length=64),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> RefMaterialPage:
    current.require("matchmaker.system.read")
    return await service.list_materials(
        db,
        page=page,
        page_size=page_size,
        category=category,
        sub_category=sub_category,
        keyword=keyword,
    )

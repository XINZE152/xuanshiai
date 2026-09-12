"""Mutual-selection (互选) activity routes for the back office (M7-A)."""

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentMatchmakerAdmin, get_current_matchmaker_admin
from app.db.session import get_db
from app.schemas.mutual_selection_admin import (
    MutualActivityCreate,
    MutualActivityItem,
    MutualActivityPage,
    MutualActivityUpdate,
    MutualOption,
    MutualParticipant,
    MutualParticipantAdd,
    MutualRecordPage,
)
from app.services import mutual_selection_admin as service

router = APIRouter(prefix="/admin/mutual-activities")
record_router = APIRouter(prefix="/admin/mutual-records")


@router.get("", response_model=MutualActivityPage, summary="查询互选活动列表")
async def mutual_activities(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = Query(None, max_length=128),
    status: int | None = Query(None, ge=1, le=4),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MutualActivityPage:
    current.require("community.activity.read")
    return await service.list_activities(db, page, page_size, keyword, status)


@router.post("", response_model=MutualActivityItem, status_code=201, summary="创建互选活动")
async def create_mutual_activity(
    body: MutualActivityCreate,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MutualActivityItem:
    current.require("community.activity.manage")
    return await service.create_activity(db, current.account.id, body)


@router.get("/{activity_id}/participants", response_model=list[MutualParticipant], summary="互选活动参与嘉宾")
async def mutual_participants(
    activity_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> list[MutualParticipant]:
    current.require("community.activity.read")
    return await service.list_participants(db, activity_id)


@router.post("/{activity_id}/participants", response_model=MutualParticipant, status_code=201, summary="添加互选参与嘉宾")
async def add_mutual_participant(
    activity_id: int = Path(..., ge=1),
    body: MutualParticipantAdd = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MutualParticipant:
    current.require("community.activity.manage")
    return await service.add_participant(db, activity_id, body)


@router.delete("/{activity_id}/participants/{user_id}", status_code=204, summary="移除互选参与嘉宾")
async def remove_mutual_participant(
    activity_id: int = Path(..., ge=1),
    user_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    current.require("community.activity.manage")
    await service.remove_participant(db, activity_id, user_id)


@router.post("/{activity_id}/copy", response_model=MutualActivityItem, status_code=201, summary="复制互选活动")
async def copy_mutual_activity(
    activity_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MutualActivityItem:
    current.require("community.activity.manage")
    return await service.copy_activity(db, current.account.id, activity_id)


@router.patch("/{activity_id}/visible", response_model=MutualActivityItem, summary="上线/下线互选活动")
async def set_mutual_visible(
    activity_id: int = Path(..., ge=1),
    visible: bool = Query(...),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MutualActivityItem:
    current.require("community.activity.manage")
    return await service.set_visible(db, activity_id, visible)


@router.patch("/{activity_id}", response_model=MutualActivityItem, summary="修改互选活动")
async def update_mutual_activity(
    activity_id: int = Path(..., ge=1),
    body: MutualActivityUpdate = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MutualActivityItem:
    current.require("community.activity.manage")
    return await service.update_activity(db, activity_id, body)


@router.delete("/{activity_id}", status_code=204, summary="删除互选活动")
async def delete_mutual_activity(
    activity_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    current.require("community.activity.manage")
    await service.delete_activity(db, activity_id)


@router.get("/{activity_id}", response_model=MutualActivityItem, summary="查询互选活动详情")
async def mutual_activity_detail(
    activity_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MutualActivityItem:
    current.require("community.activity.read")
    return await service._get(db, activity_id)


@record_router.get("", response_model=MutualRecordPage, summary="查询互选记录")
async def mutual_records(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    activity_id: int | None = Query(None, ge=1),
    actor_id: int | None = Query(None, ge=1),
    keyword: str | None = Query(None, max_length=64),
    result: str | None = Query(None, pattern="^(success|fail|none)$"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MutualRecordPage:
    current.require("community.activity.read")
    return await service.list_records(db, page, page_size, activity_id, actor_id, keyword, result)


@record_router.get("/options", summary="互选记录筛选下拉字典")
async def mutual_record_options(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, list[MutualOption]]:
    current.require("community.activity.read")
    return await service.record_options(db)

"""会员资料媒体验证（M3-2）管理后台路由。

前缀 ``/admin/members``；所有读接口需 ``matchmaker.member.read``，
写接口需 ``matchmaker.member.manage``（依赖注入已按路径自动映射，这里再显式声明）。
注意：静态路径（/media/intros、/media）必须声明在动态路径
（/media/{media_id}）之前，避免被动态路由抢占。
"""

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentMatchmakerAdmin, get_current_matchmaker_admin
from app.db.session import get_db
from app.schemas.member_media_admin import (
    MemberIntroItem,
    MemberIntroPage,
    MemberIntroUpdate,
    MemberMediaItem,
    MemberMediaPage,
    MemberMediaReplace,
    MemberMediaReview,
)
from app.services import member_media_admin as service

router = APIRouter(prefix="/admin/members")


# ─── 个人介绍（自白内容） ───────────────────────────────────────


@router.get("/media/intros", response_model=MemberIntroPage, summary="个人介绍（自白）列表")
async def intro_list(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = Query(None, max_length=64),
    letter_mode: str | None = Query(None, pattern="^(has|none)$"),
    letter_lower: bool = Query(False),
    digit: bool = Query(False),
    cn_digit: bool = Query(False),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MemberIntroPage:
    current.require("matchmaker.member.read")
    return await service.list_intros(db, page, page_size, keyword, letter_mode, letter_lower, digit, cn_digit)


@router.put("/media/intros/{user_id}", response_model=MemberIntroItem, summary="更新个人介绍（自白）")
async def intro_update(
    user_id: int = Path(..., ge=1),
    body: MemberIntroUpdate = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MemberIntroItem:
    current.require("matchmaker.member.manage")
    return await service.update_intro(db, user_id, body, current.account.id)


# ─── 媒体（头像/照片/视频） ─────────────────────────────────────


@router.get("/media", response_model=MemberMediaPage, summary="媒体审核列表（头像/照片/视频）")
async def media_list(
    media_type: str = Query(..., pattern="^(avatar|photo|video)$"),
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    review_status: int | None = Query(None, ge=0, le=3),
    keyword: str | None = Query(None, max_length=64),
    gender: int | None = Query(None, ge=1, le=2),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MemberMediaPage:
    current.require("matchmaker.member.read")
    return await service.list_media(db, page, page_size, media_type, review_status, keyword, gender)


@router.get("/media/{user_id}/history", response_model=MemberMediaPage, summary="会员头像历史记录（含已删）")
async def media_history(
    user_id: int = Path(..., ge=1),
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MemberMediaPage:
    current.require("matchmaker.member.read")
    return await service.list_avatar_history(db, user_id, page, page_size)


@router.patch("/media/{media_id}", response_model=dict, summary="媒体审核（通过/未通过/隐藏）")
async def media_review(
    media_id: int = Path(..., ge=1),
    body: MemberMediaReview = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    current.require("matchmaker.member.manage")
    return await service.review_media(db, media_id, body, current.account.id)


@router.put("/media/{media_id}", response_model=dict, summary="媒体重新上传（归零待审）")
async def media_replace(
    media_id: int = Path(..., ge=1),
    body: MemberMediaReplace = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    current.require("matchmaker.member.manage")
    return await service.replace_media(db, media_id, body, current.account.id)


@router.delete("/media/{media_id}", response_model=dict, summary="媒体软删除")
async def media_delete(
    media_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    current.require("matchmaker.member.manage")
    return await service.delete_media(db, media_id, current.account.id)

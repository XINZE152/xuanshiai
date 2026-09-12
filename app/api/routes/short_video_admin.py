"""Short-video (短视频) routes for the back office (M7-C)."""

from fastapi import APIRouter, Body, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentMatchmakerAdmin, get_current_matchmaker_admin
from app.db.session import get_db
from app.schemas.short_video_admin import (
    RedPacketClaimItem,
    RedPacketPage,
    ShortVideoCreate,
    ShortVideoItem,
    ShortVideoPage,
    ShortVideoUpdate,
    VideoBrushRequest,
    VideoBrushResult,
    VideoCategoryCreate,
    VideoCategoryItem,
    VideoCategoryUpdate,
    VideoCommentBatchDelete,
    VideoCommentItem,
    VideoCommentPage,
    VideoCommentUpdate,
    VideoHomepageItem,
    VideoHomepagePage,
    VideoTipPage,
)
from app.services import short_video_admin as service

video_router = APIRouter(prefix="/admin/short-videos")
category_router = APIRouter(prefix="/admin/short-video-categories")
comment_router = APIRouter(prefix="/admin/short-video-comments")
tip_router = APIRouter(prefix="/admin/short-video-tips")
packet_router = APIRouter(prefix="/admin/video-red-packets")
homepage_router = APIRouter(prefix="/admin/short-video-homepages")


# ─────────────────────────── 分类 ───────────────────────────


@category_router.get("", response_model=list[VideoCategoryItem], summary="短视频分类列表")
async def category_list(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> list[VideoCategoryItem]:
    current.require("video.read")
    return await service.list_categories(db)


@category_router.post("", response_model=VideoCategoryItem, status_code=201, summary="新增短视频分类")
async def category_create(
    body: VideoCategoryCreate,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> VideoCategoryItem:
    current.require("video.manage")
    return await service.create_category(db, body)


@category_router.patch("/{category_id}", response_model=VideoCategoryItem, summary="修改短视频分类")
async def category_update(
    category_id: int = Path(..., ge=1),
    body: VideoCategoryUpdate = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> VideoCategoryItem:
    current.require("video.manage")
    return await service.update_category(db, category_id, body)


@category_router.delete("/{category_id}", status_code=204, summary="删除短视频分类")
async def category_delete(
    category_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    current.require("video.manage")
    await service.delete_category(db, category_id)


# ─────────────────────────── 视频 ───────────────────────────


@video_router.get("", response_model=ShortVideoPage, summary="查询视频列表")
async def video_list(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    audit_status: str | None = Query(None, pattern="^(pending|approved|rejected)$"),
    category_id: int | None = Query(None, ge=1),
    flag: str | None = Query(None, pattern="^(top|recommend|hot)$"),
    keyword: str | None = Query(None, max_length=128),
    order_by: str | None = Query(None, pattern="^(publish|views)$"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> ShortVideoPage:
    current.require("video.read")
    return await service.list_videos(db, page, page_size, audit_status, category_id, flag, keyword, order_by)


@video_router.post("", response_model=ShortVideoItem, status_code=201, summary="新增视频")
async def video_create(
    body: ShortVideoCreate,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> ShortVideoItem:
    current.require("video.manage")
    return await service.create_video(db, body)


@video_router.post("/brush", response_model=VideoBrushResult, summary="数据刷粉（人气/点赞/刷新发布时间）")
async def video_brush(
    body: VideoBrushRequest,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> VideoBrushResult:
    current.require("video.manage")
    return await service.brush_videos(db, body)


@video_router.patch("/{video_id}", response_model=ShortVideoItem, summary="修改视频/审核/开关")
async def video_update(
    video_id: int = Path(..., ge=1),
    body: ShortVideoUpdate = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> ShortVideoItem:
    current.require("video.manage")
    return await service.update_video(db, video_id, body)


@video_router.delete("/{video_id}", status_code=204, summary="删除视频")
async def video_delete(
    video_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    current.require("video.manage")
    await service.delete_video(db, video_id)


@video_router.get("/{video_id}", response_model=ShortVideoItem, summary="视频详情")
async def video_detail(
    video_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> ShortVideoItem:
    current.require("video.read")
    return await service._get_video(db, video_id)


# ─────────────────────────── 评论 ───────────────────────────


@comment_router.get("", response_model=VideoCommentPage, summary="查询视频评论")
async def comment_list(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    audit_status: str | None = Query(None, pattern="^(pending|approved|rejected)$"),
    keyword: str | None = Query(None, max_length=128),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> VideoCommentPage:
    current.require("video.read")
    return await service.list_comments(db, page, page_size, audit_status, keyword)


@comment_router.post("/batch-delete", summary="批量删除评论")
async def comment_batch_delete(
    body: VideoCommentBatchDelete = Body(...),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    current.require("video.manage")
    deleted = await service.batch_delete_comments(db, body.ids)
    return {"deleted": deleted}


@comment_router.patch("/{comment_id}", response_model=VideoCommentItem, summary="修改/审核评论")
async def comment_update(
    comment_id: int = Path(..., ge=1),
    body: VideoCommentUpdate = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> VideoCommentItem:
    current.require("video.manage")
    return await service.update_comment(db, comment_id, body)


@comment_router.delete("/{comment_id}", status_code=204, summary="删除评论")
async def comment_delete(
    comment_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    current.require("video.manage")
    await service.delete_comment(db, comment_id)


# ─────────────────────────── 打赏 ───────────────────────────


@tip_router.get("", response_model=VideoTipPage, summary="查询短视频打赏")
async def tip_list(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = Query(None, max_length=64),
    search_by: str | None = Query(None, pattern="^(video|user)$"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> VideoTipPage:
    current.require("video.read")
    return await service.list_tips(db, page, page_size, keyword, search_by)


# ─────────────────────────── 红包 ───────────────────────────


@packet_router.get("", response_model=RedPacketPage, summary="查询短视频红包记录")
async def packet_list(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    claim_status: str | None = Query(None, pattern="^(finished|unfinished)$"),
    keyword: str | None = Query(None, max_length=128),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> RedPacketPage:
    current.require("video.read")
    return await service.list_packets(db, page, page_size, claim_status, keyword)


@packet_router.get("/{packet_id}/claims", response_model=list[RedPacketClaimItem], summary="红包领取明细")
async def packet_claims(
    packet_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> list[RedPacketClaimItem]:
    current.require("video.read")
    return await service.list_packet_claims(db, packet_id)


# ─────────────────────────── 会员主页 ───────────────────────────


@homepage_router.get("", response_model=VideoHomepagePage, summary="查询短视频会员主页列表")
async def homepage_list(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = Query(None, max_length=64),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> VideoHomepagePage:
    current.require("video.read")
    return await service.list_homepages(db, page, page_size, keyword)


@homepage_router.patch("/{homepage_id}", response_model=VideoHomepageItem, summary="修改会员主页（认证/资料）")
async def homepage_update(
    homepage_id: int = Path(..., ge=1),
    certified: bool | None = Query(None),
    wechat: str | None = Query(None, max_length=64),
    bio: str | None = Query(None, max_length=255),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> VideoHomepageItem:
    current.require("video.manage")
    if certified is not None and wechat is None and bio is None:
        return await service.set_homepage_certified(db, homepage_id, certified)
    return await service.update_homepage(db, homepage_id, wechat, bio)


@homepage_router.delete("/{homepage_id}", status_code=204, summary="删除会员主页")
async def homepage_delete(
    homepage_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    current.require("video.manage")
    await service.delete_homepage(db, homepage_id)

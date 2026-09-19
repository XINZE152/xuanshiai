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
    MemberPreferenceAdminItem,
    MemberPreferenceAdminUpdate,
    MemberProfileExtItem,
    MemberProfileExtUpdate,
    MemberRecommendPage,
)
from app.services import member_media_admin as service

router = APIRouter(prefix="/admin/members")


# ─── 资料扩展字段（性格/爱好/MBTI/自我介绍/红娘说） ─────────────
# 注意：静态前缀 /profile-ext 必须先于任何 /{user_id} 形态的动态路径声明。


@router.get("/profile-ext/{user_id}", response_model=MemberProfileExtItem, summary="查询会员资料扩展字段")
async def profile_ext_get(
    user_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MemberProfileExtItem:
    """返回会员的性格标签、爱好、MBTI、自我介绍与红娘说。"""
    current.require("matchmaker.member.read")
    return await service.get_profile_ext(db, user_id)


@router.put("/profile-ext/{user_id}", response_model=MemberProfileExtItem, summary="更新会员资料扩展字段")
async def profile_ext_update(
    user_id: int = Path(..., ge=1),
    body: MemberProfileExtUpdate = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MemberProfileExtItem:
    """按提交字段增量更新；性格标签传 [] 清空。写审计 member.profile_ext.update。"""
    current.require("matchmaker.member.manage")
    return await service.update_profile_ext(db, user_id, body, current.account.id)


# ─── 择偶要求（后台管理端） ─────────────────────────────────────


@router.get("/preference/{user_id}", response_model=MemberPreferenceAdminItem, summary="查询会员择偶要求")
async def preference_get(
    user_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MemberPreferenceAdminItem:
    """返回会员择偶要求：年龄/身高区间 + 收入/学历/职业/婚况/住房/吸烟/喝酒/结婚计划单选 + 补充说明。"""
    current.require("matchmaker.member.read")
    return await service.get_member_preference(db, user_id)


@router.put("/preference/{user_id}", response_model=MemberPreferenceAdminItem, summary="更新会员择偶要求")
async def preference_update(
    user_id: int = Path(..., ge=1),
    body: MemberPreferenceAdminUpdate = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MemberPreferenceAdminItem:
    """按提交字段增量更新（全部为后台 UI 单选白名单枚举）。写审计 member.preference.update。"""
    current.require("matchmaker.member.manage")
    return await service.update_member_preference(db, user_id, body, current.account.id)


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


# ─── 会员推荐（后台按条件筛候选人，强制异性） ───────────────────


@router.get("/{user_id}/recommendations", response_model=MemberRecommendPage, summary="为会员筛选推荐候选人")
async def member_recommendations(
    user_id: int = Path(..., ge=1),
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    store_id: int | None = Query(None, ge=1, description="门店（organization.id），不限不传"),
    matchmaker_id: int | None = Query(None, ge=1, description="服务红娘后台账号 ID，不限不传"),
    smoking: str | None = Query(None, max_length=32, description="抽烟，精确匹配，不限不传"),
    drinking: str | None = Query(None, max_length=32, description="喝酒，精确匹配，不限不传"),
    house: str | None = Query(None, max_length=32, description="住房，精确匹配，不限不传"),
    marriage: int | None = Query(None, ge=1, le=3, description="婚况：1未婚 2离异 3丧偶"),
    ethnicity: str | None = Query(None, max_length=32, description="民族，精确匹配"),
    constellation: str | None = Query(None, max_length=16, description="星座，精确匹配"),
    hometown: str | None = Query(None, max_length=128, description="家乡，精确匹配"),
    residence: str | None = Query(None, max_length=128, description="现居，精确匹配"),
    occupations: list[str] | None = Query(None, max_length=10, description="职业多选，最多 10 项"),
    mbti: str | None = Query(None, max_length=16, description="人格类型（MBTI），精确匹配"),
    tags: list[str] | None = Query(None, max_length=12, description="标签多选（命中 tags/interest_tags/personality_tags 任一即可）"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MemberRecommendPage:
    """为指定会员筛选推荐候选人：强制异性、状态正常、排除本人；其余条件可选。"""
    current.require("matchmaker.member.read")
    return await service.list_member_recommendations(
        db,
        user_id,
        page=page,
        page_size=page_size,
        store_id=store_id,
        matchmaker_id=matchmaker_id,
        smoking=smoking,
        drinking=drinking,
        house=house,
        marriage=marriage,
        ethnicity=ethnicity,
        constellation=constellation,
        hometown=hometown,
        residence=residence,
        occupations=occupations,
        mbti=mbti,
        tags=tags,
    )


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

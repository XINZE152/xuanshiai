"""匿名游客预览服务（GUEST-PREVIEW-20261115）。

为小程序游客态提供真实内容浏览：推荐名片 / 社区动态 / 线下活动。
三个入口均匿名只读，不写任何状态：

- 候选资格复用共享可见性策略（guest 视角仅放行公开、完整、过审、活跃用户），
  不新增政策、不开豁免；
- 平台浏览总开关与按 IP 固定窗口限流，与登录态同一套设施；
- 脱敏白名单在 app/schemas/public_preview.py，本模块负责投影与截断。
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import consume_rate_limit
from app.schemas.discovery import DiscoveryCard, DiscoveryFilters
from app.schemas.public_preview import (
    PublicActivityPreview,
    PublicActivityPreviewPage,
    PublicCommunityPreviewPage,
    PublicDiscoveryPreviewPage,
    PublicPostPreview,
    PublicPreviewCard,
)
from app.services import community as community_service
from app.services import discovery as discovery_service
from app.services.candidate_visibility import (
    CandidateVisibilityService,
    ViewerContext,
    VisibilityScene,
)
from app.services.profile import _json_list
from app.services.runtime_config import platform_browse_open, platform_maintenance_message

PUBLIC_PREVIEW_RATE_LIMIT = 30
PUBLIC_PREVIEW_WINDOW_SECONDS = 60

# guest 无身份/偏好/实名：性别门禁与择偶偏好在调用处显式关闭。
_GUEST_VIEWER: dict[str, Any] = {"gender": None, "realname_status": 0}
_GUEST_VIEWER_CONTEXT = ViewerContext(user_id=0, realname_status=0, is_vip=False)

_visibility_service = CandidateVisibilityService()


def mask_nickname(nickname: str | None) -> str | None:
    """游客态昵称脱敏：首字+**；空值返回 None。"""
    if nickname is None:
        return None
    value = nickname.strip()
    if value == "":
        return None
    return value[0] + "**"


async def _ensure_browse_open(db: AsyncSession) -> None:
    if not await platform_browse_open(db):
        raise HTTPException(status_code=403, detail=await platform_maintenance_message(db))


async def _ensure_rate_limit(ip: str) -> None:
    allowed = await consume_rate_limit(
        f"public:preview:{ip}", PUBLIC_PREVIEW_RATE_LIMIT, PUBLIC_PREVIEW_WINDOW_SECONDS
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "PUBLIC_PREVIEW_RATE_LIMITED",
                "message": "浏览太频繁，请稍后再试",
                "retryable": True,
            },
        )


def project_preview_card(card: DiscoveryCard) -> PublicPreviewCard:
    """登录态卡片投影到游客白名单：脱敏、截断、恒锁定、个人标记恒 false。"""
    return PublicPreviewCard(
        user_id=card.user_id,
        nickname=mask_nickname(card.nickname),
        avatar=card.avatar,
        gender=card.gender,
        age=card.age,
        residence_display=card.residence_display,
        education_level=card.education_level,
        occupation=card.occupation,
        mbti=card.mbti,
        interest_tags=card.interest_tags[:3],
        certification_tags=card.certification_tags,
        is_vip=card.is_vip,
        detail_locked=True,
        is_liked=False,
        is_favorite=False,
    )


async def get_public_discovery_preview(
    db: AsyncSession, *, ip: str, page: int, page_size: int
) -> PublicDiscoveryPreviewPage:
    await _ensure_browse_open(db)
    await _ensure_rate_limit(ip)
    filters = DiscoveryFilters(page=page, page_size=page_size)
    # 不走 _viewer_context（user 0 会 404）；plaza 口径避免「跳过已滑过」依赖真实 viewer。
    snapshot = discovery_service._candidate_snapshot(
        viewer_id=0,
        viewer=dict(_GUEST_VIEWER),
        viewer_is_vip=False,
        filters=filters,
        plaza=True,
        nickname=None,
        tag=None,
        respect_preferences=False,
        apply_intent_gender_gate=False,
    )
    candidate_page = await discovery_service.candidate_query_service.fetch_page(
        db, snapshot, cursor=None, page_size=page_size
    )
    items = [
        project_preview_card(discovery_service._card(row, 0.0, "", detail_locked=False))
        for row in candidate_page.items
    ]
    return PublicDiscoveryPreviewPage(
        items=items,
        page=candidate_page.page,
        page_size=candidate_page.page_size,
        total=candidate_page.total,
        has_more=candidate_page.has_more,
    )


_POST_PREVIEW_FROM = """
    FROM community_post p
    JOIN users u ON u.id = p.user_id
    LEFT JOIN community_topic t ON t.id = p.topic_id
    LEFT JOIN user_privacy pr ON pr.user_id = p.user_id
    LEFT JOIN user_profile_completion c ON c.user_id = p.user_id
"""


def _post_preview_where() -> tuple[str, dict[str, int]]:
    visibility = _visibility_service.predicate(
        _GUEST_VIEWER_CONTEXT,
        VisibilityScene.DISCOVERY,
        candidate_alias="u",
        privacy_alias="pr",
        completion_alias="c",
    )
    clause = (
        "p.status = 1 AND COALESCE(p.moderation_status, 1) = 1 AND p.deleted_at IS NULL "
        "AND p.visibility = 0 AND COALESCE(pr.show_posts, 1) = 1 AND " + visibility.clause
    )
    return clause, dict(visibility.params)


async def get_public_community_preview(
    db: AsyncSession, *, ip: str, page: int, page_size: int
) -> PublicCommunityPreviewPage:
    await _ensure_browse_open(db)
    await _ensure_rate_limit(ip)
    where, visibility_params = _post_preview_where()
    result = await db.execute(
        text(
            "SELECT p.id, u.nickname, u.avatar, p.content, p.images, t.name AS topic_name, "
            "p.like_count, p.comment_count, p.created_at"
            + _POST_PREVIEW_FROM
            + f" WHERE {where} ORDER BY p.created_at DESC, p.id DESC "
            "LIMIT :limit OFFSET :offset"
        ),
        {**visibility_params, "limit": page_size, "offset": (page - 1) * page_size},
    )
    rows = [dict(row) for row in result.mappings().all()]
    count_result = await db.execute(
        text("SELECT COUNT(*) AS total" + _POST_PREVIEW_FROM + f" WHERE {where}"),
        visibility_params,
    )
    total = int((count_result.mappings().first() or {"total": 0})["total"])
    items = [
        PublicPostPreview(
            id=int(row["id"]),
            author_nickname=mask_nickname(row.get("nickname")),
            author_avatar=row.get("avatar"),
            content=row.get("content") or "",
            images=_json_list(row.get("images"))[:3],
            topic_name=row.get("topic_name"),
            like_count=int(row.get("like_count") or 0),
            comment_count=int(row.get("comment_count") or 0),
            created_at=row["created_at"],
        )
        for row in rows
    ]
    return PublicCommunityPreviewPage(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
    )


async def get_public_activities_preview(
    db: AsyncSession, *, ip: str, page: int, page_size: int
) -> PublicActivityPreviewPage:
    await _ensure_browse_open(db)
    await _ensure_rate_limit(ip)
    activity_page = await community_service.list_activities(
        db, 0, filter_key="recruiting", page=page, page_size=page_size
    )
    items = [
        PublicActivityPreview(
            id=activity.id,
            title=activity.title,
            cover=activity.cover,
            type=activity.type,
            city=activity.city,
            start_time=activity.start_time,
            end_time=activity.end_time,
            signup_deadline=activity.signup_deadline,
            max_people=activity.max_people,
            current_people=activity.current_people,
            price=activity.price,
            status_text=activity.status_text,
            description=activity.description,
        )
        for activity in activity_page.items
    ]
    return PublicActivityPreviewPage(
        items=items,
        page=activity_page.page,
        page_size=activity_page.page_size,
        total=activity_page.total,
        has_more=activity_page.page * activity_page.page_size < activity_page.total,
    )
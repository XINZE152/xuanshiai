"""Community topic and banner operations for the back office."""

import json

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentMatchmakerAdmin, get_current_matchmaker_admin
from app.db.session import get_db
from app.schemas.community import (
    CommunityBannerAdminCreate,
    CommunityBannerAdminResponse,
    CommunityBannerAdminUpdate,
    CommunityTopicAdminCreate,
    CommunityTopicAdminUpdate,
    CommunityTopicResponse,
)

router = APIRouter(prefix="/admin/community")


def _require_global_write(current: CurrentMatchmakerAdmin) -> None:
    if current.account.data_scope != "ALL" and "*" not in current.permissions:
        raise HTTPException(status_code=403, detail="社区运营配置仅允许全局范围管理员修改")


async def _audit(
    db: AsyncSession,
    current: CurrentMatchmakerAdmin,
    action: str,
    resource_type: str,
    resource_id: int,
    *,
    before: dict | None = None,
    after: dict | None = None,
) -> None:
    await db.execute(
        text("""INSERT INTO business_audit_log
            (actor_user_id, action, resource_type, resource_id, before_json, after_json)
            VALUES (:actor, :action, :resource_type, :resource_id, :before_json, :after_json)"""),
        {
            "actor": current.account.id,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "before_json": json.dumps(before, ensure_ascii=False, default=str) if before else None,
            "after_json": json.dumps(after, ensure_ascii=False, default=str) if after else None,
        },
    )


def _topic_response(row: dict) -> CommunityTopicResponse:
    return CommunityTopicResponse(
        **row,
        post_count=int(row.get("post_count") or 0),
        participant_count=int(row.get("participant_count") or 0),
        heat=int(row.get("heat") or 0),
        joined=False,
    )


@router.get("/topics", response_model=list[CommunityTopicResponse], summary="查询社区话题配置")
async def topics(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> list[CommunityTopicResponse]:
    rows = (await db.execute(text("""SELECT topic.id, topic.name, topic.icon, topic.sort,
            topic.is_active, topic.created_at,
            COUNT(DISTINCT post.id) AS post_count,
            COUNT(DISTINCT participant.user_id) AS participant_count,
            COUNT(DISTINCT post.id) + COUNT(DISTINCT participant.user_id) AS heat
        FROM community_topic topic
        LEFT JOIN community_post post ON post.topic_id=topic.id AND post.status=1
        LEFT JOIN community_topic_participant participant ON participant.topic_id=topic.id
        GROUP BY topic.id ORDER BY topic.sort DESC, topic.id DESC"""))).mappings().all()
    return [_topic_response(dict(row)) for row in rows]


@router.post("/topics", response_model=CommunityTopicResponse, status_code=201, summary="创建社区话题")
async def create_topic(
    body: CommunityTopicAdminCreate,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> CommunityTopicResponse:
    _require_global_write(current)
    duplicate = await db.execute(text("SELECT id FROM community_topic WHERE name=:name"), {"name": body.name})
    if duplicate.scalar():
        raise HTTPException(409, detail="话题名称已存在")
    values = {**body.model_dump(), "is_active": int(body.is_active)}
    result = await db.execute(
        text("""INSERT INTO community_topic (name, icon, sort, is_active)
            VALUES (:name, :icon, :sort, :is_active)"""),
        values,
    )
    topic_id = int(result.lastrowid)
    await _audit(db, current, "community.topic.create", "community_topic", topic_id, after=values)
    await db.commit()
    row = (await db.execute(text("""SELECT id, name, icon, sort, is_active, created_at
        FROM community_topic WHERE id=:id"""), {"id": topic_id})).mappings().one()
    return _topic_response(dict(row))


@router.patch("/topics/{topic_id}", response_model=CommunityTopicResponse, summary="更新社区话题")
async def update_topic(
    topic_id: int = Path(..., ge=1),
    body: CommunityTopicAdminUpdate = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> CommunityTopicResponse:
    _require_global_write(current)
    existing = (await db.execute(text("""SELECT id, name, icon, sort, is_active, created_at
        FROM community_topic WHERE id=:id FOR UPDATE"""), {"id": topic_id})).mappings().first()
    if not existing:
        raise HTTPException(404, detail="社区话题不存在")
    duplicate = await db.execute(
        text("SELECT id FROM community_topic WHERE name=:name AND id<>:id"),
        {"name": body.name, "id": topic_id},
    )
    if duplicate.scalar():
        raise HTTPException(409, detail="话题名称已存在")
    values = {**body.model_dump(), "is_active": int(body.is_active), "id": topic_id}
    await db.execute(text("""UPDATE community_topic SET name=:name, icon=:icon, sort=:sort,
        is_active=:is_active WHERE id=:id"""), values)
    await _audit(
        db,
        current,
        "community.topic.update",
        "community_topic",
        topic_id,
        before=dict(existing),
        after={key: value for key, value in values.items() if key != "id"},
    )
    await db.commit()
    row = (await db.execute(text("""SELECT id, name, icon, sort, is_active, created_at
        FROM community_topic WHERE id=:id"""), {"id": topic_id})).mappings().one()
    return _topic_response(dict(row))


@router.get("/banners", response_model=list[CommunityBannerAdminResponse], summary="查询社区 Banner 配置")
async def banners(
    position: str | None = Query(None, max_length=32),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> list[CommunityBannerAdminResponse]:
    where = "WHERE 1=1"
    params: dict[str, object] = {}
    if position:
        where += " AND position=:position"
        params["position"] = position
    rows = (await db.execute(text(f"""SELECT id, title, image_url, link_type, link_value,
        sort, position, is_active, start_at, end_at FROM config_banner {where}
        ORDER BY sort DESC, id DESC"""), params)).mappings().all()
    return [CommunityBannerAdminResponse(**dict(row)) for row in rows]


@router.post("/banners", response_model=CommunityBannerAdminResponse, status_code=201, summary="创建社区 Banner")
async def create_banner(
    body: CommunityBannerAdminCreate,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> CommunityBannerAdminResponse:
    _require_global_write(current)
    values = {**body.model_dump(), "is_active": int(body.is_active)}
    result = await db.execute(text("""INSERT INTO config_banner
        (title, image_url, link_type, link_value, sort, position, is_active, start_at, end_at)
        VALUES (:title, :image_url, :link_type, :link_value, :sort, :position, :is_active, :start_at, :end_at)"""), values)
    banner_id = int(result.lastrowid)
    await _audit(db, current, "community.banner.create", "config_banner", banner_id, after=values)
    await db.commit()
    row = (await db.execute(text("""SELECT id, title, image_url, link_type, link_value, sort,
        position, is_active, start_at, end_at FROM config_banner WHERE id=:id"""), {"id": banner_id})).mappings().one()
    return CommunityBannerAdminResponse(**dict(row))


@router.patch("/banners/{banner_id}", response_model=CommunityBannerAdminResponse, summary="更新社区 Banner")
async def update_banner(
    banner_id: int = Path(..., ge=1),
    body: CommunityBannerAdminUpdate = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> CommunityBannerAdminResponse:
    _require_global_write(current)
    existing = (await db.execute(text("""SELECT id, title, image_url, link_type, link_value,
        sort, position, is_active, start_at, end_at FROM config_banner WHERE id=:id FOR UPDATE"""), {"id": banner_id})).mappings().first()
    if not existing:
        raise HTTPException(404, detail="社区 Banner 不存在")
    values = {**body.model_dump(), "is_active": int(body.is_active), "id": banner_id}
    await db.execute(text("""UPDATE config_banner SET title=:title, image_url=:image_url,
        link_type=:link_type, link_value=:link_value, sort=:sort, position=:position,
        is_active=:is_active, start_at=:start_at, end_at=:end_at, updated_at=UTC_TIMESTAMP()
        WHERE id=:id"""), values)
    await _audit(
        db,
        current,
        "community.banner.update",
        "config_banner",
        banner_id,
        before=dict(existing),
        after={key: value for key, value in values.items() if key != "id"},
    )
    await db.commit()
    row = (await db.execute(text("""SELECT id, title, image_url, link_type, link_value, sort,
        position, is_active, start_at, end_at FROM config_banner WHERE id=:id"""), {"id": banner_id})).mappings().one()
    return CommunityBannerAdminResponse(**dict(row))

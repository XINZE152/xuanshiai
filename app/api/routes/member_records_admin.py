"""Read-only record feeds used by the member CRM detail workspace."""

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentMatchmakerAdmin, get_current_matchmaker_admin
from app.db.session import get_db

router = APIRouter(prefix="/admin/members")


async def _page(db: AsyncSession, query: str, count_query: str, member_id: int, page: int, page_size: int) -> dict:
    params = {"id": member_id, "limit": page_size, "offset": (page - 1) * page_size}
    rows = await db.execute(text(query), params)
    total = int((await db.scalar(text(count_query), {"id": member_id})) or 0)
    return {"items": [dict(row) for row in rows.mappings().all()], "page": page, "page_size": page_size, "total": total, "has_more": page * page_size < total}


async def _ensure_member(db: AsyncSession, member_id: int) -> None:
    from fastapi import HTTPException
    if not await db.scalar(text("SELECT 1 FROM users WHERE id = :id"), {"id": member_id}):
        raise HTTPException(404, detail="会员不存在")


def _paging():
    return (Query(1, ge=1, le=1000), Query(20, ge=1, le=100))


@router.get("/{member_id}/match-records")
async def match_records(member_id: int = Path(..., ge=1), page: int = _paging()[0], page_size: int = _paging()[1], current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> dict:
    await _ensure_member(db, member_id)
    return await _page(db, """SELECT a.id, a.from_user_id, a.to_user_id, u.nickname AS target_nickname, a.message, a.status, a.responded_at, a.created_at
        FROM match_apply a LEFT JOIN users u ON u.id = CASE WHEN a.from_user_id = :id THEN a.to_user_id ELSE a.from_user_id END
        WHERE a.from_user_id = :id OR a.to_user_id = :id ORDER BY a.created_at DESC, a.id DESC LIMIT :limit OFFSET :offset""",
        "SELECT COUNT(*) FROM match_apply WHERE from_user_id = :id OR to_user_id = :id", member_id, page, page_size)


@router.get("/{member_id}/dating-records")
async def dating_records(member_id: int = Path(..., ge=1), page: int = _paging()[0], page_size: int = _paging()[1], current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> dict:
    await _ensure_member(db, member_id)
    return await _page(db, """SELECT r.id, r.request_id, r.scheduled_at, r.location, r.status, r.cancel_reason, r.created_at,
        q.user_id, q.target_user_id, u.nickname AS target_nickname
        FROM meeting_record r JOIN meeting_request q ON q.id = r.request_id
        LEFT JOIN users u ON u.id = CASE WHEN q.user_id = :id THEN q.target_user_id ELSE q.user_id END
        WHERE q.user_id = :id OR q.target_user_id = :id ORDER BY r.scheduled_at DESC, r.id DESC LIMIT :limit OFFSET :offset""",
        "SELECT COUNT(*) FROM meeting_record r JOIN meeting_request q ON q.id = r.request_id WHERE q.user_id = :id OR q.target_user_id = :id", member_id, page, page_size)


@router.get("/{member_id}/media")
async def media(member_id: int = Path(..., ge=1), page: int = _paging()[0], page_size: int = _paging()[1], current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> dict:
    await _ensure_member(db, member_id)
    return await _page(db, """SELECT id, media_type, file_url, thumbnail_url, mime_type, duration_seconds, sort_order, is_primary, review_status, created_at
        FROM user_media WHERE user_id = :id AND deleted_at IS NULL ORDER BY sort_order ASC, id DESC LIMIT :limit OFFSET :offset""",
        "SELECT COUNT(*) FROM user_media WHERE user_id = :id AND deleted_at IS NULL", member_id, page, page_size)


@router.get("/{member_id}/source-records")
async def source_records(member_id: int = Path(..., ge=1), page: int = _paging()[0], page_size: int = _paging()[1], current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> dict:
    await _ensure_member(db, member_id)
    # There is no dedicated provenance table yet; expose known attribution history.
    try:
        return await _page(db, """SELECT 'assignment' AS source_type, id AS source_id, source, status, effective_at AS occurred_at
            FROM resource_assignment WHERE user_id = :id
            UNION ALL SELECT 'promotion' AS source_type, id AS source_id, 'promotion' AS source, status, effective_at AS occurred_at
            FROM promotion_attribution WHERE user_id = :id
            ORDER BY occurred_at DESC, source_id DESC LIMIT :limit OFFSET :offset""",
            "SELECT (SELECT COUNT(*) FROM resource_assignment WHERE user_id = :id) + (SELECT COUNT(*) FROM promotion_attribution WHERE user_id = :id)", member_id, page, page_size)
    except SQLAlchemyError:
        await db.rollback()
        return {"items": [], "page": page, "page_size": page_size, "total": 0, "has_more": False}

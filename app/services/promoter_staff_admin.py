"""Back-office services for the 推广红娘 management page.

推广红娘与「总店红娘（服务红娘）」是两套独立体系：
- 总店红娘：`user_matchmaker_apply(service_matchmaker)` + `matchmaker_profile` 档案；
- 推广红娘：复用 `user_matchmaker_apply(promoter)` 记录身份（渠道等放 `channel`），
  不做独立档案表，推广数据（会员归属/分享码）来自 `promotion_attribution`/`promotion_touch`。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.promoter_staff_admin import (
    PromoterStaffCreate,
    PromoterStaffDetail,
    PromoterStaffItem,
    PromoterStaffPage,
    PromoterStaffUpdate,
    PromoterUserCandidate,
)

_APPLY_TYPE = "promoter"

_SELECT_BODY = """SELECT u.id AS user_id, u.avatar, u.nickname, u.phone AS user_phone,
    ma.id AS apply_id, ma.real_name, ma.phone AS apply_phone, ma.channel, ma.intro,
    ma.status, ma.reviewed_at, ma.suspended_at, ma.suspension_reason, ma.created_at,
    (SELECT COUNT(*) FROM promotion_attribution pa
        WHERE pa.promoter_id = u.id AND pa.status = 1) AS member_count,
    (SELECT COUNT(*) FROM promotion_touch pt
        WHERE pt.promoter_id = u.id) AS touch_count
    FROM users u
    JOIN user_matchmaker_apply ma
      ON ma.user_id = u.id AND ma.application_type = 'promoter'"""


def _dt(value: Any) -> datetime | None:
    if value is None:
        return None
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def _status_label(status: int) -> str:
    return "在职" if status == 1 else "离职"


def _phone(row: dict[str, Any]) -> str | None:
    return (row.get("apply_phone") or row.get("user_phone")) or None


def _item(row: dict[str, Any]) -> PromoterStaffItem:
    payload = dict(row)
    user_id = int(row["user_id"])
    status = int(row["status"] or 1)
    return PromoterStaffItem(
        id=user_id,
        user_id=user_id,
        avatar=row.get("avatar"),
        display_name=row.get("real_name") or row.get("nickname") or f"推广红娘#{user_id}",
        phone=_phone(payload),
        channel=row.get("channel"),
        member_count=int(row.get("member_count") or 0),
        touch_count=int(row.get("touch_count") or 0),
        status=status,
        status_label=_status_label(status),
        reviewed_at=_dt(row.get("reviewed_at")),
        intro=row.get("intro"),
        created_at=_dt(row.get("created_at")),
    )


def _detail(row: dict[str, Any]) -> PromoterStaffDetail:
    item = _item(row)
    return PromoterStaffDetail(
        **item.model_dump(),
        real_name=row.get("real_name") or row.get("nickname"),
        suspension_reason=row.get("suspension_reason"),
    )


async def list_promoters(
    db: AsyncSession,
    page: int,
    page_size: int,
    keyword: str | None = None,
    status: int | None = None,
) -> PromoterStaffPage:
    conditions = ["ma.status IN (1, 2)"]
    params: dict[str, object] = {"limit": page_size, "offset": (page - 1) * page_size}
    if keyword:
        conditions.append(
            "(u.nickname LIKE CONCAT('%', :keyword, '%') "
            "OR COALESCE(ma.real_name, '') LIKE CONCAT('%', :keyword, '%') "
            "OR u.phone LIKE CONCAT('%', :keyword, '%'))"
        )
        params["keyword"] = keyword
    if status in (1, 2):
        conditions.append("ma.status = :status")
        params["status"] = status
    where = " AND ".join(conditions)
    rows = await db.execute(
        text(f"{_SELECT_BODY} WHERE {where} ORDER BY u.id DESC LIMIT :limit OFFSET :offset"),
        params,
    )
    count = await db.execute(
        text(
            f"SELECT COUNT(*) FROM user_matchmaker_apply ma JOIN users u ON u.id = ma.user_id "
            f"WHERE {where}"
        ),
        {k: v for k, v in params.items() if k not in {"limit", "offset"}},
    )
    total = int(count.scalar() or 0)
    return PromoterStaffPage(
        items=[_item(dict(r)) for r in rows.mappings().all()],
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
    )


async def search_user_candidates(
    db: AsyncSession, keyword: str, limit: int = 10
) -> list[PromoterUserCandidate]:
    rows = await db.execute(
        text(
            """SELECT u.id, u.nickname, u.phone, u.avatar
            FROM users u
            WHERE u.status = 1
              AND (u.nickname LIKE CONCAT('%', :keyword, '%') OR u.phone LIKE CONCAT('%', :keyword, '%'))
              AND NOT EXISTS (
                SELECT 1 FROM user_matchmaker_apply ma
                WHERE ma.user_id = u.id AND ma.application_type = 'promoter'
              )
            ORDER BY u.id DESC LIMIT :limit"""
        ),
        {"keyword": keyword, "limit": limit},
    )
    return [PromoterUserCandidate(**dict(row)) for row in rows.mappings().all()]


async def _resolve_user_id(
    db: AsyncSession, body: PromoterStaffCreate
) -> int:
    user_id = body.user_id
    if user_id is None and body.lookup:
        column = "nickname" if body.lookup_by == "nickname" else "phone"
        user_id = (
            await db.execute(
                text(f"SELECT id FROM users WHERE {column} = :lookup AND status = 1 ORDER BY id DESC LIMIT 1"),
                {"lookup": body.lookup.strip()},
            )
        ).scalar()
        if user_id is None:
            raise HTTPException(404, detail="未找到可绑定的普通用户")
    if user_id is None:
        raise HTTPException(422, detail="请先选择或搜索要绑定的普通用户")
    return int(user_id)


async def get_promoter(db: AsyncSession, user_id: int) -> PromoterStaffDetail:
    row = (
        await db.execute(
            text(f"{_SELECT_BODY} WHERE ma.user_id = :id AND ma.status IN (1, 2)"),
            {"id": user_id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(404, detail="推广红娘不存在")
    return _detail(dict(row))


async def create_promoter(
    db: AsyncSession,
    actor_account_id: int,
    body: PromoterStaffCreate,
) -> PromoterStaffDetail:
    user_id = await _resolve_user_id(db, body)
    user = (
        await db.execute(
            text("SELECT id, nickname, phone FROM users WHERE id = :id AND status = 1 FOR UPDATE"),
            {"id": user_id},
        )
    ).mappings().first()
    if not user:
        raise HTTPException(404, detail="普通用户不存在或已停用")
    existing = await db.execute(
        text("SELECT 1 FROM user_matchmaker_apply WHERE user_id = :id AND application_type = 'promoter'"),
        {"id": user_id},
    )
    if existing.scalar():
        raise HTTPException(409, detail="该普通用户已是推广红娘或推广申请待审核")

    name = str(user["nickname"] or f"推广红娘#{user_id}")
    await db.execute(
        text(
            """INSERT INTO user_matchmaker_apply
            (user_id, application_type, real_name, phone, intro, channel, status, reviewed_by, reviewed_at)
            VALUES (:id, 'promoter', :name, :phone, :intro, :channel, 1, :actor, UTC_TIMESTAMP())"""
        ),
        {
            "id": user_id,
            "name": name,
            "phone": body.phone or user["phone"],
            "intro": body.intro,
            "channel": body.channel,
            "actor": actor_account_id,
        },
    )
    await db.execute(
        text(
            "INSERT IGNORE INTO user_role (user_id, role_code, status) VALUES (:id, 'promoter', 1)"
        ),
        {"id": user_id},
    )
    await db.commit()
    return await get_promoter(db, user_id)


async def update_promoter(
    db: AsyncSession,
    user_id: int,
    body: PromoterStaffUpdate,
) -> PromoterStaffDetail:
    await get_promoter(db, user_id)
    # None 表示“未修改”，保留原值；status=2 记离职时间，status=1 复职并清空离职时间
    await db.execute(
        text(
            """UPDATE user_matchmaker_apply
            SET channel = COALESCE(:channel, channel),
                intro = COALESCE(:intro, intro),
                suspension_reason = COALESCE(:reason, suspension_reason),
                status = CASE WHEN :status IS NOT NULL THEN :status ELSE status END,
                suspended_at = CASE
                    WHEN :status = 2 THEN UTC_TIMESTAMP()
                    WHEN :status = 1 THEN NULL
                    ELSE suspended_at END,
                updated_at = UTC_TIMESTAMP()
            WHERE user_id = :uid AND application_type = 'promoter'"""
        ),
        {
            "channel": body.channel,
            "intro": body.intro,
            "reason": body.reason,
            "status": body.status,
            "uid": user_id,
        },
    )
    if body.display_name is not None or body.phone is not None:
        user_assignments: list[str] = []
        user_params: dict[str, object] = {"uid": user_id}
        if body.display_name is not None:
            user_assignments.append("nickname = :name")
            user_params["name"] = body.display_name
        if body.phone is not None:
            user_assignments.append("phone = :phone")
            user_params["phone"] = body.phone
        await db.execute(
            text(f"UPDATE users SET {', '.join(user_assignments)} WHERE id = :uid"),
            user_params,
        )
        if body.display_name is not None:
            await db.execute(
                text(
                    "UPDATE user_matchmaker_apply SET real_name = :name "
                    "WHERE user_id = :uid AND application_type = 'promoter'"
                ),
                {"name": body.display_name, "uid": user_id},
            )
    await db.commit()
    return await get_promoter(db, user_id)

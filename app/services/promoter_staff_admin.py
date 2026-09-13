"""Back-office services for the 推广红娘 management page.

推广红娘与「总店红娘（服务红娘）」是两套独立体系：
- 总店红娘：`user_matchmaker_apply(service_matchmaker)` + `matchmaker_profile` 档案；
- 推广红娘：复用 `user_matchmaker_apply(promoter)` 记录身份（渠道等放 `channel`），
  不做独立档案表，推广数据（会员归属/分享码）来自 `promotion_attribution`/`promotion_touch`。
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.matchmaker_admin import MatchmakerAdminAccount
from app.schemas.promoter_staff_admin import (
    PromoterCommissionEntryItem,
    PromoterCommissionEntryOptions,
    PromoterCommissionEntryPage,
    PromoterCommissionEventOption,
    PromoterCommissionPromoterOption,
    PromoterDeleteResponse,
    PromoterPlatformTokenResponse,
    PromoterPosterResponse,
    PromoterStaffCreate,
    PromoterStaffDetail,
    PromoterStaffItem,
    PromoterStaffPage,
    PromoterStaffUpdate,
    PromoterStatistics,
    PromoterTeamItem,
    PromoterTeamUpdate,
    PromoterUserCandidate,
)
from app.services.matchmaker_admin_auth import _issue_session
from app.services import user_candidates

_APPLY_TYPE = "promoter"

# 列表/详情公共 FROM 段：绑定推广红娘身份，并挂上分成级别、当前团队
_FROM_BODY = """FROM users u
    JOIN user_matchmaker_apply ma
      ON ma.user_id = u.id AND ma.application_type = 'promoter'
    LEFT JOIN promoter_level_config lc ON lc.level_id = ma.commission_level_id
    LEFT JOIN (
        SELECT pm.promoter_id, pm.team_id, t.name AS team_name
        FROM partner_membership pm
        JOIN partner_team t ON t.id = pm.team_id
        WHERE pm.status = 1
    ) tm ON tm.promoter_id = u.id"""

_SELECT_BODY = """SELECT u.id AS user_id, u.avatar, u.nickname, u.phone AS user_phone,
    ma.id AS apply_id, ma.real_name, ma.phone AS apply_phone, ma.channel, ma.intro,
    ma.matchmaker_type, ma.slogan, ma.commission_level_id, ma.visible,
    ma.can_view_lead_follow, ma.can_write_lead_follow, ma.can_view_member_crm_follow,
    ma.status, ma.reviewed_at, ma.suspended_at, ma.suspension_reason, ma.created_at,
    lc.level_name AS commission_level_name, tm.team_id, tm.team_name,
    (SELECT COUNT(*) FROM promotion_attribution pa
        WHERE pa.promoter_id = u.id AND pa.status = 1) AS member_count,
    (SELECT COUNT(*) FROM promotion_attribution pa2
        WHERE pa2.promoter_id = u.id AND pa2.status = 1
          AND pa2.effective_at >= DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-01')) AS member_month,
    (SELECT COUNT(*) FROM customer_lead cl
        WHERE cl.created_by = u.id) AS lead_total,
    (SELECT COUNT(*) FROM customer_lead cl2
        WHERE cl2.created_by = u.id
          AND cl2.created_at >= DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-01')) AS lead_month,
    (SELECT COUNT(*) FROM promotion_touch pt
        WHERE pt.promoter_id = u.id) AS touch_count,
    COALESCE((SELECT SUM(ce.amount) FROM commission_entry ce
        WHERE ce.beneficiary_type = 'promoter' AND ce.beneficiary_id = u.id
          AND ce.status <> 'REVERSED'), 0) AS order_amount
    """ + _FROM_BODY


def _dt(value: Any) -> datetime | None:
    if value is None:
        return None
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def _status_label(status: int) -> str:
    return "在职" if status == 1 else "离职"


_TYPE_LABELS = {"part_time": "兼职", "full_time": "全职"}


def _type_label(value: Any) -> str:
    if value is None:
        return "—"
    return _TYPE_LABELS.get(str(value), "—")


def _money(value: Any) -> str:
    """金额统一以字符串序列化，避免 JS Number 精度丢失。"""
    if value is None:
        return "0.00"
    return f"{Decimal(str(value)):.2f}"


def _phone(row: dict[str, Any]) -> str | None:
    return (row.get("apply_phone") or row.get("user_phone")) or None


def _bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    return bool(int(value))


def _item(row: dict[str, Any]) -> PromoterStaffItem:
    payload = dict(row)
    user_id = int(row["user_id"])
    status = int(row["status"] or 1)
    return PromoterStaffItem(
        id=user_id,
        user_id=user_id,
        avatar=row.get("avatar"),
        display_name=row.get("real_name") or row.get("nickname") or f"推广红娘#{user_id}",
        account=row.get("nickname"),
        phone=_phone(payload),
        channel=row.get("channel"),
        matchmaker_type=row.get("matchmaker_type"),
        matchmaker_type_label=_type_label(row.get("matchmaker_type")),
        commission_level_id=int(row["commission_level_id"]) if row.get("commission_level_id") is not None else None,
        commission_level_name=row.get("commission_level_name"),
        team_id=int(row["team_id"]) if row.get("team_id") is not None else None,
        team_name=row.get("team_name"),
        member_count=int(row.get("member_count") or 0),
        member_month=int(row.get("member_month") or 0),
        lead_total=int(row.get("lead_total") or 0),
        lead_month=int(row.get("lead_month") or 0),
        order_amount=_money(row.get("order_amount")),
        touch_count=int(row.get("touch_count") or 0),
        status=status,
        status_label=_status_label(status),
        visible=_bool(row.get("visible"), True),
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
        slogan=row.get("slogan"),
        can_view_lead_follow=_bool(row.get("can_view_lead_follow")),
        can_write_lead_follow=_bool(row.get("can_write_lead_follow")),
        can_view_member_crm_follow=_bool(row.get("can_view_member_crm_follow")),
    )


_ORDER_BY = {
    "joined_desc": "ORDER BY COALESCE(ma.reviewed_at, ma.created_at) DESC, u.id DESC",
    "joined_asc": "ORDER BY COALESCE(ma.reviewed_at, ma.created_at) ASC, u.id ASC",
    "member_desc": "ORDER BY member_count DESC, u.id DESC",
    "member_asc": "ORDER BY member_count ASC, u.id DESC",
}


async def list_promoters(
    db: AsyncSession,
    page: int,
    page_size: int,
    keyword: str | None = None,
    status: int | None = None,
    commission_level_id: int | None = None,
    team_id: int | None = None,
    visible: bool | None = None,
    sort: str = "joined_desc",
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
    if commission_level_id is not None:
        conditions.append("ma.commission_level_id = :commission_level_id")
        params["commission_level_id"] = commission_level_id
    if team_id is not None:
        conditions.append("tm.team_id = :team_id")
        params["team_id"] = team_id
    if visible is not None:
        conditions.append("ma.visible = :visible")
        params["visible"] = int(visible)
    where = " AND ".join(conditions)
    order_by = _ORDER_BY.get(sort, _ORDER_BY["joined_desc"])
    rows = await db.execute(
        text(f"{_SELECT_BODY} WHERE {where} {order_by} LIMIT :limit OFFSET :offset"),
        params,
    )
    count = await db.execute(
        text(f"SELECT COUNT(*) {_FROM_BODY} WHERE {where}"),
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
    """推广红娘候选人：排除已是推广红娘的用户（统一走 user_candidates 实现）。"""
    rows = await user_candidates.search_user_candidates(db, keyword, "promoter", limit)
    return [PromoterUserCandidate(**row) for row in rows]


async def _resolve_user_id(
    db: AsyncSession, body: PromoterStaffCreate
) -> int:
    user_id = body.user_id
    if user_id is None and body.lookup:
        user_id = await user_candidates.resolve_user_id(
            db,
            body.lookup,
            body.lookup_by or "nickname",
            not_found_detail="未找到匹配的普通用户，请从下拉列表中选择",
        )
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
            (user_id, application_type, real_name, phone, intro, channel,
             matchmaker_type, slogan, commission_level_id,
             can_view_lead_follow, can_write_lead_follow, can_view_member_crm_follow,
             status, reviewed_by, reviewed_at)
            VALUES (:id, 'promoter', :name, :phone, :intro, :channel,
             :matchmaker_type, :slogan, :commission_level_id,
             :can_view_lead_follow, :can_write_lead_follow, :can_view_member_crm_follow,
             1, :actor, UTC_TIMESTAMP())"""
        ),
        {
            "id": user_id,
            "name": name,
            "phone": body.phone or user["phone"],
            "intro": body.intro,
            "channel": body.channel,
            "matchmaker_type": body.matchmaker_type,
            "slogan": body.slogan,
            "commission_level_id": body.commission_level_id,
            "can_view_lead_follow": int(body.can_view_lead_follow),
            "can_write_lead_follow": int(body.can_write_lead_follow),
            "can_view_member_crm_follow": int(body.can_view_member_crm_follow),
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
                matchmaker_type = COALESCE(:matchmaker_type, matchmaker_type),
                slogan = COALESCE(:slogan, slogan),
                commission_level_id = COALESCE(:commission_level_id, commission_level_id),
                visible = COALESCE(:visible, visible),
                can_view_lead_follow = COALESCE(:can_view_lead_follow, can_view_lead_follow),
                can_write_lead_follow = COALESCE(:can_write_lead_follow, can_write_lead_follow),
                can_view_member_crm_follow = COALESCE(:can_view_member_crm_follow, can_view_member_crm_follow),
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
            "matchmaker_type": body.matchmaker_type,
            "slogan": body.slogan,
            "commission_level_id": body.commission_level_id,
            "visible": int(body.visible) if body.visible is not None else None,
            "can_view_lead_follow": body.can_view_lead_follow,
            "can_write_lead_follow": body.can_write_lead_follow,
            "can_view_member_crm_follow": body.can_view_member_crm_follow,
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


# ─── 统计卡 ─────────────────────────────────────────────────────


async def promoter_statistics(db: AsyncSession) -> PromoterStatistics:
    """推广红娘统计卡口径：只统计在职（status=1）推广红娘名下的数据。"""
    type_row = (
        (
            await db.execute(
                text(
                    """SELECT
                    COALESCE(SUM(CASE WHEN ma.matchmaker_type = 'part_time' THEN 1 ELSE 0 END), 0) AS part_time_count,
                    COALESCE(SUM(CASE WHEN ma.matchmaker_type = 'full_time' THEN 1 ELSE 0 END), 0) AS full_time_count
                FROM user_matchmaker_apply ma
                WHERE ma.application_type = 'promoter' AND ma.status = 1"""
                )
            )
        )
        .mappings()
        .first()
        or {}
    )
    member_row = (
        (
            await db.execute(
                text(
                    """SELECT
                    COUNT(*) AS member_total,
                    COALESCE(SUM(CASE WHEN pa.effective_at >= DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-01')
                        THEN 1 ELSE 0 END), 0) AS member_month,
                    COALESCE(SUM(CASE
                        WHEN pa.effective_at >= DATE_FORMAT(DATE_SUB(UTC_TIMESTAMP(), INTERVAL 1 MONTH), '%Y-%m-01')
                         AND pa.effective_at < DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-01')
                        THEN 1 ELSE 0 END), 0) AS member_last_month
                FROM promotion_attribution pa
                JOIN user_matchmaker_apply ma
                  ON ma.user_id = pa.promoter_id
                 AND ma.application_type = 'promoter' AND ma.status = 1
                WHERE pa.status = 1"""
                )
            )
        )
        .mappings()
        .first()
        or {}
    )
    lead_row = (
        (
            await db.execute(
                text(
                    """SELECT
                    COUNT(*) AS lead_total,
                    COALESCE(SUM(CASE WHEN cl.created_at >= DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-01')
                        THEN 1 ELSE 0 END), 0) AS lead_month,
                    COALESCE(SUM(CASE
                        WHEN cl.created_at >= DATE_FORMAT(DATE_SUB(UTC_TIMESTAMP(), INTERVAL 1 MONTH), '%Y-%m-01')
                         AND cl.created_at < DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-01')
                        THEN 1 ELSE 0 END), 0) AS lead_last_month
                FROM customer_lead cl
                JOIN user_matchmaker_apply ma
                  ON ma.user_id = cl.created_by
                 AND ma.application_type = 'promoter' AND ma.status = 1"""
                )
            )
        )
        .mappings()
        .first()
        or {}
    )
    return PromoterStatistics(
        part_time_count=int(type_row.get("part_time_count") or 0),
        full_time_count=int(type_row.get("full_time_count") or 0),
        member_total=int(member_row.get("member_total") or 0),
        member_month=int(member_row.get("member_month") or 0),
        member_last_month=int(member_row.get("member_last_month") or 0),
        lead_total=int(lead_row.get("lead_total") or 0),
        lead_month=int(lead_row.get("lead_month") or 0),
        lead_last_month=int(lead_row.get("lead_last_month") or 0),
    )


# ─── 合伙团队 ───────────────────────────────────────────────────


async def list_promoter_teams(db: AsyncSession) -> list[PromoterTeamItem]:
    """合伙团队下拉：只取 status=1 的团队。"""
    rows = await db.execute(
        text(
            """SELECT t.id, t.name, t.owner_user_id, u.nickname AS owner_name, t.status
            FROM partner_team t
            LEFT JOIN users u ON u.id = t.owner_user_id
            WHERE t.status = 1
            ORDER BY t.id DESC"""
        )
    )
    return [PromoterTeamItem(**dict(row)) for row in rows.mappings().all()]


async def update_promoter_team(
    db: AsyncSession,
    user_id: int,
    body: PromoterTeamUpdate,
    actor_account_id: int,
) -> PromoterStaffDetail:
    """变更推广红娘的隶属团队；team_id 传 null 表示移出团队。"""
    await get_promoter(db, user_id)  # 不存在会 404
    team_name: str | None = None
    if body.team_id is not None:
        team = (
            (
                await db.execute(
                    text("SELECT id, name FROM partner_team WHERE id = :id AND status = 1"),
                    {"id": body.team_id},
                )
            )
            .mappings()
            .first()
        )
        if not team:
            raise HTTPException(400, detail="合伙团队不存在或已关闭")
        team_name = str(team["name"])

    # 1) 结束当前生效的团队归属
    await db.execute(
        text(
            """UPDATE partner_membership
            SET status = 2, left_at = UTC_TIMESTAMP(),
                changed_by = :actor, change_reason = :reason
            WHERE promoter_id = :uid AND status = 1"""
        ),
        {"actor": actor_account_id, "reason": body.reason, "uid": user_id},
    )
    # 2) 落新归属；unique(promoter_id, status) 兜底幂等，避免历史行冲突导致 500
    if body.team_id is not None:
        await db.execute(
            text(
                """INSERT INTO partner_membership
                (team_id, promoter_id, status, joined_at, changed_by, change_reason)
                VALUES (:team_id, :uid, 1, UTC_TIMESTAMP(), :actor, :reason)
                ON DUPLICATE KEY UPDATE
                    team_id = VALUES(team_id),
                    joined_at = VALUES(joined_at),
                    left_at = NULL,
                    changed_by = VALUES(changed_by),
                    change_reason = VALUES(change_reason)"""
            ),
            {
                "team_id": body.team_id,
                "uid": user_id,
                "actor": actor_account_id,
                "reason": body.reason,
            },
        )
    await db.execute(
        text(
            """INSERT INTO business_audit_log
            (actor_user_id, action, resource_type, resource_id, after_json)
            VALUES (:actor, 'promoter_staff.team.update', 'user_matchmaker_apply', :uid, :after_json)"""
        ),
        {
            "actor": actor_account_id,
            "uid": user_id,
            "after_json": json.dumps(
                {"team_id": body.team_id, "team_name": team_name, "reason": body.reason},
                ensure_ascii=False,
            ),
        },
    )
    await db.commit()
    return await get_promoter(db, user_id)


# ─── 分成明细 ───────────────────────────────────────────────────


def _commission_entry_item(row: dict[str, Any]) -> PromoterCommissionEntryItem:
    return PromoterCommissionEntryItem(
        id=int(row["id"]),
        created_at=_dt(row.get("created_at")),
        promoter_id=int(row["beneficiary_id"]),
        promoter_name=row.get("promoter_name") or f"推广红娘#{row['beneficiary_id']}",
        promoter_avatar=row.get("promoter_avatar"),
        consumer_id=int(row["consumer_id"]) if row.get("consumer_id") else None,
        consumer_name=row.get("consumer_name"),
        consumer_phone=row.get("consumer_phone"),
        event_name=row.get("event_name") or row.get("product_name"),
        order_id=int(row["order_id"]) if row.get("order_id") else None,
        order_no=row.get("order_no"),
        base_amount=_money(row.get("base_amount")),
        amount=_money(row.get("amount")),
        status=str(row.get("status") or "PENDING"),
    )


async def list_promoter_commission_entries(
    db: AsyncSession,
    page: int,
    page_size: int,
    promoter_id: int | None = None,
    rule_id: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> PromoterCommissionEntryPage:
    """推广红娘分成流水（beneficiary_type='promoter'）。"""
    where = ["ce.beneficiary_type = 'promoter'"]
    params: dict[str, object] = {"limit": page_size, "offset": (page - 1) * page_size}
    if promoter_id is not None:
        where.append("ce.beneficiary_id = :promoter_id")
        params["promoter_id"] = promoter_id
    if rule_id is not None:
        where.append("ce.rule_id = :rule_id")
        params["rule_id"] = rule_id
    if start_date:
        where.append("ce.created_at >= :start_date")
        params["start_date"] = f"{start_date} 00:00:00"
    if end_date:
        where.append("ce.created_at < DATE_ADD(:end_date, INTERVAL 1 DAY)")
        params["end_date"] = f"{end_date} 00:00:00"
    clause = " AND ".join(where)

    list_sql = f"""SELECT ce.id, ce.created_at, ce.beneficiary_id,
        ce.order_id, ce.base_amount, ce.amount, ce.status, ce.rule_id,
        p.nickname AS promoter_name, p.avatar AS promoter_avatar,
        po.order_no, po.user_id AS consumer_id, po.product_name,
        c.nickname AS consumer_name, c.phone AS consumer_phone,
        COALESCE(rule.name, po.product_name) AS event_name
        FROM commission_entry ce
        LEFT JOIN users p ON p.id = ce.beneficiary_id
        LEFT JOIN payment_order po ON po.id = ce.order_id
        LEFT JOIN users c ON c.id = po.user_id
        LEFT JOIN commission_rule rule ON rule.id = ce.rule_id
        WHERE {clause}
        ORDER BY ce.created_at DESC, ce.id DESC
        LIMIT :limit OFFSET :offset"""
    rows = (await db.execute(text(list_sql), params)).mappings().all()

    count_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}
    total = int(
        (
            await db.execute(
                text(f"SELECT COUNT(*) FROM commission_entry ce WHERE {clause}"),
                count_params,
            )
        ).scalar()
        or 0
    )
    return PromoterCommissionEntryPage(
        items=[_commission_entry_item(dict(row)) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
    )


async def promoter_commission_entry_options(
    db: AsyncSession,
) -> PromoterCommissionEntryOptions:
    """分成明细筛选下拉：在职推广红娘 + 启用中的推广分成规则。"""
    promoter_rows = await db.execute(
        text(
            """SELECT ma.user_id AS id,
                COALESCE(ma.real_name, u.nickname) AS name, u.avatar
            FROM user_matchmaker_apply ma
            JOIN users u ON u.id = ma.user_id
            WHERE ma.application_type = 'promoter' AND ma.status = 1
            ORDER BY ma.user_id DESC"""
        )
    )
    rule_rows = await db.execute(
        text(
            """SELECT id, name FROM commission_rule
            WHERE beneficiary_type = 'promoter' AND status = 1
            ORDER BY priority DESC, id DESC"""
        )
    )
    return PromoterCommissionEntryOptions(
        promoters=[
            PromoterCommissionPromoterOption(**dict(row))
            for row in promoter_rows.mappings().all()
        ],
        events=[
            PromoterCommissionEventOption(**dict(row))
            for row in rule_rows.mappings().all()
        ],
    )


# ─── 海报 / 免登 / 删除 ─────────────────────────────────────────


async def promoter_poster(db: AsyncSession, user_id: int) -> PromoterPosterResponse:
    await get_promoter(db, user_id)
    return PromoterPosterResponse(
        promoter_id=user_id,
        url=f"/storage/posters/promoter-{user_id}.png",
        qr_content=f"promoter:{user_id}",
    )


async def promoter_platform_token(
    db: AsyncSession, user_id: int
) -> PromoterPlatformTokenResponse:
    """为推广红娘签发免登 token（复用独立红娘后台账号体系）。

    推广红娘工作台地址尚未定稿，jump_url 暂复用总店红娘工作台路径。
    """
    await get_promoter(db, user_id)
    row = (
        (
            await db.execute(
                text(
                    "SELECT id,username,display_name,matchmaker_user_id,data_scope,organization_id,status,last_login_at "
                    "FROM matchmaker_admin_account WHERE matchmaker_user_id=:id AND status=1"
                ),
                {"id": user_id},
            )
        )
        .mappings()
        .first()
    )
    if not row:
        raise HTTPException(404, detail="推广红娘后台账号不存在")
    account = MatchmakerAdminAccount(**dict(row))
    issued = await _issue_session(db, account, None, "admin-impersonation")
    return PromoterPlatformTokenResponse(
        promoter_id=user_id,
        access_token=issued.access_token,
        refresh_token=issued.refresh_token,
        expires_in=issued.expires_in,
        # 推广红娘工作台地址待定，暂复用总店红娘工作台路径
        jump_url=f"/matchmaker/workbench?token={issued.access_token}",
    )


async def delete_promoter(db: AsyncSession, user_id: int) -> PromoterDeleteResponse:
    await get_promoter(db, user_id)
    related = await db.execute(
        text(
            """SELECT
            (SELECT COUNT(*) FROM promotion_attribution WHERE promoter_id = :id AND status = 1) +
            (SELECT COUNT(*) FROM customer_lead WHERE created_by = :id)"""
        ),
        {"id": user_id},
    )
    if int(related.scalar() or 0):
        raise HTTPException(409, detail="该推广红娘名下存在会员归属或客源线索，无法删除")
    await db.execute(
        text(
            "DELETE FROM user_matchmaker_apply WHERE user_id = :id AND application_type = 'promoter'"
        ),
        {"id": user_id},
    )
    await db.execute(
        text("DELETE FROM user_role WHERE user_id = :id AND role_code = 'promoter'"),
        {"id": user_id},
    )
    await db.commit()
    return PromoterDeleteResponse(id=user_id, deleted=True)

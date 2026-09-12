"""Back-office services for the 合伙红娘 pages.

身份模型（与客户端 `matchmaker_workspace.get_partner_center` 同源）：
- 合伙人 = `partner_team` 一行（owner_user_id 唯一），团队名称即合伙人团队名；
- 团队成员 = `partner_membership`（status=1）中的推广红娘；
- 团队业绩 / 团队有效会员 = 团队成员名下 `promotion_attribution` 关联的会员消费与审核通过数；
- 分成 = `commission_entry(beneficiary_type='partner', beneficiary_id=团队 owner_user_id)`。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.partner_admin import (
    PartnerCommissionEntryCreate,
    PartnerCommissionEntryCreateResult,
    PartnerCommissionEntryItem,
    PartnerCommissionEntryOptions,
    PartnerCommissionEntryPage,
    PartnerCommissionEventOption,
    PartnerCommissionPartnerOption,
    PartnerRelationBind,
    PartnerRelationItem,
    PartnerRelationPage,
    PartnerRelationRemove,
    PartnerRelationResult,
    PartnerStaffCreate,
    PartnerStaffDetail,
    PartnerStaffItem,
    PartnerStaffPage,
    PartnerStaffUpdate,
    PartnerStatistics,
    PartnerTeamOption,
    PartnerUserCandidate,
)

_TEAM_STATUS_LABEL = {1: "正常", 2: "关闭", 3: "冻结"}
_RELATION_STATUS_LABEL = {1: "正常", 2: "移出", 3: "变更"}

# 团队成员/业绩/有效会员/累积分成 四个聚合子查询，合伙人列表与详情共用。
_STATS_COLUMNS = """(SELECT COUNT(*) FROM partner_membership pm
        WHERE pm.team_id = t.id AND pm.status = 1) AS member_count,
    (SELECT COUNT(DISTINCT pa.user_id) FROM partner_membership pm2
        JOIN promotion_attribution pa ON pa.promoter_id = pm2.promoter_id AND pa.status = 1
        JOIN matchmaker_member_review mr ON mr.user_id = pa.user_id AND mr.status = 'PASSED'
        WHERE pm2.team_id = t.id AND pm2.status = 1) AS effective_member_count,
    COALESCE((SELECT SUM(po.amount) FROM partner_membership pm3
        JOIN promotion_attribution pa2 ON pa2.promoter_id = pm3.promoter_id AND pa2.status = 1
        JOIN payment_order po ON po.user_id = pa2.user_id AND po.status = 1
        WHERE pm3.team_id = t.id AND pm3.status = 1), 0) AS performance_amount,
    COALESCE((SELECT SUM(ce.amount) FROM commission_entry ce
        WHERE ce.beneficiary_type = 'partner' AND ce.beneficiary_id = t.owner_user_id
          AND ce.status <> 'REVERSED'), 0) AS commission_amount"""

_FROM_BODY = """FROM partner_team t
    JOIN users u ON u.id = t.owner_user_id
    LEFT JOIN partner_level_config lc ON lc.level_id = t.level_id"""

_SELECT_BODY = f"""SELECT t.id AS team_id, t.owner_user_id, t.name AS team_name, t.level_id,
    t.status, t.open_mode, t.created_at, t.updated_at,
    u.nickname AS account, u.avatar, u.phone AS user_phone,
    lc.level_name,
    {_STATS_COLUMNS}
    {_FROM_BODY}"""

_ORDER_BY = {
    "created_desc": "ORDER BY t.created_at DESC, t.id DESC",
    "created_asc": "ORDER BY t.created_at ASC, t.id ASC",
    "performance_desc": "ORDER BY performance_amount DESC, t.id DESC",
    "member_desc": "ORDER BY member_count DESC, t.id DESC",
}


def _dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _money(value: Any) -> str:
    """金额统一以字符串序列化，避免 JS Number 精度丢失。"""
    if value is None:
        return "0.00"
    return f"{Decimal(str(value)):.2f}"


def _int_or_none(value: Any) -> int | None:
    return int(value) if value is not None else None


def _item(row: dict[str, Any]) -> PartnerStaffItem:
    status = int(row["status"] or 1)
    level_id = int(row.get("level_id") or 1)
    if level_id not in (1, 2, 3):
        level_id = 1
    return PartnerStaffItem(
        id=int(row["team_id"]),
        team_id=int(row["team_id"]),
        user_id=int(row["owner_user_id"]),
        account=row.get("account"),
        display_name=row.get("account") or f"合伙人#{row['owner_user_id']}",
        avatar=row.get("avatar"),
        phone=row.get("user_phone"),
        team_name=str(row["team_name"]),
        level_id=level_id,
        level_name=row.get("level_name"),
        member_count=int(row.get("member_count") or 0),
        performance_amount=_money(row.get("performance_amount")),
        effective_member_count=int(row.get("effective_member_count") or 0),
        commission_amount=_money(row.get("commission_amount")),
        status=status,
        status_label=_TEAM_STATUS_LABEL.get(status, "正常"),
        open_mode=str(row.get("open_mode") or "manual"),
        created_at=_dt(row.get("created_at")),
    )


async def list_partners(
    db: AsyncSession,
    page: int,
    page_size: int,
    keyword: str | None = None,
    level_id: int | None = None,
    status: int | None = None,
    sort: str = "created_desc",
) -> PartnerStaffPage:
    conditions = ["1 = 1"]
    params: dict[str, object] = {"limit": page_size, "offset": (page - 1) * page_size}
    if keyword:
        conditions.append(
            "(u.nickname LIKE CONCAT('%', :keyword, '%') "
            "OR t.name LIKE CONCAT('%', :keyword, '%') "
            "OR u.phone LIKE CONCAT('%', :keyword, '%'))"
        )
        params["keyword"] = keyword
    if level_id in (1, 2, 3):
        conditions.append("t.level_id = :level_id")
        params["level_id"] = level_id
    if status in (1, 2, 3):
        conditions.append("t.status = :status")
        params["status"] = status
    where = " AND ".join(conditions)
    order_by = _ORDER_BY.get(sort, _ORDER_BY["created_desc"])
    rows = await db.execute(
        text(f"{_SELECT_BODY} WHERE {where} {order_by} LIMIT :limit OFFSET :offset"), params
    )
    total = int(
        (
            await db.execute(
                text(f"SELECT COUNT(*) {_FROM_BODY} WHERE {where}"),
                {k: v for k, v in params.items() if k not in {"limit", "offset"}},
            )
        ).scalar()
        or 0
    )
    return PartnerStaffPage(
        items=[_item(dict(row)) for row in rows.mappings().all()],
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
    )


async def partner_statistics(db: AsyncSession) -> PartnerStatistics:
    row = (
        await db.execute(
            text(
                f"""SELECT COUNT(*) AS total_partners,
                SUM(t.status = 1) AS active_partners,
                COALESCE(SUM((SELECT COUNT(*) FROM partner_membership pm
                    WHERE pm.team_id = t.id AND pm.status = 1)), 0) AS total_members,
                COALESCE(SUM((SELECT COUNT(DISTINCT pa.user_id) FROM partner_membership pm2
                    JOIN promotion_attribution pa ON pa.promoter_id = pm2.promoter_id AND pa.status = 1
                    JOIN matchmaker_member_review mr ON mr.user_id = pa.user_id AND mr.status = 'PASSED'
                    WHERE pm2.team_id = t.id AND pm2.status = 1)), 0) AS total_effective_members,
                COALESCE(SUM((SELECT SUM(po.amount) FROM partner_membership pm3
                    JOIN promotion_attribution pa2 ON pa2.promoter_id = pm3.promoter_id AND pa2.status = 1
                    JOIN payment_order po ON po.user_id = pa2.user_id AND po.status = 1
                    WHERE pm3.team_id = t.id AND pm3.status = 1)), 0) AS total_performance,
                COALESCE(SUM((SELECT SUM(ce.amount) FROM commission_entry ce
                    WHERE ce.beneficiary_type = 'partner' AND ce.beneficiary_id = t.owner_user_id
                      AND ce.status <> 'REVERSED')), 0) AS total_commission
                {_FROM_BODY}"""
            )
        )
    ).mappings().first()
    return PartnerStatistics(
        total_partners=int(row["total_partners"] or 0) if row else 0,
        active_partners=int(row["active_partners"] or 0) if row else 0,
        total_members=int(row["total_members"] or 0) if row else 0,
        total_effective_members=int(row["total_effective_members"] or 0) if row else 0,
        total_performance=_money(row["total_performance"] if row else 0),
        total_commission=_money(row["total_commission"] if row else 0),
    )


async def search_user_candidates(
    db: AsyncSession, keyword: str, limit: int = 10
) -> list[PartnerUserCandidate]:
    """可绑定为合伙人的候选用户：仅排除服务红娘（服务红娘不可成为合伙人）。"""
    rows = await db.execute(
        text(
            """SELECT u.id, u.nickname, u.phone, u.avatar,
                EXISTS (SELECT 1 FROM user_matchmaker_apply ma
                    WHERE ma.user_id = u.id AND ma.application_type = 'promoter') AS is_promoter,
                EXISTS (SELECT 1 FROM partner_team t WHERE t.owner_user_id = u.id) AS has_team
            FROM users u
            WHERE u.status = 1
              AND NOT EXISTS (SELECT 1 FROM user_matchmaker_apply sm
                    WHERE sm.user_id = u.id AND sm.application_type = 'service_matchmaker')
              AND (u.nickname LIKE CONCAT('%', :keyword, '%') OR u.phone LIKE CONCAT('%', :keyword, '%'))
            ORDER BY u.id DESC LIMIT :limit"""
        ),
        {"keyword": keyword, "limit": limit},
    )
    return [
        PartnerUserCandidate(
            id=int(row["id"]),
            nickname=row["nickname"],
            real_name=row["nickname"],
            phone=row["phone"],
            avatar=row["avatar"],
            is_promoter=bool(row["is_promoter"]),
            has_team=bool(row["has_team"]),
        )
        for row in rows.mappings().all()
    ]


async def _resolve_user_id(db: AsyncSession, body: PartnerStaffCreate) -> int:
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
            raise HTTPException(404, detail="未找到可绑定的用户账号")
    if user_id is None:
        raise HTTPException(422, detail="请先选择或搜索要绑定的用户账号")
    return int(user_id)


async def get_partner(db: AsyncSession, team_id: int) -> PartnerStaffDetail:
    row = (
        await db.execute(
            text(f"{_SELECT_BODY} WHERE t.id = :id"), {"id": team_id}
        )
    ).mappings().first()
    if not row:
        raise HTTPException(404, detail="合伙人不存在")
    data = dict(row)
    item = _item(data)
    invite = (
        await db.execute(
            text("""SELECT code FROM promotion_touch
                WHERE partner_team_id = :team_id ORDER BY id DESC LIMIT 1"""),
            {"team_id": team_id},
        )
    ).scalar()
    return PartnerStaffDetail(
        **item.model_dump(),
        owner_nickname=data.get("account"),
        owner_phone=data.get("user_phone"),
        invite_code=str(invite) if invite else None,
    )


async def create_partner(
    db: AsyncSession, actor_account_id: int, body: PartnerStaffCreate
) -> PartnerStaffDetail:
    user_id = await _resolve_user_id(db, body)
    user = (
        await db.execute(
            text("SELECT id, nickname FROM users WHERE id = :id AND status = 1"), {"id": user_id}
        )
    ).mappings().first()
    if not user:
        raise HTTPException(404, detail="用户不存在或已停用")
    # 服务红娘不可成为合伙人
    is_service = await db.scalar(
        text("""SELECT 1 FROM user_matchmaker_apply
            WHERE user_id = :id AND application_type = 'service_matchmaker' LIMIT 1"""),
        {"id": user_id},
    )
    if is_service:
        raise HTTPException(409, detail="服务红娘不能成为合伙人")
    if await db.scalar(text("SELECT 1 FROM partner_team WHERE owner_user_id = :id"), {"id": user_id}):
        raise HTTPException(409, detail="该用户已是合伙人")
    team_name = body.team_name.strip() or f"{user['nickname'] or '合伙人'}的团队"
    try:
        result = await db.execute(
            text("""INSERT INTO partner_team (owner_user_id, name, level_id, status, open_mode)
                VALUES (:uid, :name, :level_id, 1, :open_mode)"""),
            {
                "uid": user_id,
                "name": team_name,
                "level_id": body.level_id,
                "open_mode": body.open_mode,
            },
        )
        team_id = int(result.lastrowid)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, detail="该用户已是合伙人")
    # 授予 partner 角色
    await db.execute(
        text("INSERT IGNORE INTO user_role (user_id, role_code, status) VALUES (:id, 'partner', 1)"),
        {"id": user_id},
    )
    # 若其本身已是推广红娘且尚无团队，自动加入自己的团队（与客户端口径一致）
    is_promoter = await db.scalar(
        text("""SELECT 1 FROM user_matchmaker_apply
            WHERE user_id = :id AND application_type = 'promoter' AND status = 1 LIMIT 1"""),
        {"id": user_id},
    )
    if is_promoter and not await db.scalar(
        text("SELECT 1 FROM partner_membership WHERE promoter_id = :id AND status = 1"), {"id": user_id}
    ):
        await db.execute(
            text("""INSERT INTO partner_membership (team_id, promoter_id, status, joined_at, changed_by)
                VALUES (:team_id, :uid, 1, UTC_TIMESTAMP(), :actor)
                ON DUPLICATE KEY UPDATE team_id = VALUES(team_id), left_at = NULL"""),
            {"team_id": team_id, "uid": user_id, "actor": actor_account_id},
        )
    await db.execute(
        text("""INSERT INTO business_audit_log
            (actor_user_id, action, resource_type, resource_id, after_json)
            VALUES (:actor, 'partner.create', 'partner_team', :team_id, :after_json)"""),
        {
            "actor": actor_account_id,
            "team_id": team_id,
            "after_json": json.dumps(
                {"owner_user_id": user_id, "team_name": team_name, "level_id": body.level_id},
                ensure_ascii=False,
            ),
        },
    )
    await db.commit()
    return await get_partner(db, team_id)


async def update_partner(
    db: AsyncSession, team_id: int, body: PartnerStaffUpdate, actor_account_id: int
) -> PartnerStaffDetail:
    existing = await get_partner(db, team_id)
    values = body.model_dump(exclude_unset=True)
    if values:
        assignments = ", ".join(f"`{key}` = :{key}" for key in values)
        await db.execute(
            text(f"UPDATE partner_team SET {assignments}, updated_at = UTC_TIMESTAMP() WHERE id = :id"),
            {**values, "id": team_id},
        )
        await db.execute(
            text("""INSERT INTO business_audit_log
                (actor_user_id, action, resource_type, resource_id, after_json)
                VALUES (:actor, 'partner.update', 'partner_team', :id, :after_json)"""),
            {
                "actor": actor_account_id,
                "id": team_id,
                "after_json": json.dumps(
                    {k: (str(v) if not isinstance(v, (int, float, str, bool, type(None))) else v)
                     for k, v in values.items()},
                    ensure_ascii=False,
                ),
            },
        )
        await db.commit()
    return await get_partner(db, team_id) if values else existing


async def delete_partner(db: AsyncSession, team_id: int, actor_account_id: int) -> dict[str, Any]:
    """关闭合伙人：把团队置为关闭、成员关系全部移出，并撤销 partner 角色。"""
    partner = await get_partner(db, team_id)
    await db.execute(
        text("""UPDATE partner_membership
            SET status = 2, left_at = UTC_TIMESTAMP(), changed_by = :actor,
                change_reason = '合伙人关闭，团队成员移出'
            WHERE team_id = :team_id AND status = 1"""),
        {"team_id": team_id, "actor": actor_account_id},
    )
    await db.execute(
        text("UPDATE partner_team SET status = 2, updated_at = UTC_TIMESTAMP() WHERE id = :id"),
        {"id": team_id},
    )
    await db.execute(
        text("""UPDATE user_role SET status = 3, revoked_at = UTC_TIMESTAMP(),
            revoke_reason = '合伙人团队关闭'
            WHERE user_id = :uid AND role_code = 'partner'"""),
        {"uid": partner.user_id},
    )
    await db.execute(
        text("""INSERT INTO business_audit_log
            (actor_user_id, action, resource_type, resource_id, after_json)
            VALUES (:actor, 'partner.delete', 'partner_team', :id, :after_json)"""),
        {
            "actor": actor_account_id,
            "id": team_id,
            "after_json": json.dumps({"status": 2}, ensure_ascii=False),
        },
    )
    await db.commit()
    return {"team_id": team_id, "status": 2, "removed_members": partner.member_count}


# ─── 团队关系 ──────────────────────────────────────────────────

# 团队业绩贡献口径与合伙人列表保持一致：该推广红娘名下会员的已支付订单总额。
_RELATION_SELECT = """SELECT pm.id, pm.promoter_id, pm.team_id, pm.status, pm.joined_at,
    pm.left_at, pm.change_reason,
    u.nickname AS promoter_name, u.avatar AS promoter_avatar, u.phone AS promoter_phone,
    t.name AS team_name,
    (SELECT COUNT(*) FROM promotion_attribution pa
        WHERE pa.promoter_id = pm.promoter_id AND pa.status = 1) AS member_count,
    COALESCE((SELECT SUM(po.amount) FROM promotion_attribution pa2
        JOIN payment_order po ON po.user_id = pa2.user_id AND po.status = 1
        WHERE pa2.promoter_id = pm.promoter_id AND pa2.status = 1), 0) AS performance_amount
    FROM partner_membership pm
    JOIN users u ON u.id = pm.promoter_id
    LEFT JOIN partner_team t ON t.id = pm.team_id"""


def _relation_item(row: dict[str, Any]) -> PartnerRelationItem:
    status = int(row["status"] or 1)
    return PartnerRelationItem(
        id=int(row["id"]),
        promoter_id=int(row["promoter_id"]),
        promoter_name=row.get("promoter_name"),
        promoter_avatar=row.get("promoter_avatar"),
        promoter_phone=row.get("promoter_phone"),
        team_id=int(row["team_id"]),
        team_name=row.get("team_name"),
        joined_at=_dt(row.get("joined_at")),
        left_at=_dt(row.get("left_at")),
        member_count=int(row.get("member_count") or 0),
        performance_amount=_money(row.get("performance_amount")),
        status=status,
        status_label=_RELATION_STATUS_LABEL.get(status, "正常"),
        change_reason=row.get("change_reason"),
    )


async def list_relations(
    db: AsyncSession,
    page: int,
    page_size: int,
    team_id: int | None = None,
    keyword: str | None = None,
    status: int | None = None,
) -> PartnerRelationPage:
    conditions = ["1 = 1"]
    params: dict[str, object] = {"limit": page_size, "offset": (page - 1) * page_size}
    if team_id is not None:
        conditions.append("pm.team_id = :team_id")
        params["team_id"] = team_id
    if keyword:
        conditions.append("(u.nickname LIKE CONCAT('%', :keyword, '%') OR u.phone LIKE CONCAT('%', :keyword, '%'))")
        params["keyword"] = keyword
    if status in (1, 2, 3):
        conditions.append("pm.status = :status")
        params["status"] = status
    else:
        # 默认只看生效中的团队关系，避免历史移出记录干扰列表
        conditions.append("pm.status = 1")
    where = " AND ".join(conditions)
    rows = await db.execute(
        text(f"{_RELATION_SELECT} WHERE {where} ORDER BY pm.joined_at DESC, pm.id DESC LIMIT :limit OFFSET :offset"),
        params,
    )
    total = int(
        (
            await db.execute(
                text(
                    f"""SELECT COUNT(*) FROM partner_membership pm
                    JOIN users u ON u.id = pm.promoter_id WHERE {where}"""
                ),
                {k: v for k, v in params.items() if k not in {"limit", "offset"}},
            )
        ).scalar()
        or 0
    )
    return PartnerRelationPage(
        items=[_relation_item(dict(row)) for row in rows.mappings().all()],
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
    )


async def list_team_options(db: AsyncSession) -> list[PartnerTeamOption]:
    """团队下拉：只取 status=1 的团队，供人工绑定与筛选使用。"""
    rows = await db.execute(
        text(
            """SELECT t.id, t.name, t.owner_user_id, u.nickname AS owner_name,
                t.level_id, lc.level_name
            FROM partner_team t
            LEFT JOIN users u ON u.id = t.owner_user_id
            LEFT JOIN partner_level_config lc ON lc.level_id = t.level_id
            WHERE t.status = 1
            ORDER BY t.id DESC"""
        )
    )
    return [
        PartnerTeamOption(
            id=int(row["id"]),
            name=str(row["name"]),
            owner_user_id=_int_or_none(row["owner_user_id"]),
            owner_name=row["owner_name"],
            level_id=int(row["level_id"]) if row["level_id"] in (1, 2, 3) else None,
            level_name=row["level_name"],
        )
        for row in rows.mappings().all()
    ]


async def bind_relation(
    db: AsyncSession, body: PartnerRelationBind, actor_account_id: int
) -> PartnerRelationResult:
    """人工绑定：已在其它团队的推广红娘先记录为「变更」移出，再落到目标团队。"""
    team = (
        await db.execute(
            text("SELECT id, name FROM partner_team WHERE id = :id AND status = 1"),
            {"id": body.team_id},
        )
    ).mappings().first()
    if not team:
        raise HTTPException(400, detail="合伙团队不存在或已关闭")

    promoter_id = body.promoter_user_id
    if promoter_id is None:
        promoter_id = (
            await db.execute(
                text("""SELECT u.id FROM users u
                    WHERE u.status = 1 AND u.nickname = :lookup
                      AND EXISTS (SELECT 1 FROM user_matchmaker_apply ma
                            WHERE ma.user_id = u.id AND ma.application_type = 'promoter')
                    ORDER BY u.id DESC LIMIT 1"""),
                {"lookup": (body.promoter_lookup or "").strip()},
            )
        ).scalar()
    if promoter_id is None:
        raise HTTPException(404, detail="未找到该推广红娘")

    existing_team = (
        await db.execute(
            text("SELECT team_id FROM partner_membership WHERE promoter_id = :id AND status = 1"),
            {"id": promoter_id},
        )
    ).scalar()
    if existing_team is not None and int(existing_team) == body.team_id:
        raise HTTPException(409, detail="该推广红娘已隶属该团队")

    # 结束当前归属（有则记为「变更」）
    await db.execute(
        text("""UPDATE partner_membership
            SET status = 3, left_at = UTC_TIMESTAMP(), changed_by = :actor, change_reason = :reason
            WHERE promoter_id = :uid AND status = 1"""),
        {
            "actor": actor_account_id,
            "reason": body.reason or "后台人工变更团队",
            "uid": promoter_id,
        },
    )
    await db.execute(
        text("""INSERT INTO partner_membership
            (team_id, promoter_id, status, joined_at, changed_by, change_reason)
            VALUES (:team_id, :uid, 1, UTC_TIMESTAMP(), :actor, :reason)
            ON DUPLICATE KEY UPDATE
                team_id = VALUES(team_id), joined_at = VALUES(joined_at),
                left_at = NULL, changed_by = VALUES(changed_by),
                change_reason = VALUES(change_reason)"""),
        {
            "team_id": body.team_id,
            "uid": promoter_id,
            "actor": actor_account_id,
            "reason": body.reason,
        },
    )
    await db.execute(
        text("""INSERT INTO business_audit_log
            (actor_user_id, action, resource_type, resource_id, after_json)
            VALUES (:actor, 'partner_relation.bind', 'partner_membership', :uid, :after_json)"""),
        {
            "actor": actor_account_id,
            "uid": promoter_id,
            "after_json": json.dumps(
                {"team_id": body.team_id, "previous_team_id": _int_or_none(existing_team), "reason": body.reason},
                ensure_ascii=False,
            ),
        },
    )
    await db.commit()
    return PartnerRelationResult(
        promoter_id=int(promoter_id),
        team_id=body.team_id,
        team_name=str(team["name"]),
        status="BOUND",
        message="已绑定团队关系",
    )


async def remove_relation(
    db: AsyncSession, relation_id: int, body: PartnerRelationRemove, actor_account_id: int
) -> PartnerRelationResult:
    relation = (
        await db.execute(
            text("SELECT id, promoter_id, team_id, status FROM partner_membership WHERE id = :id"),
            {"id": relation_id},
        )
    ).mappings().first()
    if not relation:
        raise HTTPException(404, detail="团队关系不存在")
    if int(relation["status"]) != 1:
        raise HTTPException(409, detail="该团队关系已不是生效状态")
    await db.execute(
        text("""UPDATE partner_membership
            SET status = 2, left_at = UTC_TIMESTAMP(), changed_by = :actor, change_reason = :reason
            WHERE id = :id"""),
        {"id": relation_id, "actor": actor_account_id, "reason": body.reason},
    )
    await db.execute(
        text("""INSERT INTO business_audit_log
            (actor_user_id, action, resource_type, resource_id, reason)
            VALUES (:actor, 'partner_relation.remove', 'partner_membership', :id, :reason)"""),
        {"actor": actor_account_id, "id": relation_id, "reason": body.reason},
    )
    await db.commit()
    return PartnerRelationResult(
        promoter_id=int(relation["promoter_id"]),
        team_id=int(relation["team_id"]),
        status="REMOVED",
        message="已移出团队",
    )


# ─── 分成明细 ──────────────────────────────────────────────────

_ENTRY_SELECT = """SELECT ce.id, ce.created_at, ce.beneficiary_id, ce.order_id,
    ce.base_amount, ce.amount, ce.status, ce.rule_id, ce.source, ce.remark,
    p.nickname AS partner_name,
    t.id AS team_id, t.name AS team_name,
    pa.promoter_id, pr.nickname AS promoter_name,
    po.order_no, po.user_id AS consumer_id, po.product_name,
    c.nickname AS consumer_name, c.gender AS consumer_gender,
    COALESCE(rule.name, po.product_name) AS event_name
    FROM commission_entry ce
    LEFT JOIN users p ON p.id = ce.beneficiary_id
    LEFT JOIN partner_team t ON t.owner_user_id = ce.beneficiary_id
    LEFT JOIN payment_order po ON po.id = ce.order_id
    LEFT JOIN promotion_attribution pa ON pa.user_id = po.user_id AND pa.status = 1
    LEFT JOIN users pr ON pr.id = pa.promoter_id
    LEFT JOIN users c ON c.id = po.user_id
    LEFT JOIN commission_rule rule ON rule.id = ce.rule_id"""

_GENDER_LABEL = {1: "男", 2: "女"}


def _entry_item(row: dict[str, Any]) -> PartnerCommissionEntryItem:
    event_name = row.get("event_name")
    consumer_name = row.get("consumer_name")
    # 分成事件 = 「相亲会员昵称(Q会员号) - 性别 - 事件名」，与列表 mockup 文案对齐
    description = None
    if event_name:
        if consumer_name:
            gender = _GENDER_LABEL.get(int(row["consumer_gender"]) if row.get("consumer_gender") else 0, "未知")
            description = f"相亲会员{consumer_name}(Q{row.get('consumer_id')}) - {gender} - {event_name}"
        else:
            description = str(event_name)
    return PartnerCommissionEntryItem(
        id=int(row["id"]),
        created_at=_dt(row.get("created_at")),
        partner_id=int(row["beneficiary_id"]),
        partner_name=row.get("partner_name") or f"合伙人#{row['beneficiary_id']}",
        team_id=_int_or_none(row.get("team_id")),
        team_name=row.get("team_name"),
        promoter_id=_int_or_none(row.get("promoter_id")),
        promoter_name=row.get("promoter_name"),
        event_type=event_name,
        event_name=description,
        consumer_id=_int_or_none(row.get("consumer_id")),
        consumer_name=consumer_name,
        order_id=_int_or_none(row.get("order_id")),
        order_no=row.get("order_no"),
        base_amount=_money(row.get("base_amount")),
        amount=_money(row.get("amount")),
        status=str(row.get("status") or "PENDING"),
        source=str(row.get("source") or "order"),
        remark=row.get("remark"),
    )


async def list_commission_entries(
    db: AsyncSession,
    page: int,
    page_size: int,
    partner_id: int | None = None,
    rule_id: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> PartnerCommissionEntryPage:
    conditions = ["ce.beneficiary_type = 'partner'"]
    params: dict[str, object] = {"limit": page_size, "offset": (page - 1) * page_size}
    if partner_id is not None:
        conditions.append("ce.beneficiary_id = :partner_id")
        params["partner_id"] = partner_id
    if rule_id is not None:
        conditions.append("ce.rule_id = :rule_id")
        params["rule_id"] = rule_id
    if start_date:
        conditions.append("ce.created_at >= :start_date")
        params["start_date"] = f"{start_date} 00:00:00"
    if end_date:
        conditions.append("ce.created_at < DATE_ADD(:end_date, INTERVAL 1 DAY)")
        params["end_date"] = f"{end_date} 00:00:00"
    where = " AND ".join(conditions)
    rows = await db.execute(
        text(
            f"{_ENTRY_SELECT} WHERE {where} ORDER BY ce.created_at DESC, ce.id DESC LIMIT :limit OFFSET :offset"
        ),
        params,
    )
    total = int(
        (
            await db.execute(
                text(f"SELECT COUNT(*) FROM commission_entry ce WHERE {where}"),
                {k: v for k, v in params.items() if k not in {"limit", "offset"}},
            )
        ).scalar()
        or 0
    )
    return PartnerCommissionEntryPage(
        items=[_entry_item(dict(row)) for row in rows.mappings().all()],
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
    )


async def commission_entry_options(db: AsyncSession) -> PartnerCommissionEntryOptions:
    partner_rows = await db.execute(
        text(
            """SELECT t.owner_user_id AS id, t.id AS team_id, t.name AS team_name,
                u.nickname AS name, u.avatar
            FROM partner_team t
            JOIN users u ON u.id = t.owner_user_id
            WHERE t.status = 1
            ORDER BY t.id DESC"""
        )
    )
    rule_rows = await db.execute(
        text(
            """SELECT id, name FROM commission_rule
            WHERE beneficiary_type = 'partner' AND status = 1
            ORDER BY priority DESC, id DESC"""
        )
    )
    return PartnerCommissionEntryOptions(
        partners=[
            PartnerCommissionPartnerOption(
                id=int(row["id"]),
                name=row["name"] or f"合伙人#{row['id']}",
                team_id=int(row["team_id"]),
                team_name=row["team_name"],
                avatar=row["avatar"],
            )
            for row in partner_rows.mappings().all()
        ],
        events=[
            PartnerCommissionEventOption(id=int(row["id"]), name=str(row["name"]))
            for row in rule_rows.mappings().all()
        ],
    )


async def create_commission_entry(
    db: AsyncSession,
    actor_account_id: int,
    body: PartnerCommissionEntryCreate,
) -> PartnerCommissionEntryCreateResult:
    """后台手工录入一笔合伙人分成：写 commission_entry + account_ledger（计入合伙人余额）。"""
    team = (
        await db.execute(
            text("SELECT id, name FROM partner_team WHERE owner_user_id = :uid AND status = 1"),
            {"uid": body.partner_user_id},
        )
    ).mappings().first()
    if not team:
        raise HTTPException(400, detail="合伙人不存在或团队已关闭")

    rule_name: str | None = None
    rule_version: int | None = None
    if body.rule_id is not None:
        rule = (
            await db.execute(
                text("""SELECT id, name, version FROM commission_rule
                    WHERE id = :id AND beneficiary_type = 'partner' AND status = 1"""),
                {"id": body.rule_id},
            )
        ).mappings().first()
        if not rule:
            raise HTTPException(400, detail="分成事件不存在或已停用")
        rule_name = str(rule["name"])
        rule_version = int(rule["version"]) if rule["version"] is not None else None

    if body.consumer_user_id is not None:
        consumer = await db.scalar(
            text("SELECT id FROM users WHERE id = :id"), {"id": body.consumer_user_id}
        )
        if consumer is None:
            raise HTTPException(400, detail="购买账号不存在")

    amount = Decimal(str(body.amount))
    base_amount = Decimal(str(body.base_amount)) if body.base_amount is not None else amount
    key = f"partner-manual-{uuid.uuid4().hex}"
    result = await db.execute(
        text("""INSERT INTO commission_entry
            (order_id, beneficiary_type, beneficiary_id, rule_id, rule_version,
             base_amount, amount, status, source, remark, idempotency_key)
            VALUES (NULL, 'partner', :beneficiary_id, :rule_id, :rule_version,
             :base_amount, :amount, 'AVAILABLE', 'manual', :remark, :key)"""),
        {
            "beneficiary_id": body.partner_user_id,
            "rule_id": body.rule_id,
            "rule_version": rule_version,
            "base_amount": f"{base_amount:.2f}",
            "amount": f"{amount:.2f}",
            "remark": body.remark,
            "key": key,
        },
    )
    entry_id = int(result.lastrowid)

    ledger = await db.execute(
        text("""INSERT INTO account_ledger
            (account_type, account_id, direction, amount, state, source_type, source_id, idempotency_key)
            VALUES ('user', :account_id, 'CREDIT', :amount, 'AVAILABLE', 'commission_entry', :source_id, :key)"""),
        {
            "account_id": body.partner_user_id,
            "amount": f"{amount:.2f}",
            "source_id": entry_id,
            "key": f"{key}-ledger",
        },
    )
    ledger_id = int(ledger.lastrowid)

    await db.execute(
        text("""INSERT INTO business_audit_log
            (actor_user_id, action, resource_type, resource_id, after_json)
            VALUES (:actor, 'partner_commission.create', 'commission_entry', :entry_id, :after_json)"""),
        {
            "actor": actor_account_id,
            "entry_id": entry_id,
            "after_json": json.dumps(
                {
                    "partner_user_id": body.partner_user_id,
                    "consumer_user_id": body.consumer_user_id,
                    "rule_name": rule_name,
                    "amount": f"{amount:.2f}",
                    "remark": body.remark,
                },
                ensure_ascii=False,
            ),
        },
    )
    await db.commit()

    balance = (
        await db.execute(
            text("""SELECT COALESCE(SUM(CASE WHEN direction = 'CREDIT' THEN amount ELSE -amount END), 0) AS balance
                FROM account_ledger
                WHERE account_type = 'user' AND account_id = :uid AND state <> 'REVERSED'"""),
            {"uid": body.partner_user_id},
        )
    ).scalar()

    row = (
        await db.execute(text(f"{_ENTRY_SELECT} WHERE ce.id = :id"), {"id": entry_id})
    ).mappings().first()
    return PartnerCommissionEntryCreateResult(
        entry=_entry_item(dict(row)) if row else PartnerCommissionEntryItem(
            id=entry_id, partner_id=body.partner_user_id, amount=f"{amount:.2f}"
        ),
        ledger_id=ledger_id,
        balance_after=_money(balance),
    )

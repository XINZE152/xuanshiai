"""Platform-level matchmaker staff administration services."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentMatchmakerAdmin
from app.core.security import hash_password
from app.schemas.matchmaker_admin import MatchmakerAdminAccount
from app.services.matchmaker_admin_auth import _issue_session
from app.schemas.matchmaker_staff_admin import (
    AdminMenuNode,
    CommissionLevelItem,
    MatchmakerDeleteResponse,
    MatchmakerDetailReport,
    MatchmakerPermissions,
    MatchmakerPermissionsUpdate,
    MatchmakerPlatformTokenResponse,
    MatchmakerPosterResponse,
    MatchmakerStaffCreate,
    MatchmakerStaffDetail,
    MatchmakerStaffItem,
    MatchmakerStaffPage,
    MatchmakerUserCandidate,
    MatchmakerStaffUpdate,
    MatchmakerTutorial,
    MatchmakerVisibilityUpdate,
    MatchmakerLockUpdate,
    MatchmakerWorkReport,
    FunnelItem,
    StoreDictItem,
)


def _dt(value: object | None) -> datetime | None:
    return (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(str(value))
        if value
        else None
    )


def _item(row: dict) -> MatchmakerStaffItem:
    return MatchmakerStaffItem(
        id=int(row["id"]),
        avatar=row.get("avatar"),
        display_name=row.get("display_name") or row.get("nickname") or "",
        username=row.get("username"),
        store_id=int(row["store_id"]) if row.get("store_id") else None,
        store_name=row.get("store_name"),
        role_tag=row.get("role_tag") or "normal",
        role_label="超级红娘" if row.get("role_tag") == "super" else "普通红娘",
        phone=row.get("phone"),
        wechat=row.get("wechat"),
        wechat_qr=row.get("wechat_qr"),
        commission_level_id=int(row["commission_level_id"])
        if row.get("commission_level_id")
        else None,
        commission_level_name=row.get("commission_level_name"),
        commission_rate=Decimal(str(row["rate_percent"]))
        if row.get("rate_percent") is not None
        else None,
        success_count=int(row.get("success_count") or 0),
        commission_amount=Decimal(str(row.get("commission_amount") or 0)),
        locked=bool(row.get("locked")),
        visible=bool(row.get("visible", 1)),
        description=row.get("description"),
        slogan=row.get("slogan"),
        sort=int(row.get("sort") or 0),
        # 旧库行可能为 NULL，缺省按允许（1）处理
        contact_editable=bool(row["contact_editable"])
        if row.get("contact_editable") is not None
        else True,
        lock_at=_dt(row.get("lock_at")),
        created_at=_dt(row.get("created_at")),
        updated_at=_dt(row.get("updated_at")),
    )


SELECT_STAFF = """SELECT u.id, u.avatar, u.nickname, u.phone, u.created_at, u.updated_at,
    p.wechat, p.wechat_qr, p.role_tag, p.visible, p.locked, p.description, p.slogan, p.sort, p.contact_editable, p.lock_at, p.commission_level_id,
    l.name commission_level_name, l.rate_percent,
    a.id account_id, a.username,
    o.id store_id, COALESCE(o.display_name, o.name) store_name,
    (SELECT COUNT(*) FROM matchmaker_service s WHERE s.matchmaker_id = u.id AND s.status = 2) success_count,
    (SELECT COUNT(*) FROM matchmaker_menu_permission mmp WHERE mmp.matchmaker_user_id = u.id) menu_permission_count,
    (SELECT COALESCE(SUM(e.amount), 0) FROM commission_entry e WHERE e.beneficiary_type = 'service_matchmaker' AND e.beneficiary_id = u.id AND e.status <> 'REVERSED') commission_amount
    FROM users u JOIN user_matchmaker_apply ma ON ma.user_id = u.id AND ma.application_type = 'service_matchmaker' AND ma.status = 1
    LEFT JOIN matchmaker_profile p ON p.user_id = u.id
    LEFT JOIN commission_level l ON l.id = p.commission_level_id
    LEFT JOIN matchmaker_admin_account a ON a.matchmaker_user_id = u.id
    LEFT JOIN organization_member om ON om.user_id = u.id AND om.role_code = 'store_matchmaker' AND om.status = 1
    LEFT JOIN organization o ON o.id = om.organization_id AND o.org_type = 'store'
    WHERE p.deleted_at IS NULL"""


async def search_user_candidates(db: AsyncSession, keyword: str, limit: int = 10) -> list[MatchmakerUserCandidate]:
    rows = await db.execute(
        text(
            """SELECT u.id, u.nickname, u.phone, u.avatar
            FROM users u
            WHERE u.status=1
              AND (u.nickname LIKE CONCAT('%', :keyword, '%') OR u.phone LIKE CONCAT('%', :keyword, '%'))
              AND NOT EXISTS (
                SELECT 1 FROM user_matchmaker_apply ma
                WHERE ma.user_id=u.id AND ma.application_type='service_matchmaker' AND ma.status=1
              )
            ORDER BY u.id DESC LIMIT :limit"""
        ),
        {"keyword": keyword, "limit": limit},
    )
    return [MatchmakerUserCandidate(**dict(row)) for row in rows.mappings().all()]


async def list_staff(
    db: AsyncSession,
    admin: CurrentMatchmakerAdmin,
    page: int,
    page_size: int,
    keyword: str | None,
    store_id: int | None,
    level_id: int | None,
    locked: bool | None,
    in_store: bool | None = None,
) -> MatchmakerStaffPage:
    conditions = ["1=1"]
    params: dict[str, object] = {"limit": page_size, "offset": (page - 1) * page_size}
    if keyword:
        conditions.append(
            "(u.nickname LIKE CONCAT('%', :keyword, '%') OR a.username LIKE CONCAT('%', :keyword, '%') OR u.phone LIKE CONCAT('%', :keyword, '%'))"
        )
        params["keyword"] = keyword
    if store_id is not None:
        conditions.append("o.id = :store_id")
        params["store_id"] = store_id
    if level_id is not None:
        conditions.append("p.commission_level_id = :level_id")
        params["level_id"] = level_id
    if locked is not None:
        conditions.append("p.locked = :locked")
        params["locked"] = int(locked)
    if in_store is not None:
        # in_store=True 只返回已挂靠门店的分店红娘；False 只返回未挂门店的总店红娘
        conditions.append("om.id IS NOT NULL" if in_store else "om.id IS NULL")
    scope = admin.scope_condition(organization_column="o.id", params=params, user_column="u.id")
    conditions.append(scope)
    where = " AND ".join(conditions)
    rows = await db.execute(
        text(f"{SELECT_STAFF} AND {where} ORDER BY u.id DESC LIMIT :limit OFFSET :offset"), params
    )
    count = await db.execute(
        text(f"SELECT COUNT(*) FROM ({SELECT_STAFF} AND {where}) x"),
        {k: v for k, v in params.items() if k not in {"limit", "offset"}},
    )
    total = int(count.scalar() or 0)
    return MatchmakerStaffPage(
        items=[_item(dict(r)) for r in rows.mappings().all()],
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
    )


async def get_staff(db: AsyncSession, matchmaker_id: int) -> MatchmakerStaffDetail:
    row = (
        (await db.execute(text(f"{SELECT_STAFF} AND u.id = :id"), {"id": matchmaker_id}))
        .mappings()
        .first()
    )
    if not row:
        raise HTTPException(404, detail="红娘不存在")
    item = _item(dict(row))
    return MatchmakerStaffDetail(
        **item.model_dump(),
        account_id=row.get("account_id"),
        data_scope="SELF",
        intro=row.get("description"),
    )


async def _validate_refs(
    db: AsyncSession, body: MatchmakerStaffCreate | MatchmakerStaffUpdate
) -> None:
    if (
        body.store_id is not None
        and not (
            await db.execute(
                text("SELECT 1 FROM organization WHERE id=:id AND org_type='store' AND status=1"),
                {"id": body.store_id},
            )
        ).scalar()
    ):
        raise HTTPException(422, detail="门店不存在或已停用")
    if (
        body.commission_level_id is not None
        and not (
            await db.execute(
                text("SELECT 1 FROM commission_level WHERE id=:id AND status=1"),
                {"id": body.commission_level_id},
            )
        ).scalar()
    ):
        raise HTTPException(422, detail="分成级别不存在或已停用")


async def create_staff(
    db: AsyncSession, admin: CurrentMatchmakerAdmin, body: MatchmakerStaffCreate
) -> MatchmakerStaffDetail:
    await _validate_refs(db, body)
    user_id = body.user_id
    if user_id is None and body.lookup:
        column = "nickname" if body.lookup_by == "nickname" else "phone"
        user_id = (await db.execute(text(f"SELECT id FROM users WHERE {column}=:lookup AND status=1 ORDER BY id DESC LIMIT 1"), {"lookup": body.lookup.strip()})).scalar()
        if user_id is None:
            raise HTTPException(404, detail="未找到可绑定的普通用户")
    if user_id is not None:
        user = (await db.execute(text("SELECT id FROM users WHERE id=:id AND status=1 FOR UPDATE"), {"id": user_id})).mappings().first()
        if not user:
            raise HTTPException(404, detail="普通用户不存在或已停用")
        if (await db.execute(text("SELECT 1 FROM user_matchmaker_apply WHERE user_id=:id AND application_type='service_matchmaker' AND status=1"), {"id": user_id})).scalar():
            raise HTTPException(409, detail="该普通用户已经绑定红娘")
        await db.execute(text("UPDATE users SET nickname=:name, avatar=COALESCE(:avatar, avatar), phone=:phone WHERE id=:id"), {"id": user_id, "name": body.display_name, "avatar": body.avatar, "phone": body.phone})
    else:
        if (await db.execute(text("SELECT 1 FROM users WHERE phone=:phone"), {"phone": body.phone})).scalar():
            raise HTTPException(409, detail="该手机号已注册，请通过账号绑定选择普通用户")
        result = await db.execute(text("INSERT INTO users (phone,nickname,avatar,status) VALUES (:phone,:name,:avatar,1)"), {"phone": body.phone, "name": body.display_name, "avatar": body.avatar})
        user_id = int(result.lastrowid)
    await db.execute(
        text(
            "INSERT INTO user_role (user_id,role_code,status) VALUES (:id,'service_matchmaker',1)"
        ),
        {"id": user_id},
    )
    await db.execute(
        text(
            "INSERT INTO user_matchmaker_apply (user_id,application_type,real_name,phone,intro,status,reviewed_by,reviewed_at) VALUES (:id,'service_matchmaker',:name,:phone,:description,1,:actor,UTC_TIMESTAMP())"
        ),
        {
            "id": user_id,
            "name": body.display_name,
            "phone": body.phone,
            "description": body.description,
            "actor": admin.account.id,
        },
    )
    await db.execute(
        text(
            "INSERT INTO matchmaker_profile (user_id,wechat,wechat_qr,commission_level_id,role_tag,visible,description,slogan,sort,contact_editable,lock_at) VALUES (:id,:wechat,:wechat_qr,:level,:role,:visible,:description,:slogan,:sort,:contact_editable,:lock_at)"
        ),
        {
            "id": user_id,
            "wechat": body.wechat,
            "wechat_qr": body.wechat_qr,
            "level": body.commission_level_id,
            "role": body.role_tag,
            "visible": int(body.visible),
            "description": body.description,
            "slogan": body.slogan,
            "sort": body.sort,
            "contact_editable": int(body.contact_editable),
            "lock_at": body.lock_at,
        },
    )
    if body.store_id:
        await db.execute(
            text(
                "INSERT INTO organization_member (organization_id,user_id,role_code,status) VALUES (:store,:id,'store_matchmaker',1)"
            ),
            {"store": body.store_id, "id": user_id},
        )
    await db.commit()
    return await get_staff(db, user_id)


async def update_staff(
    db: AsyncSession, matchmaker_id: int, body: MatchmakerStaffUpdate
) -> MatchmakerStaffDetail:
    await get_staff(db, matchmaker_id)
    await _validate_refs(db, body)
    values = body.model_dump(exclude_unset=True)
    user_fields = {"display_name": "nickname", "avatar": "avatar", "phone": "phone"}
    for source, target in user_fields.items():
        if source in values:
            await db.execute(
                text(f"UPDATE users SET {target}=:value WHERE id=:id"),
                {"value": values[source], "id": matchmaker_id},
            )
    profile_map = {
        "wechat": "wechat",
        "wechat_qr": "wechat_qr",
        "commission_level_id": "commission_level_id",
        "role_tag": "role_tag",
        "visible": "visible",
        "description": "description",
        "slogan": "slogan",
        "sort": "sort",
        "contact_editable": "contact_editable",
        "lock_at": "lock_at",
    }
    for source, target in profile_map.items():
        if source in values:
            value = values[source]
            # visible / contact_editable 为布尔开关，入库统一转 tinyint；显式置空时保持 NULL
            if source in ("visible", "contact_editable"):
                value = None if value is None else int(value)
            await db.execute(
                text(f"UPDATE matchmaker_profile SET {target}=:value WHERE user_id=:id"),
                {"value": value, "id": matchmaker_id},
            )
    if "password" in values:
        await db.execute(
            text(
                "UPDATE matchmaker_admin_account SET password_hash=:password WHERE matchmaker_user_id=:id"
            ),
            {"password": hash_password(values["password"]), "id": matchmaker_id},
        )
    if "store_id" in values:
        await db.execute(
            text(
                "UPDATE organization_member SET status=3, ended_at=UTC_TIMESTAMP() WHERE user_id=:id AND role_code='store_matchmaker' AND status=1"
            ),
            {"id": matchmaker_id},
        )
        if values["store_id"]:
            await db.execute(
                text(
                    "INSERT INTO organization_member (organization_id,user_id,role_code,status) VALUES (:store,:id,'store_matchmaker',1)"
                ),
                {"store": values["store_id"], "id": matchmaker_id},
            )
    await db.commit()
    return await get_staff(db, matchmaker_id)


async def set_lock(
    db: AsyncSession, matchmaker_id: int, body: MatchmakerLockUpdate
) -> MatchmakerStaffDetail:
    await get_staff(db, matchmaker_id)
    await db.execute(
        text("UPDATE matchmaker_profile SET locked=:locked WHERE user_id=:id"),
        {"locked": int(body.locked), "id": matchmaker_id},
    )
    if body.locked:
        await db.execute(
            text("UPDATE matchmaker_admin_account SET status=3 WHERE matchmaker_user_id=:id"),
            {"id": matchmaker_id},
        )
    else:
        await db.execute(
            text("UPDATE matchmaker_admin_account SET status=1 WHERE matchmaker_user_id=:id"),
            {"id": matchmaker_id},
        )
    await db.commit()
    return await get_staff(db, matchmaker_id)


async def set_visibility(
    db: AsyncSession, matchmaker_id: int, body: MatchmakerVisibilityUpdate
) -> MatchmakerStaffDetail:
    await get_staff(db, matchmaker_id)
    await db.execute(
        text("UPDATE matchmaker_profile SET visible=:visible WHERE user_id=:id"),
        {"visible": int(body.visible), "id": matchmaker_id},
    )
    await db.commit()
    return await get_staff(db, matchmaker_id)


async def delete_staff(db: AsyncSession, matchmaker_id: int) -> MatchmakerDeleteResponse:
    await get_staff(db, matchmaker_id)
    related = await db.execute(
        text(
            "SELECT (SELECT COUNT(*) FROM customer_lead WHERE matchmaker_id=:id) + (SELECT COUNT(*) FROM matchmaker_service WHERE matchmaker_id=:id)"
        ),
        {"id": matchmaker_id},
    )
    if int(related.scalar() or 0):
        raise HTTPException(409, detail="红娘存在关联客源或牵线记录，禁止删除")
    await db.execute(
        text(
            "UPDATE matchmaker_profile SET deleted_at=UTC_TIMESTAMP(), visible=0 WHERE user_id=:id"
        ),
        {"id": matchmaker_id},
    )
    await db.execute(text("UPDATE users SET status=3 WHERE id=:id"), {"id": matchmaker_id})
    await db.commit()
    return MatchmakerDeleteResponse(id=matchmaker_id, deleted=True)


async def levels(db: AsyncSession) -> list[CommissionLevelItem]:
    rows = await db.execute(
        text(
            "SELECT id,code,name,rate_percent,sort,status FROM commission_level WHERE status=1 ORDER BY sort,id"
        )
    )
    return [CommissionLevelItem(**dict(r)) for r in rows.mappings().all()]


async def stores(db: AsyncSession) -> list[StoreDictItem]:
    rows = await db.execute(
        text(
            "SELECT id,code,name,display_name,status FROM organization WHERE org_type='store' ORDER BY id"
        )
    )
    return [StoreDictItem(**dict(r)) for r in rows.mappings().all()]


async def menu_tree(db: AsyncSession) -> list[AdminMenuNode]:
    rows = [
        dict(r)
        for r in (
            await db.execute(
                text(
                    "SELECT id,parent_id,name,path,menu_type,permission_code,icon,sort FROM admin_menu WHERE status=1 ORDER BY sort,id"
                )
            )
        )
        .mappings()
        .all()
    ]
    nodes = {r["id"]: AdminMenuNode(**r) for r in rows}
    roots = []
    for node in nodes.values():
        if node.parent_id and node.parent_id in nodes:
            nodes[node.parent_id].children.append(node)
        else:
            roots.append(node)
    return roots


async def permissions(db: AsyncSession, matchmaker_id: int) -> MatchmakerPermissions:
    await get_staff(db, matchmaker_id)
    rows = await db.execute(
        text("SELECT menu_id FROM matchmaker_menu_permission WHERE matchmaker_user_id=:id"),
        {"id": matchmaker_id},
    )
    return MatchmakerPermissions(
        matchmaker_id=matchmaker_id, menuIds=[int(r[0]) for r in rows.all()]
    )


async def save_permissions(
    db: AsyncSession, matchmaker_id: int, body: MatchmakerPermissionsUpdate
) -> MatchmakerPermissions:
    await get_staff(db, matchmaker_id)
    await db.execute(
        text("DELETE FROM matchmaker_menu_permission WHERE matchmaker_user_id=:id"),
        {"id": matchmaker_id},
    )
    for menu_id in set(body.menu_ids):
        if not (
            await db.execute(
                text("SELECT 1 FROM admin_menu WHERE id=:id AND status=1"), {"id": menu_id}
            )
        ).scalar():
            raise HTTPException(422, detail="菜单不存在")
        await db.execute(
            text(
                "INSERT INTO matchmaker_menu_permission (matchmaker_user_id,menu_id) VALUES (:user,:menu)"
            ),
            {"user": matchmaker_id, "menu": menu_id},
        )
    await db.commit()
    return await permissions(db, matchmaker_id)


async def work_report(
    db: AsyncSession, matchmaker_id: int, from_date: date, to_date: date
) -> MatchmakerWorkReport:
    if to_date < from_date or (to_date - from_date).days > 366:
        raise HTTPException(422, detail="日期范围必须为1至366天")
    params = {"id": matchmaker_id, "from_date": from_date, "to_date": to_date + timedelta(days=1)}
    row = (
        (
            await db.execute(
                text(
                    """SELECT (SELECT COUNT(*) FROM customer_lead WHERE matchmaker_id=:id AND created_at>=:from_date AND created_at<:to_date) new_lead_count, (SELECT COUNT(*) FROM matchmaker_service WHERE matchmaker_id=:id AND created_at>=:from_date AND created_at<:to_date) matchmaking_count, (SELECT COUNT(*) FROM matchmaker_service WHERE matchmaker_id=:id AND status=2 AND updated_at>=:from_date AND updated_at<:to_date) success_count, (SELECT COALESCE(SUM(amount),0) FROM commission_entry WHERE beneficiary_type='service_matchmaker' AND beneficiary_id=:id AND created_at>=:from_date AND created_at<:to_date AND status<>'REVERSED') commission_amount, (SELECT COUNT(*) FROM member_follow_up f JOIN resource_assignment a ON a.user_id=f.user_id AND a.matchmaker_id=:id AND a.status=1 WHERE f.created_at>=:from_date AND f.created_at<:to_date) follow_up_count, (SELECT COUNT(*) FROM resource_assignment WHERE matchmaker_id=:id AND status=1 AND effective_at<:to_date) assigned_member_count,
                    -- 以下为本次新增的 5 个工作量指标
                    -- 新增会员资料：会员归属红娘通过 resource_assignment(matchmaker_id,user_id) 记录，按生效时间落在区间内的条数；
                    -- 项目无独立 user_profile 表，会员归属以 resource_assignment 为准（与既有 assigned_member_count 同口径，仅改为区间计数）
                    (SELECT COUNT(*) FROM resource_assignment WHERE matchmaker_id=:id AND status=1 AND effective_at>=:from_date AND effective_at<:to_date) new_member_count,
                    -- 线索跟进：customer_lead_follow_up 经 lead_id 关联 customer_lead，按归属红娘与跟进创建时间计数
                    (SELECT COUNT(*) FROM customer_lead_follow_up f JOIN customer_lead c ON c.id=f.lead_id WHERE c.matchmaker_id=:id AND f.created_at>=:from_date AND f.created_at<:to_date) lead_follow_up_count,
                    -- 预约申请：meeting_request.matchmaker_id 直接关联
                    (SELECT COUNT(*) FROM meeting_request WHERE matchmaker_id=:id AND created_at>=:from_date AND created_at<:to_date) meeting_request_count,
                    -- 约会安排：meeting_record 经 request_id 关联 meeting_request，按归属红娘与约会记录创建时间计数
                    (SELECT COUNT(*) FROM meeting_record r JOIN meeting_request q ON q.id=r.request_id WHERE q.matchmaker_id=:id AND r.created_at>=:from_date AND r.created_at<:to_date) meeting_arranged_count,
                    -- 线下业绩：payment_order 已含 matchmaker_id，按 product_type='offline_vip'（平台既有线下业绩口径，见 admin_home.py）与支付成功状态、支付时间区间聚合；
                    -- 若后续下线该商品编码，需同步调整此口径
                    (SELECT COALESCE(SUM(amount),0) FROM payment_order WHERE matchmaker_id=:id AND status=1 AND product_type='offline_vip' AND pay_time>=:from_date AND pay_time<:to_date) offline_income"""
                ),
                params,
            )
        )
        .mappings()
        .one()
    )
    return MatchmakerWorkReport(
        matchmaker_id=matchmaker_id, from_date=from_date, to_date=to_date, **dict(row)
    )


async def report(
    db: AsyncSession, matchmaker_id: int, from_date: date, to_date: date
) -> MatchmakerDetailReport:
    work = await work_report(db, matchmaker_id, from_date, to_date)
    return MatchmakerDetailReport(
        matchmaker_id=matchmaker_id,
        from_date=from_date,
        to_date=to_date,
        work_report=work,
        funnel=[
            FunnelItem(stage="lead", label="客源", count=work.new_lead_count),
            FunnelItem(stage="matchmaking", label="牵线", count=work.matchmaking_count),
            FunnelItem(stage="success", label="成功", count=work.success_count),
        ],
        monthly_trends=[],
    )


async def tutorial(db: AsyncSession) -> MatchmakerTutorial:
    row = (
        (
            await db.execute(
                text(
                    "SELECT title,content,link_url,updated_at FROM matchmaker_tutorial WHERE status=1 ORDER BY id LIMIT 1"
                )
            )
        )
        .mappings()
        .first()
    )
    return (
        MatchmakerTutorial(**dict(row))
        if row
        else MatchmakerTutorial(title="红娘使用教程", content="暂无教程内容")
    )


async def platform_token(db: AsyncSession, matchmaker_id: int) -> MatchmakerPlatformTokenResponse:
    row = (
        (
            await db.execute(
                text(
                    "SELECT id,username,display_name,matchmaker_user_id,data_scope,organization_id,status,last_login_at FROM matchmaker_admin_account WHERE matchmaker_user_id=:id AND status=1"
                ),
                {"id": matchmaker_id},
            )
        )
        .mappings()
        .first()
    )
    if not row:
        raise HTTPException(404, detail="红娘后台账号不存在")
    account = MatchmakerAdminAccount(**dict(row))
    issued = await _issue_session(db, account, None, "admin-impersonation")
    return MatchmakerPlatformTokenResponse(
        matchmaker_id=matchmaker_id,
        access_token=issued.access_token,
        refresh_token=issued.refresh_token,
        expires_in=issued.expires_in,
        jump_url=f"/matchmaker/workbench?token={issued.access_token}",
    )


async def poster(matchmaker_id: int) -> MatchmakerPosterResponse:
    return MatchmakerPosterResponse(
        matchmaker_id=matchmaker_id,
        url=f"/storage/posters/matchmaker-{matchmaker_id}.png",
        qr_content=f"matchmaker:{matchmaker_id}",
    )

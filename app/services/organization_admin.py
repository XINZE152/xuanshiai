"""Independent back-office queries and state changes for organizations."""

from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.organization_admin import (
    AssignmentAdminItem,
    StoreAdminCreate,
    StoreAdminItem,
    StoreAdminUpdate,
    StoreMemberAdminItem,
    StoreReport,
    StoreReportMonthly,
    StoreReportMonthlyRow,
    StoreReportSummary,
)


def _mask(phone: str | None) -> str | None:
    return f"{phone[:3]}****{phone[-4:]}" if phone and len(phone) >= 7 else None


STORE_SELECT = """SELECT o.id, o.code, o.name, o.display_name, o.region_code,
    o.link_url, o.sort_order, o.qr_code, o.status, o.auto_redirect, o.created_at, o.updated_at,
    (SELECT COUNT(*) FROM organization_member om WHERE om.organization_id = o.id AND om.status = 1) member_count,
    (SELECT COUNT(*) FROM organization_member om WHERE om.organization_id = o.id AND om.status = 1
        AND om.role_code IN ('store_manager', 'store_matchmaker')) matchmaker_count
    FROM organization o"""


def _store_item(row) -> StoreAdminItem:
    data = dict(row)
    data["auto_redirect"] = bool(data["auto_redirect"])
    data["sort_order"] = int(data["sort_order"] or 0)
    return StoreAdminItem(**data)


async def list_stores_admin(
    db: AsyncSession, page: int, page_size: int, search: str | None = None, status: int | None = None
) -> tuple[list[StoreAdminItem], int]:
    where = ["o.org_type = 'store'"]
    params: dict[str, object] = {"limit": page_size, "offset": (page - 1) * page_size}
    if search:
        where.append("(o.name LIKE CONCAT('%', :search, '%') OR o.display_name LIKE CONCAT('%', :search, '%')"
                     " OR o.code LIKE CONCAT('%', :search, '%') OR o.region_code LIKE CONCAT('%', :search, '%'))")
        params["search"] = search
    if status is not None:
        where.append("o.status = :status")
        params["status"] = status
    clause = " AND ".join(where)
    rows = await db.execute(
        text(f"{STORE_SELECT} WHERE {clause} ORDER BY o.sort_order DESC, o.id DESC LIMIT :limit OFFSET :offset"),
        params,
    )
    total = int((await db.execute(
        text(f"SELECT COUNT(*) FROM organization o WHERE {clause}"),
        {key: value for key, value in params.items() if key not in ("limit", "offset")},
    )).scalar() or 0)
    return [_store_item(row) for row in rows.mappings().all()], total


async def create_store_admin(
    db: AsyncSession, body: StoreAdminCreate, actor_id: int
) -> StoreAdminItem:
    try:
        result = await db.execute(text("""INSERT INTO organization
            (org_type, code, name, display_name, region_code, link_url, sort_order, qr_code, auto_redirect, created_by)
            VALUES ('store', :code, :name, :display_name, :region_code, :link_url, :sort_order, :qr_code, :auto_redirect, :created_by)"""), {
            "code": body.code, "name": body.name, "display_name": body.display_name,
            "region_code": body.region_code, "link_url": body.link_url, "sort_order": body.sort_order,
            "qr_code": body.qr_code, "auto_redirect": int(body.auto_redirect), "created_by": actor_id,
        })
        store_id = int(result.lastrowid)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, detail="分站编码已存在")
    await db.execute(text("""INSERT INTO business_audit_log
        (actor_user_id, action, resource_type, resource_id)
        VALUES (:actor, 'organization.create', 'organization', :id)"""), {"actor": actor_id, "id": store_id})
    await db.commit()
    return await get_store_admin(db, store_id)


async def delete_store_admin(db: AsyncSession, store_id: int, actor_id: int) -> StoreAdminItem:
    item = await get_store_admin(db, store_id)
    if item.member_count:
        raise HTTPException(409, detail="该分站下仍有成员，请先移除成员后再删除")
    if (await db.execute(text(
        "SELECT 1 FROM resource_assignment WHERE organization_id = :id AND status = 1 LIMIT 1"
    ), {"id": store_id})).scalar():
        raise HTTPException(409, detail="该分站下仍有生效的会员归属，无法删除")
    await db.execute(text("DELETE FROM organization WHERE id = :id"), {"id": store_id})
    await db.execute(text("""INSERT INTO business_audit_log
        (actor_user_id, action, resource_type, resource_id)
        VALUES (:actor, 'organization.delete', 'organization', :id)"""), {"actor": actor_id, "id": store_id})
    await db.commit()
    return item


async def update_store(db: AsyncSession, store_id: int, body: StoreAdminUpdate, actor_id: int):
    await get_store_admin(db, store_id)
    values = body.model_dump(exclude_unset=True)
    if "auto_redirect" in values and values["auto_redirect"] is not None:
        values["auto_redirect"] = int(values["auto_redirect"])
    if values:
        assignments = ", ".join(f"{key} = :{key}" for key in values)
        await db.execute(text(
            f"UPDATE organization SET {assignments}, updated_at = UTC_TIMESTAMP() WHERE id = :id"
        ), {**values, "id": store_id})
    await db.execute(text("""INSERT INTO business_audit_log
        (actor_user_id, action, resource_type, resource_id)
        VALUES (:actor, 'organization.update', 'organization', :id)"""),
        {"actor": actor_id, "id": store_id})
    await db.commit()
    return await get_store_admin(db, store_id)


async def get_store_admin(db: AsyncSession, store_id: int) -> StoreAdminItem:
    row = (await db.execute(text(f"{STORE_SELECT} WHERE o.id = :id AND o.org_type = 'store'"),
        {"id": store_id})).mappings().first()
    if not row:
        raise HTTPException(404, detail="门店不存在")
    return _store_item(row)


async def update_store_status(db: AsyncSession, store_id: int, status: int, reason: str | None, actor_id: int):
    await get_store_admin(db, store_id)
    await db.execute(text(
        "UPDATE organization SET status = :status, updated_at = UTC_TIMESTAMP() WHERE id = :id"
    ), {"status": status, "id": store_id})
    await db.execute(text("""INSERT INTO business_audit_log
        (actor_user_id, action, resource_type, resource_id, reason)
        VALUES (:actor, 'organization.status', 'organization', :id, :reason)"""),
        {"actor": actor_id, "id": store_id, "reason": reason})
    await db.commit()
    return await get_store_admin(db, store_id)


async def list_store_members(db: AsyncSession, store_id: int, page: int, page_size: int) -> tuple[list[StoreMemberAdminItem], int]:
    await get_store_admin(db, store_id)
    params = {"store_id": store_id, "limit": page_size, "offset": (page - 1) * page_size}
    rows = await db.execute(text("""SELECT om.id, om.organization_id, om.user_id,
        u.nickname, u.phone, om.role_code, om.status, om.started_at, om.ended_at
        FROM organization_member om LEFT JOIN users u ON u.id = om.user_id
        WHERE om.organization_id = :store_id ORDER BY om.id DESC
        LIMIT :limit OFFSET :offset"""), params)
    total = int((await db.execute(text(
        "SELECT COUNT(*) FROM organization_member WHERE organization_id = :store_id"
    ), {"store_id": store_id})).scalar() or 0)
    items = [StoreMemberAdminItem(**{**dict(row), "phone_masked": _mask(row["phone"])}) for row in rows.mappings().all()]
    return items, total


async def remove_store_member(db: AsyncSession, member_id: int, reason: str, actor_id: int) -> StoreMemberAdminItem:
    row = (await db.execute(text("""SELECT om.id, om.organization_id, om.user_id,
        u.nickname, u.phone, om.role_code, om.status, om.started_at, om.ended_at
        FROM organization_member om LEFT JOIN users u ON u.id = om.user_id
        WHERE om.id = :id FOR UPDATE"""), {"id": member_id})).mappings().first()
    if not row:
        raise HTTPException(404, detail="门店成员不存在")
    if int(row["status"]) != 1:
        raise HTTPException(409, detail="门店成员已结束")
    await db.execute(text("""UPDATE organization_member SET status = 3,
        ended_at = UTC_TIMESTAMP(), end_reason = :reason WHERE id = :id"""),
        {"id": member_id, "reason": reason})
    await db.execute(text("""INSERT INTO business_audit_log
        (actor_user_id, action, resource_type, resource_id, reason)
        VALUES (:actor, 'organization.member.remove', 'organization_member', :id, :reason)"""),
        {"actor": actor_id, "id": member_id, "reason": reason})
    await db.commit()
    row = dict(row)
    row.update(status=3, ended_at=None, phone_masked=_mask(row.pop("phone")))
    return StoreMemberAdminItem(**row)


async def store_report(db: AsyncSession, store_id: int) -> StoreReport:
    await get_store_admin(db, store_id)
    row = (await db.execute(text("""SELECT
        (SELECT COUNT(*) FROM organization_member WHERE organization_id = :id AND status = 1) active_member_count,
        (SELECT COUNT(*) FROM resource_assignment WHERE organization_id = :id AND status = 1) active_assignment_count,
        (SELECT COUNT(*) FROM resource_assignment WHERE organization_id = :id) total_assignment_count"""),
        {"id": store_id})).mappings().one()
    return StoreReport(store_id=store_id, **dict(row))


async def list_assignments(db: AsyncSession, page: int, page_size: int, search: str | None = None):
    params = {"limit": page_size, "offset": (page - 1) * page_size}
    where = ["1 = 1"]
    if search:
        where.append("(u.nickname LIKE CONCAT('%', :search, '%') OR o.name LIKE CONCAT('%', :search, '%'))")
        params["search"] = search
    clause = " AND ".join(where)
    rows = await db.execute(text(f"""SELECT ra.id, ra.user_id, u.nickname,
        ra.organization_id, o.name organization_name, ra.matchmaker_id, mu.nickname matchmaker_name,
        ra.source, ra.status, ra.effective_at, ra.ended_at, ra.end_reason
        FROM resource_assignment ra JOIN users u ON u.id = ra.user_id
        LEFT JOIN organization o ON o.id = ra.organization_id
        LEFT JOIN users mu ON mu.id = ra.matchmaker_id
        WHERE {clause} ORDER BY ra.id DESC LIMIT :limit OFFSET :offset"""), params)
    count = await db.execute(text(f"""SELECT COUNT(*) FROM resource_assignment ra
        JOIN users u ON u.id = ra.user_id
        LEFT JOIN organization o ON o.id = ra.organization_id WHERE {clause}"""),
        {key: value for key, value in params.items() if key not in ("limit", "offset")})
    return [AssignmentAdminItem(**dict(row)) for row in rows.mappings().all()], int(count.scalar() or 0)


async def end_assignment(db: AsyncSession, assignment_id: int, reason: str, actor_id: int) -> AssignmentAdminItem:
    row = (await db.execute(text(
        "SELECT id FROM resource_assignment WHERE id = :id FOR UPDATE"
    ), {"id": assignment_id})).scalar()
    if not row:
        raise HTTPException(404, detail="资源分配不存在")
    await db.execute(text("""UPDATE resource_assignment SET status = 2,
        ended_at = UTC_TIMESTAMP(), end_reason = :reason WHERE id = :id AND status = 1"""),
        {"id": assignment_id, "reason": reason})
    await db.execute(text("""INSERT INTO business_audit_log
        (actor_user_id, action, resource_type, resource_id, reason)
        VALUES (:actor, 'resource_assignment.end', 'resource_assignment', :id, :reason)"""),
        {"actor": actor_id, "id": assignment_id, "reason": reason})
    await db.commit()
    items, _ = await list_assignments(db, 1, 1)
    for item in items:
        if item.id == assignment_id:
            return item
    raise HTTPException(404, detail="资源分配不存在")


# ------------------------- M5 分店报表 -------------------------
_STORE_USER_SCOPE = (
    "EXISTS (SELECT 1 FROM resource_assignment sa WHERE sa.user_id = {col} "
    "AND sa.status = 1 AND sa.organization_id = :store_id)"
)


async def store_report_summary(db: AsyncSession, store_id: int) -> StoreReportSummary:
    """分店报表 9 张统计卡（口径与平台首页 dashboard 保持一致）。"""
    store = await get_store_admin(db, store_id)
    user_scope = _STORE_USER_SCOPE.format(col="u.id")
    membership_scope = _STORE_USER_SCOPE.format(col="m.user_id")
    order_scope = _STORE_USER_SCOPE.format(col="po.user_id")
    row = (await db.execute(text(f"""SELECT
        (SELECT COUNT(*) FROM customer_lead cl WHERE cl.organization_id = :store_id) lead_count,
        (SELECT COUNT(DISTINCT sa.user_id) FROM resource_assignment sa
            WHERE sa.status = 1 AND sa.organization_id = :store_id) member_count,
        (SELECT COUNT(*) FROM matchmaker_service ms WHERE ms.status = 2
            AND EXISTS (SELECT 1 FROM organization_member om WHERE om.user_id = ms.matchmaker_id
                AND om.status = 1 AND om.organization_id = :store_id)) online_match_count,
        (SELECT COUNT(DISTINCT m.user_id) FROM user_membership m
            WHERE m.status = 1 AND (m.end_at IS NULL OR m.end_at > UTC_TIMESTAMP())
            AND NOT EXISTS (SELECT 1 FROM payment_order mo WHERE mo.order_no = m.order_no AND mo.product_type = 'offline_vip')
            AND {membership_scope}) online_vip_count,
        (SELECT COUNT(DISTINCT m.user_id) FROM user_membership m
            WHERE m.status = 1 AND (m.end_at IS NULL OR m.end_at > UTC_TIMESTAMP())
            AND EXISTS (SELECT 1 FROM payment_order mo WHERE mo.order_no = m.order_no AND mo.product_type = 'offline_vip')
            AND {membership_scope}) offline_vip_count,
        (SELECT COUNT(*) FROM meeting_record mr WHERE mr.status <> 'CANCELLED'
            AND mr.organization_id = :store_id) meeting_arranged_count,
        (SELECT COALESCE(SUM(ce.amount), 0) FROM commission_entry ce
            WHERE ce.beneficiary_type = 'store' AND ce.beneficiary_id = :store_id AND ce.status <> 'REVERSED') online_commission,
        (SELECT COALESCE(SUM(po.amount), 0) FROM payment_order po
            WHERE po.status = 1 AND po.product_type = 'offline_vip' AND {order_scope}) offline_performance,
        (SELECT COUNT(*) FROM organization o2 WHERE o2.org_type = 'store' AND o2.status <> 3
            AND o2.id <> :store_id AND (SELECT COALESCE(SUM(ce2.amount), 0) FROM commission_entry ce2
                WHERE ce2.beneficiary_type = 'store' AND ce2.beneficiary_id = o2.id AND ce2.status <> 'REVERSED') >
                (SELECT COALESCE(SUM(ce3.amount), 0) FROM commission_entry ce3
                    WHERE ce3.beneficiary_type = 'store' AND ce3.beneficiary_id = :store_id AND ce3.status <> 'REVERSED')) online_commission_rank,
        (SELECT COUNT(*) FROM organization o2 WHERE o2.org_type = 'store' AND o2.status <> 3
            AND o2.id <> :store_id AND (SELECT COALESCE(SUM(po2.amount), 0) FROM payment_order po2
                WHERE po2.status = 1 AND po2.product_type = 'offline_vip' AND EXISTS (SELECT 1 FROM resource_assignment sa2
                    WHERE sa2.user_id = po2.user_id AND sa2.status = 1 AND sa2.organization_id = o2.id)) >
                (SELECT COALESCE(SUM(po3.amount), 0) FROM payment_order po3
                    WHERE po3.status = 1 AND po3.product_type = 'offline_vip' AND {order_scope})) offline_performance_rank,
        (SELECT COUNT(*) FROM organization o2 WHERE o2.org_type = 'store' AND o2.status <> 3
            AND o2.id <> :store_id AND (SELECT COUNT(*) FROM meeting_record mr2
                WHERE mr2.status <> 'CANCELLED' AND mr2.organization_id = o2.id) >
                (SELECT COUNT(*) FROM meeting_record mr3 WHERE mr3.status <> 'CANCELLED' AND mr3.organization_id = :store_id)) meeting_rank
    """), {"store_id": store_id})).mappings().one()
    return StoreReportSummary(
        store_id=store_id,
        store_name=store.display_name or store.name,
        lead_count=int(row["lead_count"] or 0),
        member_count=int(row["member_count"] or 0),
        online_match_count=int(row["online_match_count"] or 0),
        online_vip_count=int(row["online_vip_count"] or 0),
        offline_vip_count=int(row["offline_vip_count"] or 0),
        meeting_arranged_count=int(row["meeting_arranged_count"] or 0),
        online_commission=_money(row["online_commission"]),
        offline_performance=_money(row["offline_performance"]),
        meeting_rank=int(row["meeting_rank"] or 0) + 1,
        online_commission_rank=int(row["online_commission_rank"] or 0) + 1,
        offline_performance_rank=int(row["offline_performance_rank"] or 0) + 1,
    )


def _money(value: object) -> Decimal:
    return Decimal(str(value if value is not None else "0.00"))


def _recent_months(months: int) -> list[str]:
    today = date.today()
    year, month = today.year, today.month
    collected: list[str] = []
    for _ in range(months):
        collected.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return list(reversed(collected))


async def store_report_monthly(db: AsyncSession, store_id: int, months: int = 6) -> StoreReportMonthly:
    """分店月度报表：按月聚合新增会员/客源/牵线/约会/VIP/分成。"""
    await get_store_admin(db, store_id)
    months = max(1, min(months, 24))
    month_list = _recent_months(months)
    start = datetime.strptime(f"{month_list[0]}-01", "%Y-%m-%d")
    first_of_this_month = datetime(date.today().year, date.today().month, 1)
    end = first_of_this_month + timedelta(days=32)
    end = datetime(end.year, end.month, 1)
    user_scope = _STORE_USER_SCOPE.format(col="u.id")
    order_scope = _STORE_USER_SCOPE.format(col="po.user_id")
    rows = (await db.execute(text(f"""SELECT m, SUM(new_male_members) new_male_members,
        SUM(new_female_members) new_female_members, SUM(new_leads) new_leads,
        SUM(new_online_vip) new_online_vip, SUM(new_match_requests) new_match_requests,
        SUM(new_offline_meetings) new_offline_meetings, SUM(new_offline_vip) new_offline_vip,
        SUM(online_commission) online_commission, SUM(offline_performance) offline_performance FROM (
          SELECT DATE_FORMAT(u.created_at, '%%Y-%%m') m, SUM(u.gender = 1) new_male_members,
                 SUM(u.gender = 2) new_female_members, 0 new_leads, 0 new_online_vip,
                 0 new_match_requests, 0 new_offline_meetings, 0 new_offline_vip,
                 0 online_commission, 0 offline_performance
          FROM users u WHERE u.status = 1 AND u.created_at >= :start AND u.created_at < :end AND {user_scope}
          GROUP BY m
          UNION ALL
          SELECT DATE_FORMAT(cl.created_at, '%%Y-%%m') m, 0, 0, COUNT(*), 0, 0, 0, 0, 0, 0
          FROM customer_lead cl WHERE cl.organization_id = :store_id AND cl.created_at >= :start AND cl.created_at < :end
          GROUP BY m
          UNION ALL
          SELECT DATE_FORMAT(um.created_at, '%%Y-%%m') m, 0, 0, 0,
                 SUM(NOT EXISTS (SELECT 1 FROM payment_order mo WHERE mo.order_no = um.order_no AND mo.product_type = 'offline_vip')),
                 0, 0,
                 SUM(EXISTS (SELECT 1 FROM payment_order mo WHERE mo.order_no = um.order_no AND mo.product_type = 'offline_vip')),
                 0, 0
          FROM user_membership um WHERE um.status = 1 AND um.created_at >= :start AND um.created_at < :end AND {_STORE_USER_SCOPE.format(col='um.user_id')}
          GROUP BY m
          UNION ALL
          SELECT DATE_FORMAT(mr.created_at, '%%Y-%%m') m, 0, 0, 0, 0, COUNT(*), 0, 0, 0, 0
          FROM meeting_request mr WHERE mr.organization_id = :store_id AND mr.created_at >= :start AND mr.created_at < :end
          GROUP BY m
          UNION ALL
          SELECT DATE_FORMAT(rec.created_at, '%%Y-%%m') m, 0, 0, 0, 0, 0, COUNT(*), 0, 0, 0
          FROM meeting_record rec WHERE rec.organization_id = :store_id AND rec.status <> 'CANCELLED'
            AND rec.created_at >= :start AND rec.created_at < :end
          GROUP BY m
          UNION ALL
          SELECT DATE_FORMAT(po.pay_time, '%%Y-%%m') m, 0, 0, 0, 0, 0, 0, 0, 0, COALESCE(SUM(po.amount), 0)
          FROM payment_order po WHERE po.status = 1 AND po.product_type = 'offline_vip'
            AND po.pay_time >= :start AND po.pay_time < :end AND {order_scope}
          GROUP BY m
          UNION ALL
          SELECT DATE_FORMAT(ce.created_at, '%%Y-%%m') m, 0, 0, 0, 0, 0, 0, 0, COALESCE(SUM(ce.amount), 0), 0
          FROM commission_entry ce WHERE ce.beneficiary_type = 'store' AND ce.beneficiary_id = :store_id
            AND ce.status <> 'REVERSED' AND ce.created_at >= :start AND ce.created_at < :end
          GROUP BY m
        ) monthly GROUP BY m"""), {"store_id": store_id, "start": start, "end": end})).mappings().all()
    values = {str(row["m"]): row for row in rows}
    result: list[StoreReportMonthlyRow] = []
    for month in month_list:
        item = values.get(month, {})
        result.append(StoreReportMonthlyRow(
            month=month,
            new_male_members=int(item.get("new_male_members") or 0),
            new_female_members=int(item.get("new_female_members") or 0),
            new_leads=int(item.get("new_leads") or 0),
            new_online_vip=int(item.get("new_online_vip") or 0),
            new_match_requests=int(item.get("new_match_requests") or 0),
            new_offline_meetings=int(item.get("new_offline_meetings") or 0),
            new_offline_vip=int(item.get("new_offline_vip") or 0),
            online_commission=_money(item.get("online_commission")),
            offline_performance=_money(item.get("offline_performance")),
        ))
    return StoreReportMonthly(store_id=store_id, months=result)

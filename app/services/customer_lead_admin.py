"""Customer lead management for the independent back office."""

from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.customer_lead_admin import (
    CustomerLead, CustomerLeadAbandonment, CustomerLeadAssignment, CustomerLeadCreate, CustomerLeadFollowUp,
    CustomerLeadBatchImportResult, CustomerLeadFollowUpCreate, CustomerLeadImportRow,
    CustomerLeadPage, CustomerLeadStatistics, CustomerLeadUpdate,
)
from app.services.customer_lead_contact import (
    ensure_contact_available, normalize_contact, raise_duplicate_contact,
)

LEAD_SELECT = """SELECT id, name, phone, wechat, source, intention_level, status, matchmaker_id,
    organization_id, next_follow_at, converted_user_id, remark, created_by, created_at, updated_at
    FROM customer_lead"""


def _lead(row: Any) -> CustomerLead:
    return CustomerLead(**dict(row))


async def _validate_owner(db: AsyncSession, assignment: CustomerLeadAssignment) -> None:
    if assignment.matchmaker_id is not None:
        result = await db.execute(text("""SELECT 1 FROM user_matchmaker_apply a JOIN user_role r ON r.user_id = a.user_id
            AND r.role_code = 'service_matchmaker' AND r.status = 1
            WHERE a.user_id = :id AND a.application_type = 'service_matchmaker' AND a.status = 1"""), {"id": assignment.matchmaker_id})
        if not result.scalar():
            raise HTTPException(422, detail="只能分配给有效服务红娘")
    if assignment.organization_id is not None:
        result = await db.execute(text("SELECT 1 FROM organization WHERE id = :id AND org_type = 'store' AND status = 1"), {"id": assignment.organization_id})
        if not result.scalar():
            raise HTTPException(422, detail="门店不存在或已停用")


async def create_lead(db: AsyncSession, account_id: int, request: CustomerLeadCreate) -> CustomerLead:
    phone = normalize_contact(request.phone)
    wechat = normalize_contact(request.wechat)
    await ensure_contact_available(db, phone, wechat)
    try:
        result = await db.execute(text("""INSERT INTO customer_lead
            (name, phone, wechat, source, intention_level, remark, created_by)
            VALUES (:name, :phone, :wechat, :source, :intention_level, :remark, :created_by)"""), {
            **request.model_dump(), "phone": phone, "wechat": wechat, "created_by": account_id,
        })
        lead_id = int(result.lastrowid)
        await db.execute(text("""INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id)
            VALUES (:actor, 'customer_lead.create', 'customer_lead', :id)"""), {"actor": account_id, "id": lead_id})
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise_duplicate_contact()
    return await get_lead(db, lead_id)


async def get_lead(db: AsyncSession, lead_id: int) -> CustomerLead:
    row = (await db.execute(text(f"{LEAD_SELECT} WHERE id = :id"), {"id": lead_id})).mappings().first()
    if not row:
        raise HTTPException(404, detail="客源线索不存在")
    return _lead(row)


async def list_leads(db: AsyncSession, page: int, page_size: int, status: str | None, source: str | None, matchmaker_id: int | None, search: str | None) -> CustomerLeadPage:
    where = ["1=1"]
    params: dict[str, Any] = {"limit": page_size, "offset": (page - 1) * page_size}
    if status:
        where.append("status = :status")
        params["status"] = status
    if source:
        where.append("source = :source")
        params["source"] = source
    if matchmaker_id is not None:
        where.append("matchmaker_id = :matchmaker_id")
        params["matchmaker_id"] = matchmaker_id
    if search:
        where.append("(name LIKE CONCAT('%', :search, '%') OR phone LIKE CONCAT('%', :search, '%') OR wechat LIKE CONCAT('%', :search, '%'))")
        params["search"] = search
    clause = " AND ".join(where)
    rows = await db.execute(text(f"{LEAD_SELECT} WHERE {clause} ORDER BY id DESC LIMIT :limit OFFSET :offset"), params)
    count = await db.execute(text(f"SELECT COUNT(*) FROM customer_lead WHERE {clause}"), {k: v for k, v in params.items() if k not in ("limit", "offset")})
    total = int(count.scalar() or 0)
    return CustomerLeadPage(items=[_lead(row) for row in rows.mappings().all()], page=page, page_size=page_size, total=total, has_more=page * page_size < total)


async def update_lead(db: AsyncSession, account_id: int, lead_id: int, request: CustomerLeadUpdate) -> CustomerLead:
    current = await get_lead(db, lead_id)
    values = request.model_dump(exclude_unset=True)
    target_status = values.get("status") or current.status
    # 目标状态仍为有效线索时，联系方式不得与其它有效线索重复；改为 LOST/CLOSED 后生成列会置空。
    if target_status not in ("LOST", "CLOSED"):
        target_phone = normalize_contact(values["phone"]) if "phone" in values else normalize_contact(current.phone)
        target_wechat = normalize_contact(values["wechat"]) if "wechat" in values else normalize_contact(current.wechat)
        await ensure_contact_available(db, target_phone, target_wechat, exclude_lead_id=lead_id)
    assignments = ", ".join(f"{key} = :{key}" for key in values)
    try:
        await db.execute(text(f"UPDATE customer_lead SET {assignments}, updated_at = UTC_TIMESTAMP() WHERE id = :id"), {**values, "id": lead_id})
        await db.execute(text("INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id) VALUES (:actor, 'customer_lead.update', 'customer_lead', :id)"), {"actor": account_id, "id": lead_id})
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise_duplicate_contact()
    return await get_lead(db, lead_id)


async def assign_lead(db: AsyncSession, account_id: int, lead_id: int, request: CustomerLeadAssignment) -> CustomerLead:
    await get_lead(db, lead_id)
    await _validate_owner(db, request)
    await db.execute(text("UPDATE customer_lead SET matchmaker_id = :matchmaker_id, organization_id = :organization_id, updated_at = UTC_TIMESTAMP() WHERE id = :id"), {**request.model_dump(), "id": lead_id})
    await db.execute(text("INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id) VALUES (:actor, 'customer_lead.assign', 'customer_lead', :id)"), {"actor": account_id, "id": lead_id})
    await db.commit()
    return await get_lead(db, lead_id)


async def add_follow_up(db: AsyncSession, account_id: int, lead_id: int, request: CustomerLeadFollowUpCreate) -> CustomerLeadFollowUp:
    await get_lead(db, lead_id)
    result = await db.execute(text("""INSERT INTO customer_lead_follow_up
        (lead_id, method, content, intention_level, next_follow_at, created_by)
        VALUES (:lead_id, :method, :content, :intention_level, :next_follow_at, :created_by)"""), {**request.model_dump(), "lead_id": lead_id, "created_by": account_id})
    follow_id = int(result.lastrowid)
    await db.execute(text("UPDATE customer_lead SET status = CASE WHEN status = 'NEW' THEN 'CONTACTED' ELSE status END, next_follow_at = :next_follow_at, updated_at = UTC_TIMESTAMP() WHERE id = :id"), {"next_follow_at": request.next_follow_at, "id": lead_id})
    await db.commit()
    row = (await db.execute(text("SELECT id, lead_id, method, content, intention_level, next_follow_at, created_by, created_at FROM customer_lead_follow_up WHERE id = :id"), {"id": follow_id})).mappings().one()
    return CustomerLeadFollowUp(**dict(row))


async def list_follow_ups(db: AsyncSession, lead_id: int, page: int, page_size: int) -> list[CustomerLeadFollowUp]:
    await get_lead(db, lead_id)
    rows = await db.execute(text("""SELECT id, lead_id, method, content, intention_level, next_follow_at, created_by, created_at
        FROM customer_lead_follow_up WHERE lead_id = :lead_id ORDER BY id DESC LIMIT :limit OFFSET :offset"""), {"lead_id": lead_id, "limit": page_size, "offset": (page - 1) * page_size})
    return [CustomerLeadFollowUp(**dict(row)) for row in rows.mappings().all()]


async def lead_statistics(db: AsyncSession) -> CustomerLeadStatistics:
    row = (await db.execute(text("""SELECT COUNT(*) total, SUM(status = 'NEW') new_count, SUM(status = 'CONTACTED') contacted_count,
        SUM(status = 'INTENDED') intended_count, SUM(status = 'CONVERTED') converted_count, SUM(status = 'LOST') lost_count
        FROM customer_lead"""))).mappings().one()
    return CustomerLeadStatistics(**{key: int(row[key] or 0) for key in ("total", "new_count", "contacted_count", "intended_count", "converted_count", "lost_count")})


async def abandon_lead(db: AsyncSession, account_id: int, lead_id: int, reason: str) -> CustomerLeadAbandonment:
    lead = await get_lead(db, lead_id)
    if lead.status in ("CONVERTED", "CLOSED"):
        raise HTTPException(409, detail="已入库或已关闭的客源不能弃海")
    active = await db.execute(text("SELECT 1 FROM customer_lead_abandonment WHERE lead_id = :id AND restored_at IS NULL"), {"id": lead_id})
    if active.scalar():
        raise HTTPException(409, detail="该客源已在弃海池")
    result = await db.execute(text("""INSERT INTO customer_lead_abandonment (lead_id, reason, abandoned_by)
        VALUES (:lead_id, :reason, :account_id)"""), {"lead_id": lead_id, "reason": reason, "account_id": account_id})
    await db.execute(text("UPDATE customer_lead SET status = 'LOST', matchmaker_id = NULL, next_follow_at = NULL WHERE id = :id"), {"id": lead_id})
    await db.execute(text("INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id) VALUES (:actor, 'customer_lead.abandon', 'customer_lead', :id)"), {"actor": account_id, "id": lead_id})
    await db.commit()
    row = (await db.execute(text("SELECT id, lead_id, reason, abandoned_by, abandoned_at, restored_by, restored_at, restore_reason FROM customer_lead_abandonment WHERE id = :id"), {"id": int(result.lastrowid)})).mappings().one()
    return CustomerLeadAbandonment(**dict(row))


async def restore_lead(db: AsyncSession, account_id: int, lead_id: int, reason: str) -> CustomerLead:
    current = await get_lead(db, lead_id)
    active = (await db.execute(text("SELECT id FROM customer_lead_abandonment WHERE lead_id = :id AND restored_at IS NULL ORDER BY id DESC LIMIT 1"), {"id": lead_id})).scalar()
    if not active:
        raise HTTPException(409, detail="该客源不在弃海池")
    # 恢复后线索回到有效状态，先确认联系方式没有被其它有效线索占用。
    await ensure_contact_available(
        db,
        normalize_contact(current.phone),
        normalize_contact(current.wechat),
        exclude_lead_id=lead_id,
        detail="该客源的联系方式已被其他有效线索占用，无法恢复",
    )
    await db.execute(text("UPDATE customer_lead_abandonment SET restored_by = :account_id, restored_at = UTC_TIMESTAMP(), restore_reason = :reason WHERE id = :id"), {"account_id": account_id, "reason": reason, "id": active})
    await db.execute(text("UPDATE customer_lead SET status = 'NEW', updated_at = UTC_TIMESTAMP() WHERE id = :id"), {"id": lead_id})
    await db.execute(text("INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id) VALUES (:actor, 'customer_lead.restore', 'customer_lead', :id)"), {"actor": account_id, "id": lead_id})
    await db.commit()
    return await get_lead(db, lead_id)


async def list_abandonments(db: AsyncSession, active_only: bool) -> list[CustomerLeadAbandonment]:
    where = "WHERE restored_at IS NULL" if active_only else ""
    rows = await db.execute(text(f"SELECT id, lead_id, reason, abandoned_by, abandoned_at, restored_by, restored_at, restore_reason FROM customer_lead_abandonment {where} ORDER BY id DESC"))
    return [CustomerLeadAbandonment(**dict(row)) for row in rows.mappings().all()]


async def batch_import_leads(
    db: AsyncSession,
    account_id: int,
    rows: list[CustomerLeadImportRow],
    dup_mode: str = "skip",
) -> CustomerLeadBatchImportResult:
    """批量导入客源线索。dup_mode=skip 时联系方式重复的行跳过，append 时仍然导入。"""
    created = skipped = failed = 0
    errors: list[str] = []
    for index, row in enumerate(rows, start=2):  # Excel 数据从第 2 行起
        if not row.phone and not row.wechat:
            failed += 1
            errors.append(f"第 {index} 行：phone 或 wechat 至少提供一个")
            continue
        try:
            if dup_mode == "skip":
                contact_conditions: list[str] = []
                dup_params: dict[str, Any] = {}
                if row.phone:
                    contact_conditions.append("phone = :phone")
                    dup_params["phone"] = row.phone
                if row.wechat:
                    contact_conditions.append("wechat = :wechat")
                    dup_params["wechat"] = row.wechat
                duplicate = await db.execute(
                    text("SELECT id FROM customer_lead WHERE status NOT IN ('LOST', 'CLOSED') AND ("
                         + " OR ".join(contact_conditions) + ") LIMIT 1"),
                    dup_params,
                )
                if duplicate.scalar():
                    skipped += 1
                    continue
            result = await db.execute(text("""INSERT INTO customer_lead
                (name, phone, wechat, source, intention_level, remark, created_by)
                VALUES (:name, :phone, :wechat, :source, :intention_level, :remark, :created_by)"""),
                {**row.model_dump(), "created_by": account_id},
            )
            lead_id = int(result.lastrowid)
            await db.execute(text("""INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id)
                VALUES (:actor, 'customer_lead.import', 'customer_lead', :id)"""),
                {"actor": account_id, "id": lead_id},
            )
            created += 1
        except HTTPException:
            raise
        except Exception as exc:  # 单行失败不阻断整批
            failed += 1
            errors.append(f"第 {index} 行：{exc}")
    await db.commit()
    return CustomerLeadBatchImportResult(created=created, skipped=skipped, failed=failed, errors=errors[:50])

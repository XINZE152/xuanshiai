"""短信管理（A1）服务。

签名 / 模板为平台级配置；群发逐条写入 ``admin_sms_message`` 并调用供应商适配层。
供应商未配置（``disabled``）时**在发送前** fail-closed 503，不会留下半批记录；
逐条发送失败只影响该条，不阻断整批（记录 ``failed`` 与错误原因）。
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sensitive_fields import mask_phone
from app.schemas.admin_sms_admin import (
    SmsQuota,
    SmsRecordItem,
    SmsRecordPage,
    SmsSendRequest,
    SmsSendResult,
    SmsSignatureCreate,
    SmsSignatureItem,
    SmsSignaturePage,
    SmsSignatureUpdate,
    SmsTemplateCreate,
    SmsTemplateItem,
    SmsTemplatePage,
    SmsTemplateUpdate,
)
from app.services.sms.providers import ensure_sms_template_configured, get_sms_template_provider

_SIGNATURE_SELECT = """SELECT id, name, provider_ref, status, audit_remark, created_at, updated_at
    FROM admin_sms_signature"""

_TEMPLATE_SELECT = """SELECT t.id, t.name, t.code, t.content, t.signature_id, s.name AS signature_name,
    t.scene, t.status, t.created_at, t.updated_at
    FROM admin_sms_template t LEFT JOIN admin_sms_signature s ON s.id = t.signature_id"""

_RECORD_SELECT = """SELECT m.id, m.batch_no, m.template_id, t.name AS template_name, m.phone, m.user_id,
    m.content, m.status, m.provider_ref, m.error, m.sent_at, m.created_at
    FROM admin_sms_message m LEFT JOIN admin_sms_template t ON t.id = m.template_id"""


def _signature_item(row: Any) -> SmsSignatureItem:
    return SmsSignatureItem(**dict(row))


async def _get_signature(db: AsyncSession, signature_id: int) -> SmsSignatureItem:
    row = (
        await db.execute(text(f"{_SIGNATURE_SELECT} WHERE id = :id"), {"id": signature_id})
    ).mappings().first()
    if not row:
        raise HTTPException(404, detail="短信签名不存在")
    return _signature_item(row)


async def list_signatures(
    db: AsyncSession, page: int, page_size: int, keyword: str | None, status: str | None
) -> SmsSignaturePage:
    where: list[str] = []
    params: dict[str, Any] = {}
    if keyword:
        where.append("name LIKE :keyword")
        params["keyword"] = f"%{keyword}%"
    if status in {"pending", "approved", "rejected"}:
        where.append("status = :status")
        params["status"] = status
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    total = int((await db.execute(text(f"SELECT COUNT(*) FROM admin_sms_signature {where_sql}"), params)).scalar() or 0)
    params["limit"] = page_size
    params["offset"] = (page - 1) * page_size
    rows = (
        await db.execute(
            text(f"{_SIGNATURE_SELECT} {where_sql} ORDER BY id DESC LIMIT :limit OFFSET :offset"), params
        )
    ).mappings().all()
    return SmsSignaturePage(
        items=[_signature_item(r) for r in rows],
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
    )


async def create_signature(db: AsyncSession, actor_id: int, body: SmsSignatureCreate) -> SmsSignatureItem:
    result = await db.execute(
        text(
            "INSERT INTO admin_sms_signature (name, status, audit_remark)"
            " VALUES (:name, :status, :audit_remark)"
        ),
        {"name": body.name, "status": body.status, "audit_remark": body.audit_remark},
    )
    item = await _get_signature(db, int(result.lastrowid))
    await _audit(db, actor_id, "sms_signature.create", "admin_sms_signature", item.id)
    await db.commit()
    return item


async def update_signature(
    db: AsyncSession, actor_id: int, signature_id: int, body: SmsSignatureUpdate
) -> SmsSignatureItem:
    await _get_signature(db, signature_id)
    values = body.model_dump(exclude_unset=True)
    if values:
        assignments = ", ".join(f"{key} = :{key}" for key in values)
        await db.execute(
            text(f"UPDATE admin_sms_signature SET {assignments}, updated_at = UTC_TIMESTAMP() WHERE id = :id"),
            {**values, "id": signature_id},
        )
    await _audit(db, actor_id, "sms_signature.update", "admin_sms_signature", signature_id)
    await db.commit()
    return await _get_signature(db, signature_id)


async def delete_signature(db: AsyncSession, actor_id: int, signature_id: int) -> None:
    await _get_signature(db, signature_id)
    used = (
        await db.execute(
            text("SELECT 1 FROM admin_sms_template WHERE signature_id = :id LIMIT 1"),
            {"id": signature_id},
        )
    ).scalar_one_or_none()
    if used:
        raise HTTPException(409, detail="该签名仍被模板引用，不能删除")
    await db.execute(text("DELETE FROM admin_sms_signature WHERE id = :id"), {"id": signature_id})
    await _audit(db, actor_id, "sms_signature.delete", "admin_sms_signature", signature_id)
    await db.commit()


# ─────────────────────────── 模板 ───────────────────────────


async def _get_template(db: AsyncSession, template_id: int) -> SmsTemplateItem:
    row = (
        await db.execute(text(f"{_TEMPLATE_SELECT} WHERE t.id = :id"), {"id": template_id})
    ).mappings().first()
    if not row:
        raise HTTPException(404, detail="短信模板不存在")
    return SmsTemplateItem(**dict(row))


async def list_templates(
    db: AsyncSession, page: int, page_size: int, keyword: str | None, scene: str | None, status: int | None
) -> SmsTemplatePage:
    where: list[str] = []
    params: dict[str, Any] = {}
    if keyword:
        where.append("(t.name LIKE :keyword OR t.code LIKE :keyword)")
        params["keyword"] = f"%{keyword}%"
    if scene:
        where.append("t.scene = :scene")
        params["scene"] = scene
    if status in (0, 1):
        where.append("t.status = :status")
        params["status"] = status
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    total = int(
        (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM admin_sms_template t"
                    " LEFT JOIN admin_sms_signature s ON s.id = t.signature_id "
                    + where_sql
                ),
                params,
            )
        ).scalar()
        or 0
    )
    params["limit"] = page_size
    params["offset"] = (page - 1) * page_size
    rows = (
        await db.execute(
            text(f"{_TEMPLATE_SELECT} {where_sql} ORDER BY t.id DESC LIMIT :limit OFFSET :offset"), params
        )
    ).mappings().all()
    return SmsTemplatePage(
        items=[SmsTemplateItem(**dict(r)) for r in rows],
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
    )


async def create_template(db: AsyncSession, actor_id: int, body: SmsTemplateCreate) -> SmsTemplateItem:
    if body.signature_id is not None:
        await _get_signature(db, body.signature_id)
    result = await db.execute(
        text(
            "INSERT INTO admin_sms_template (name, code, content, signature_id, scene, status)"
            " VALUES (:name, :code, :content, :signature_id, :scene, :status)"
        ),
        body.model_dump(),
    )
    item = await _get_template(db, int(result.lastrowid))
    await _audit(db, actor_id, "sms_template.create", "admin_sms_template", item.id)
    await db.commit()
    return item


async def update_template(
    db: AsyncSession, actor_id: int, template_id: int, body: SmsTemplateUpdate
) -> SmsTemplateItem:
    await _get_template(db, template_id)
    values = body.model_dump(exclude_unset=True)
    if values.get("signature_id") is not None:
        await _get_signature(db, int(values["signature_id"]))
    if values:
        assignments = ", ".join(f"{key} = :{key}" for key in values)
        await db.execute(
            text(f"UPDATE admin_sms_template SET {assignments}, updated_at = UTC_TIMESTAMP() WHERE id = :id"),
            {**values, "id": template_id},
        )
    await _audit(db, actor_id, "sms_template.update", "admin_sms_template", template_id)
    await db.commit()
    return await _get_template(db, template_id)


async def delete_template(db: AsyncSession, actor_id: int, template_id: int) -> None:
    await _get_template(db, template_id)
    await db.execute(text("DELETE FROM admin_sms_template WHERE id = :id"), {"id": template_id})
    await _audit(db, actor_id, "sms_template.delete", "admin_sms_template", template_id)
    await db.commit()


# ─────────────────────────── 群发 ───────────────────────────


def _render(content: str, variables: dict[str, str]) -> str:
    rendered = content
    for key, value in variables.items():
        rendered = rendered.replace("{" + key + "}", str(value))
    return rendered


async def _resolve_targets(
    db: AsyncSession, user_ids: list[int], phones: list[str]
) -> list[dict[str, Any]]:
    """把会员与额外手机号合并成目标列表；会员手机号从库内读取。"""
    targets: list[dict[str, Any]] = []
    if user_ids:
        placeholders = ", ".join(f":uid_{i}" for i in range(len(user_ids)))
        params = {f"uid_{i}": value for i, value in enumerate(user_ids)}
        rows = (
            await db.execute(
                text(f"SELECT id, phone FROM users WHERE id IN ({placeholders})"), params
            )
        ).mappings().all()
        found = {int(row["id"]): row["phone"] for row in rows}
        for user_id in user_ids:
            phone = found.get(user_id)
            if not phone:
                targets.append({"user_id": user_id, "phone": None, "skip": "会员不存在或未登记手机号"})
                continue
            targets.append({"user_id": user_id, "phone": phone})
    for phone in phones:
        normalized = (phone or "").strip()
        if not normalized:
            continue
        targets.append({"user_id": None, "phone": normalized})
    return targets


async def send_sms(db: AsyncSession, actor_id: int, body: SmsSendRequest) -> SmsSendResult:
    # 供应商前置校验：未配置时整批 503，不写任何记录。
    ensure_sms_template_configured()
    provider = get_sms_template_provider()

    template: SmsTemplateItem | None = None
    if body.template_id is not None:
        template = await _get_template(db, body.template_id)
        if template.status != 1:
            raise HTTPException(409, detail="模板已停用，不能群发")
        template_content = template.content
    else:
        template_content = body.content or ""

    targets = await _resolve_targets(db, body.user_ids, body.phones)
    batch_no = uuid.uuid4().hex[:24]
    sent = failed = 0
    skipped: list[dict] = []
    for target in targets:
        phone = target.get("phone")
        if not phone:
            skipped.append({"target": target.get("user_id"), "reason": target["skip"]})
            continue
        content = _render(template_content, body.variables)
        result = await db.execute(
            text(
                "INSERT INTO admin_sms_message (batch_no, template_id, phone, user_id, content, status)"
                " VALUES (:batch_no, :template_id, :phone, :user_id, :content, 'queued')"
            ),
            {
                "batch_no": batch_no,
                "template_id": template.id if template else None,
                "phone": phone,
                "user_id": target.get("user_id"),
                "content": content,
            },
        )
        message_id = int(result.lastrowid)
        try:
            provider_ref = await provider.send_template(
                phone, template.content if template else content, body.variables
            )
        except HTTPException as exc:
            if exc.status_code == 503:
                # 供应商在发送阶段才发现不可用：回滚整批，避免留下 queued 孤儿记录。
                await db.rollback()
                raise
            failed += 1
            await db.execute(
                text(
                    "UPDATE admin_sms_message SET status = 'failed', error = :error WHERE id = :id"
                ),
                {"error": str(exc.detail)[:500], "id": message_id},
            )
            continue
        except Exception as exc:  # 供应商异常只影响该条
            failed += 1
            await db.execute(
                text("UPDATE admin_sms_message SET status = 'failed', error = :error WHERE id = :id"),
                {"error": str(exc)[:500], "id": message_id},
            )
            continue
        sent += 1
        await db.execute(
            text(
                "UPDATE admin_sms_message SET status = 'sent', provider_ref = :ref,"
                " sent_at = UTC_TIMESTAMP() WHERE id = :id"
            ),
            {"ref": provider_ref, "id": message_id},
        )
    # admin_sms_statistics 是**增量账本**：管理端首页（admin_home.sms_statistics）
    # 对本表三列做 SUM，因此这里每次发送只累加成功/失败增量；remaining_count
    # 由充值/账本任务写入，发送本身不改变余量，写 0 才是正确增量。
    await db.execute(
        text(
            "INSERT INTO admin_sms_statistics (success_count, failed_count, remaining_count)"
            " VALUES (:sent, :failed, 0)"
        ),
        {"sent": sent, "failed": failed},
    )
    await _audit(db, actor_id, "sms.send", "admin_sms_message", None, batch_no)
    await db.commit()
    return SmsSendResult(
        batch_no=batch_no, total=len(targets), sent=sent, failed=failed, skipped=skipped
    )


async def list_records(
    db: AsyncSession,
    page: int,
    page_size: int,
    keyword: str | None,
    template_id: int | None,
    status: str | None,
    start_date: str | None,
    end_date: str | None,
) -> SmsRecordPage:
    where: list[str] = []
    params: dict[str, Any] = {}
    if keyword:
        # 记录列表按手机号片段匹配；回显一律走 mask_phone，不落完整号码。
        where.append("m.phone LIKE :keyword")
        params["keyword"] = f"%{keyword}%"
    if template_id is not None:
        where.append("m.template_id = :template_id")
        params["template_id"] = template_id
    if status in {"queued", "sent", "failed"}:
        where.append("m.status = :status")
        params["status"] = status
    if start_date:
        where.append("m.created_at >= :start_date")
        params["start_date"] = start_date
    if end_date:
        where.append("m.created_at < DATE_ADD(:end_date, INTERVAL 1 DAY)")
        params["end_date"] = end_date
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    total = int(
        (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM admin_sms_message m"
                    " LEFT JOIN admin_sms_template t ON t.id = m.template_id " + where_sql
                ),
                params,
            )
        ).scalar()
        or 0
    )
    params["limit"] = page_size
    params["offset"] = (page - 1) * page_size
    rows = (
        await db.execute(
            text(f"{_RECORD_SELECT} {where_sql} ORDER BY m.id DESC LIMIT :limit OFFSET :offset"), params
        )
    ).mappings().all()
    items = []
    for row in rows:
        data = dict(row)
        data["phone"] = mask_phone(data.get("phone")) or ""
        items.append(SmsRecordItem(**data))
    return SmsRecordPage(
        items=items, page=page, page_size=page_size, total=total, has_more=page * page_size < total
    )


async def get_quota(db: AsyncSession, provider_name: str) -> SmsQuota:
    """与首页卡片同口径：对增量账本求和，避免两处数字不一致。"""
    row = (
        await db.execute(
            text(
                "SELECT COALESCE(SUM(remaining_count), 0) AS remaining_count,"
                " COALESCE(SUM(success_count), 0) AS success_count,"
                " COALESCE(SUM(failed_count), 0) AS failed_count,"
                " MAX(created_at) AS created_at"
                " FROM admin_sms_statistics WHERE tenant_id = 1"
            )
        )
    ).mappings().first()
    if not row or row["created_at"] is None:
        return SmsQuota(provider=provider_name)
    return SmsQuota(
        provider=provider_name,
        remaining_count=int(row["remaining_count"] or 0),
        success_count=int(row["success_count"] or 0),
        failed_count=int(row["failed_count"] or 0),
        updated_at=row["created_at"],
    )


async def _audit(
    db: AsyncSession, actor_id: int, action: str, resource_type: str, resource_id: int | None,
    reason: str | None = None,
) -> None:
    await db.execute(
        text(
            "INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id, reason)"
            " VALUES (:actor, :action, :resource_type, :resource_id, :reason)"
        ),
        {
            "actor": actor_id,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "reason": reason,
        },
    )

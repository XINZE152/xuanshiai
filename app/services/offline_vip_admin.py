"""线下VIP会员服务：列表 / 统计 / 新增 / 编辑 / 详情 / 成功约见修改记录。

对应前端页面：会员CRM → 线下VIP（/love-user-vip-underline）。
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.offline_vip_admin import (
    CONTRACT_STATUS_LABELS,
    PROGRESS_LABELS,
    OfflineVipCreate,
    OfflineVipItem,
    OfflineVipMeetLogItem,
    OfflineVipMeetLogPage,
    OfflineVipOption,
    OfflineVipOptions,
    OfflineVipPage,
    OfflineVipStatistics,
    OfflineVipUpdate,
)

# 列表公共投影：会员 + 三名红娘 + 最近跟进时间
_SELECT_BODY = """
SELECT v.id, v.user_id, u.nickname, u.avatar, u.phone,
       v.sign_date, v.package_name, v.progress, v.service_start, v.service_end,
       v.contract_amount, v.contract_status, v.contract_no,
       v.promise_meet_count, v.success_meet_count, v.remark, v.attach_urls,
       v.created_at, v.updated_at,
       v.sales_matchmaker_id, su.nickname AS sales_matchmaker_name,
       v.service_matchmaker_id, cu.nickname AS service_matchmaker_name,
       v.promoter_id, pu.nickname AS promoter_name,
       (SELECT MAX(f.created_at) FROM member_follow_up f WHERE f.user_id = v.user_id) AS last_follow_at
FROM offline_vip v
JOIN users u ON u.id = v.user_id
LEFT JOIN users su ON su.id = v.sales_matchmaker_id
LEFT JOIN users cu ON cu.id = v.service_matchmaker_id
LEFT JOIN users pu ON pu.id = v.promoter_id
"""

# 计数用（不需要展示列，避免多算 join）
_COUNT_BODY = "FROM offline_vip v JOIN users u ON u.id = v.user_id"

# 筛选关键字匹配的会员实名来源
_KEYWORD_SQL = (
    "(u.nickname LIKE CONCAT('%', :keyword, '%')"
    " OR u.phone LIKE CONCAT('%', :keyword, '%')"
    " OR CONCAT('G', LPAD(u.id, 6, '0')) LIKE CONCAT('%', :keyword, '%')"
    " OR EXISTS (SELECT 1 FROM user_auth au WHERE au.user_id = u.id"
    "            AND au.real_name LIKE CONCAT('%', :keyword, '%')))"
)

# 套餐类型默认兜底选项（库中尚无历史套餐时展示）
_DEFAULT_PACKAGES = ["基础服务套餐", "标准服务套餐", "尊享服务套餐", "私人定制套餐"]


def _int(value: Any) -> int:
    return int(value or 0)


def _money(value: Any) -> Decimal:
    return Decimal(str(value if value is not None else 0)).quantize(Decimal("0.01"))


def _progress_label(progress: str | None) -> str:
    return PROGRESS_LABELS.get(str(progress or "matching"), "匹配推荐中")


def _contract_label(status: str | None) -> str:
    return CONTRACT_STATUS_LABELS.get(str(status or "none"), "未发起")


def _attach_urls(value: Any) -> list[str]:
    """attach_urls 为 JSON 列：驱动可能返回 list，也可能返回 str。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if item]
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "ignore")
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except ValueError:
            return [stripped]
        if isinstance(parsed, list):
            return [str(item) for item in parsed if item]
        return [str(parsed)]
    return []


def _row(payload: dict[str, Any]) -> OfflineVipItem:
    progress = str(payload.get("progress") or "matching")
    contract_status = str(payload.get("contract_status") or "none")
    return OfflineVipItem(
        id=int(payload["id"]),
        user_id=int(payload["user_id"]),
        member_code=f"G{int(payload['user_id']):06d}",
        nickname=payload.get("nickname"),
        avatar=payload.get("avatar"),
        phone=payload.get("phone"),
        sign_date=payload.get("sign_date"),
        package_name=payload.get("package_name"),
        progress=progress,
        progress_label=_progress_label(progress),
        service_start=payload.get("service_start"),
        service_end=payload.get("service_end"),
        last_follow_at=payload.get("last_follow_at"),
        contract_amount=_money(payload.get("contract_amount")),
        sales_matchmaker_id=payload.get("sales_matchmaker_id"),
        sales_matchmaker_name=payload.get("sales_matchmaker_name"),
        service_matchmaker_id=payload.get("service_matchmaker_id"),
        service_matchmaker_name=payload.get("service_matchmaker_name"),
        promoter_id=payload.get("promoter_id"),
        promoter_name=payload.get("promoter_name"),
        contract_status=contract_status,
        contract_status_label=_contract_label(contract_status),
        contract_no=payload.get("contract_no"),
        promise_meet_count=_int(payload.get("promise_meet_count")),
        success_meet_count=_int(payload.get("success_meet_count")),
        remark=payload.get("remark"),
        attach_urls=_attach_urls(payload.get("attach_urls")),
        created_at=payload.get("created_at"),
        updated_at=payload.get("updated_at"),
    )


async def list_vips(
    db: AsyncSession,
    page: int,
    page_size: int,
    progress: str | None = None,
    sales_matchmaker_id: int | None = None,
    service_matchmaker_id: int | None = None,
    promoter_id: int | None = None,
    sign_start: str | None = None,
    sign_end: str | None = None,
    keyword: str | None = None,
) -> OfflineVipPage:
    """线下VIP 分页列表。progress 为空表示「全部」。"""
    conditions = ["v.deleted_at IS NULL"]
    params: dict[str, Any] = {"limit": page_size, "offset": (page - 1) * page_size}
    if progress:
        conditions.append("v.progress = :progress")
        params["progress"] = progress
    if sales_matchmaker_id:
        conditions.append("v.sales_matchmaker_id = :sales_matchmaker_id")
        params["sales_matchmaker_id"] = sales_matchmaker_id
    if service_matchmaker_id:
        conditions.append("v.service_matchmaker_id = :service_matchmaker_id")
        params["service_matchmaker_id"] = service_matchmaker_id
    if promoter_id:
        conditions.append("v.promoter_id = :promoter_id")
        params["promoter_id"] = promoter_id
    if sign_start:
        conditions.append("v.sign_date >= :sign_start")
        params["sign_start"] = sign_start
    if sign_end:
        conditions.append("v.sign_date <= :sign_end")
        params["sign_end"] = sign_end
    if keyword:
        conditions.append(_KEYWORD_SQL)
        params["keyword"] = keyword
    where = " AND ".join(conditions)
    rows = await db.execute(
        text(f"{_SELECT_BODY} WHERE {where} ORDER BY v.id DESC LIMIT :limit OFFSET :offset"),
        params,
    )
    count_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}
    total = _int(
        (await db.scalar(text(f"SELECT COUNT(*) {_COUNT_BODY} WHERE {where}"), count_params))
    )
    return OfflineVipPage(
        items=[_row(dict(row)) for row in rows.mappings().all()],
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
    )


async def statistics(db: AsyncSession) -> OfflineVipStatistics:
    """线下VIP 顶部 10 张统计卡。

    口径（详见 docs/api/offline-vip.md）：
    - 服务中：未过期且进度不属于「恋爱分手/已经领证」；
    - 即将到期：service_end 落在今天起 30 天内；
    - 已退费：offline_vip 无退费字段，无数据源，恒为 0。
    """
    row = (
        await db.execute(
            text(
                """
                SELECT
                    COUNT(*) AS vip_count,
                    COALESCE(SUM(v.service_end IS NOT NULL AND v.service_end >= CURDATE()
                        AND v.progress NOT IN ('breakup','married')), 0) AS serving_count,
                    COALESCE(SUM(v.service_end IS NOT NULL
                        AND v.service_end >= CURDATE()
                        AND v.service_end <= DATE_ADD(CURDATE(), INTERVAL 30 DAY)), 0) AS expiring_count,
                    COALESCE(SUM(v.service_end IS NOT NULL AND v.service_end < CURDATE()), 0) AS expired_count,
                    COALESCE(SUM(v.contract_amount > 0), 0) AS paid_count,
                    COALESCE(SUM(v.promise_meet_count), 0) AS promise_meet_total,
                    COALESCE(SUM(CASE WHEN DATE_FORMAT(v.created_at, '%Y-%m')
                        = DATE_FORMAT(CURDATE(), '%Y-%m') THEN v.promise_meet_count ELSE 0 END), 0)
                        AS promise_meet_month,
                    COALESCE(SUM(v.progress IN ('paused','breakup')), 0) AS refund_risk_count
                FROM offline_vip v
                WHERE v.deleted_at IS NULL
                """
            )
        )
    ).mappings().one()
    store_count = _int(
        await db.scalar(
            text("SELECT COUNT(*) FROM organization WHERE org_type = 'store' AND status = 1")
        )
    )
    return OfflineVipStatistics(
        store_count=store_count,
        vip_count=_int(row["vip_count"]),
        serving_count=_int(row["serving_count"]),
        expiring_count=_int(row["expiring_count"]),
        expired_count=_int(row["expired_count"]),
        paid_count=_int(row["paid_count"]),
        promise_meet_total=_int(row["promise_meet_total"]),
        promise_meet_month=_int(row["promise_meet_month"]),
        refund_risk_count=_int(row["refund_risk_count"]),
        # offline_vip 无退费字段，无数据源
        refunded_count=0,
    )


async def options(db: AsyncSession) -> OfflineVipOptions:
    """筛选/抽屉下拉选项：服务红娘、销售红娘、推广红娘、套餐类型。"""
    staff_rows = (
        await db.execute(
            text(
                "SELECT u.id, u.nickname, p.role_tag FROM users u"
                " JOIN matchmaker_profile p ON p.user_id = u.id"
                " WHERE p.deleted_at IS NULL AND u.status = 1"
                " ORDER BY p.sort DESC, u.id ASC"
            )
        )
    ).mappings().all()
    staff = [
        OfflineVipOption(
            id=int(row["id"]),
            name=str(row["nickname"] or f"红娘#{int(row['id'])}"),
            extra="超级红娘" if str(row.get("role_tag") or "") == "super" else "普通红娘",
        )
        for row in staff_rows
    ]
    promoter_rows = (
        await db.execute(
            text(
                "SELECT u.id, COALESCE(a.real_name, u.nickname) AS name FROM users u"
                " JOIN user_matchmaker_apply a ON a.user_id = u.id"
                "   AND a.application_type = 'promoter'"
                " WHERE a.status = 1 ORDER BY u.id DESC"
            )
        )
    ).mappings().all()
    promoters = [
        OfflineVipOption(id=int(row["id"]), name=str(row["name"] or f"推广红娘#{int(row['id'])}"))
        for row in promoter_rows
    ]
    package_rows = (
        await db.execute(
            text(
                "SELECT DISTINCT package_name FROM offline_vip"
                " WHERE package_name IS NOT NULL AND package_name <> '' AND deleted_at IS NULL"
                " ORDER BY package_name"
            )
        )
    ).scalars().all()
    packages = [str(item) for item in package_rows if item]
    for fallback in _DEFAULT_PACKAGES:
        if fallback not in packages:
            packages.append(fallback)
    return OfflineVipOptions(
        sales_matchmakers=staff, service_matchmakers=staff, promoters=promoters, packages=packages
    )


async def _resolve_member(db: AsyncSession, body: OfflineVipCreate) -> int:
    """解析「会员账号」：优先 user_id，其次按 lookup 模糊搜索（昵称/手机/姓名/编号）。"""
    if body.user_id:
        found = await db.scalar(
            text("SELECT id FROM users WHERE id = :id AND status = 1"), {"id": body.user_id}
        )
        if not found:
            raise HTTPException(404, detail="会员不存在或已停用")
        return int(found)
    if body.lookup:
        keyword = body.lookup.strip()
        column = "u.phone" if body.lookup_by == "phone" else "u.nickname"
        if body.lookup_by == "nickname":
            # 昵称/姓名/编号都按包含匹配
            condition = (
                "(u.nickname LIKE CONCAT('%', :keyword, '%')"
                " OR CONCAT('G', LPAD(u.id, 6, '0')) LIKE CONCAT('%', :keyword, '%')"
                " OR EXISTS (SELECT 1 FROM user_auth au WHERE au.user_id = u.id"
                "            AND au.real_name LIKE CONCAT('%', :keyword, '%')))"
            )
        else:
            condition = f"{column} = :keyword"
        found = await db.scalar(
            text(f"SELECT u.id FROM users u WHERE u.status = 1 AND {condition} ORDER BY u.id DESC LIMIT 1"),
            {"keyword": keyword},
        )
        if not found:
            raise HTTPException(404, detail="未找到匹配的会员，请核对昵称/手机/姓名/编号")
        return int(found)
    raise HTTPException(400, detail="请填写会员账号（昵称/手机/姓名/编号）")


async def create_vip(db: AsyncSession, account_id: int, body: OfflineVipCreate) -> OfflineVipItem:
    """新增线下VIP记录。"""
    user_id = await _resolve_member(db, body)
    duplicate = await db.scalar(
        text("SELECT id FROM offline_vip WHERE user_id = :uid AND deleted_at IS NULL LIMIT 1"),
        {"uid": user_id},
    )
    if duplicate:
        raise HTTPException(409, detail="该会员已存在线下VIP记录，请直接编辑")
    result = await db.execute(
        text(
            "INSERT INTO offline_vip (user_id, sales_matchmaker_id, service_matchmaker_id, promoter_id,"
            " sign_date, service_start, service_end, package_name, contract_amount,"
            " promise_meet_count, success_meet_count, remark, attach_urls, created_by)"
            " VALUES (:user_id, :sales_matchmaker_id, :service_matchmaker_id, :promoter_id,"
            " :sign_date, :service_start, :service_end, :package_name, :contract_amount,"
            " :promise_meet_count, :success_meet_count, :remark, :attach_urls, :created_by)"
        ),
        {
            "user_id": user_id,
            "sales_matchmaker_id": body.sales_matchmaker_id,
            "service_matchmaker_id": body.service_matchmaker_id,
            "promoter_id": body.promoter_id,
            "sign_date": body.sign_date,
            "service_start": body.service_start,
            "service_end": body.service_end,
            "package_name": body.package_name,
            "contract_amount": str(body.contract_amount),
            "promise_meet_count": body.promise_meet_count,
            "success_meet_count": body.success_meet_count,
            "remark": body.remark,
            "attach_urls": json.dumps(body.attach_urls, ensure_ascii=False),
            "created_by": account_id,
        },
    )
    vip_id = int(result.lastrowid)
    await db.execute(
        text(
            "INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id)"
            " VALUES (:actor, 'offline_vip.create', 'offline_vip', :id)"
        ),
        {"actor": account_id, "id": vip_id},
    )
    await db.commit()
    return await get_vip(db, vip_id)


async def _fetch_row(db: AsyncSession, vip_id: int) -> dict[str, Any]:
    row = (
        await db.execute(
            text(f"{_SELECT_BODY} WHERE v.id = :id AND v.deleted_at IS NULL"), {"id": vip_id}
        )
    ).mappings().first()
    if not row:
        raise HTTPException(404, detail="线下VIP记录不存在")
    return dict(row)


async def get_vip(db: AsyncSession, vip_id: int) -> OfflineVipItem:
    return _row(await _fetch_row(db, vip_id))


async def update_vip(
    db: AsyncSession, vip_id: int, account_id: int, body: OfflineVipUpdate
) -> OfflineVipItem:
    """编辑线下VIP；success_meet_count 发生变化时写入人工修改记录。"""
    current = await _fetch_row(db, vip_id)
    payload = body.model_dump(exclude_unset=True)
    payload.pop("meet_change_remark", None)

    assignments: list[str] = []
    params: dict[str, Any] = {"id": vip_id}
    for field, value in payload.items():
        if field == "attach_urls":
            value = json.dumps(value or [], ensure_ascii=False)
        elif isinstance(value, Decimal):
            value = str(value)
        elif hasattr(value, "isoformat"):
            value = value.isoformat()
        assignments.append(f"{field} = :{field}")
        params[field] = value

    meet_record = None
    if body.success_meet_count is not None:
        before = _int(current.get("success_meet_count"))
        if body.success_meet_count != before:
            meet_record = (before, body.success_meet_count, body.meet_change_remark)

    if assignments:
        await db.execute(
            text(f"UPDATE offline_vip SET {', '.join(assignments)} WHERE id = :id AND deleted_at IS NULL"),
            params,
        )
    if meet_record is not None:
        before, after, remark = meet_record
        await db.execute(
            text(
                "INSERT INTO offline_vip_meet_log (vip_id, before_count, after_count, remark, changed_by)"
                " VALUES (:vip_id, :before_count, :after_count, :remark, :changed_by)"
            ),
            {
                "vip_id": vip_id,
                "before_count": before,
                "after_count": after,
                "remark": remark,
                "changed_by": account_id,
            },
        )
    await db.execute(
        text(
            "INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id)"
            " VALUES (:actor, 'offline_vip.update', 'offline_vip', :id)"
        ),
        {"actor": account_id, "id": vip_id},
    )
    await db.commit()
    return await get_vip(db, vip_id)


async def list_meet_logs(
    db: AsyncSession, vip_id: int, page: int, page_size: int
) -> OfflineVipMeetLogPage:
    """成功约见次数人工修改记录分页。"""
    await _fetch_row(db, vip_id)
    rows = await db.execute(
        text(
            "SELECT l.id, l.vip_id, l.before_count, l.after_count, l.remark,"
            " l.changed_by, l.created_at, COALESCE(acc.display_name, u.nickname) AS changed_by_name"
            " FROM offline_vip_meet_log l"
            " LEFT JOIN matchmaker_admin_account acc ON acc.id = l.changed_by"
            " LEFT JOIN users u ON u.id = l.changed_by"
            " WHERE l.vip_id = :vip_id ORDER BY l.id DESC LIMIT :limit OFFSET :offset"
        ),
        {"vip_id": vip_id, "limit": page_size, "offset": (page - 1) * page_size},
    )
    total = _int(
        await db.scalar(
            text("SELECT COUNT(*) FROM offline_vip_meet_log WHERE vip_id = :vip_id"),
            {"vip_id": vip_id},
        )
    )
    return OfflineVipMeetLogPage(
        items=[
            OfflineVipMeetLogItem(
                id=int(row["id"]),
                vip_id=int(row["vip_id"]),
                before_count=_int(row["before_count"]),
                after_count=_int(row["after_count"]),
                remark=row["remark"],
                changed_by=row["changed_by"],
                changed_by_name=row["changed_by_name"] or "admin",
                created_at=row["created_at"],
            )
            for row in rows.mappings().all()
        ],
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
    )

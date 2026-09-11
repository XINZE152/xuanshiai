"""线上行为（M3-3）管理后台服务层。

覆盖浏览 / 收藏 / 爆灯 / 赠送礼物 / 网友举报五类流水的分页查询与删除。
数据源：user_browse_history / user_favorite / user_boost / user_gift_record / user_report。
删除为物理删除并写入 business_audit_log。
"""

import json
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.member_behavior_admin import MemberBehaviorItem, MemberBehaviorPage

_CATEGORIES = ("browse", "favorite", "superlike", "gift", "report")

# 会员编号统一 N+6 位补零（发起方 G，对象方 G）
_CODE = "CONCAT('G', LPAD({col}, 6, '0'))"


def _code(col: str) -> str:
    return _CODE.format(col=col)


def _money(value: Any) -> str | None:
    """Decimal → 字符串，避免前端精度丢失。"""
    if value is None:
        return None
    return str(value)


def _pay_label(value: Any) -> str | None:
    if value is None:
        return None
    return "已支付" if int(value) == 1 else "未支付"


def _event_status_label(value: Any) -> str | None:
    if value is None:
        return None
    return {1: "正常", 2: "已过期", 3: "已取消"}.get(int(value), "正常")


def _report_status_label(value: Any) -> str | None:
    if value is None:
        return None
    return {0: "待处理", 1: "已处理", 2: "已驳回"}.get(int(value), "待处理")


def _images(raw: Any) -> list[str] | None:
    """user_report.images 为 json 列，驱动可能返回 str 或已解析对象。"""
    if not raw:
        return None
    if isinstance(raw, (list, tuple)):
        return [str(x) for x in raw]
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if isinstance(parsed, list):
        return [str(x) for x in parsed]
    return None


def _row(category: str, row: dict[str, Any]) -> MemberBehaviorItem:
    payload: dict[str, Any] = {
        "event_id": int(row["event_id"]),
        "user_id": int(row["user_id"]),
        "member_code": row.get("member_code") or _code("user_id"),
        "nickname": row.get("nickname"),
        "user_avatar": row.get("user_avatar"),
        "target_user_id": int(row["target_user_id"]) if row.get("target_user_id") is not None else None,
        "target_member_code": row.get("target_member_code"),
        "target_nickname": row.get("target_nickname"),
        "target_avatar": row.get("target_avatar"),
        "occurred_at": row.get("occurred_at"),
    }
    if category == "browse":
        payload["browse_times"] = int(row.get("browse_times") or 0)
    elif category == "superlike":
        payload.update(
            amount=_money(row.get("amount")),
            order_no=row.get("order_no"),
            pay_status=int(row["pay_status"]) if row.get("pay_status") is not None else None,
            pay_status_label=_pay_label(row.get("pay_status")),
            pay_method=row.get("pay_method"),
            event_status=int(row["event_status"]) if row.get("event_status") is not None else None,
            event_status_label=_event_status_label(row.get("event_status")),
        )
    elif category == "gift":
        payload.update(
            gift_name=row.get("gift_name"),
            gift_qty=int(row["gift_qty"]) if row.get("gift_qty") is not None else None,
            qty_unit=row.get("qty_unit"),
            point_cost=int(row["point_cost"]) if row.get("point_cost") is not None else None,
            paid_amount=_money(row.get("paid_amount")),
            reward_points=int(row["reward_points"]) if row.get("reward_points") is not None else None,
            order_no=row.get("order_no"),
            pay_status=int(row["pay_status"]) if row.get("pay_status") is not None else None,
            pay_status_label=_pay_label(row.get("pay_status")),
            pay_method=row.get("pay_method"),
        )
    elif category == "report":
        payload.update(
            submit_ip=row.get("submit_ip"),
            report_type=row.get("report_type"),
            detail=row.get("detail"),
            images=_images(row.get("images")),
            report_status=int(row["report_status"]) if row.get("report_status") is not None else None,
            report_status_label=_report_status_label(row.get("report_status")),
        )
    return MemberBehaviorItem(**payload)


def _build(category: str, search: str | None, min_times: int | None, report_status: int | None, pay_status: int | None) -> tuple[str, str, dict[str, Any]]:
    """返回 (data_sql, count_sql, params)。params 会再补 limit/offset。"""
    params: dict[str, Any] = {}
    where = ["1 = 1"]
    if search:
        # 支持「按昵称搜」与「按编号搜」（编号为 G + 6 位左补零，也兼容纯数字）
        where.append(
            "(u.nickname LIKE CONCAT('%', :search, '%') "
            "OR u.phone LIKE CONCAT('%', :search, '%') "
            "OR LPAD(u.id, 6, '0') LIKE CONCAT('%', :search, '%') "
            "OR CONCAT('G', LPAD(u.id, 6, '0')) LIKE CONCAT('%', :search, '%'))"
        )
        params["search"] = search
    clause = " AND ".join(where)

    if category == "browse":
        having = ""
        if min_times:
            having = " HAVING browse_times >= :min_times"
            params["min_times"] = min_times
        group = "GROUP BY h.user_id, h.target_user_id, u.nickname, u.avatar, t.nickname, t.avatar"
        base = (
            f"FROM user_browse_history h JOIN users u ON u.id = h.user_id "
            f"LEFT JOIN users t ON t.id = h.target_user_id WHERE {clause} {group}{having}"
        )
        data_sql = (
            f"SELECT MIN(h.id) AS event_id, h.user_id, {_code('h.user_id')} AS member_code, "
            f"u.nickname, u.avatar AS user_avatar, h.target_user_id, "
            f"{_code('h.target_user_id')} AS target_member_code, t.nickname AS target_nickname, "
            f"t.avatar AS target_avatar, COUNT(*) AS browse_times, MAX(h.created_at) AS occurred_at "
            f"{base} ORDER BY occurred_at DESC, event_id DESC LIMIT :limit OFFSET :offset"
        )
        count_sql = f"SELECT COUNT(*) FROM (SELECT h.user_id, h.target_user_id {base}) x"
        return data_sql, count_sql, params

    if category == "favorite":
        base = (
            f"FROM user_favorite f JOIN users u ON u.id = f.user_id "
            f"LEFT JOIN users t ON t.id = f.target_user_id WHERE f.type = 2 AND {clause}"
        )
        data_sql = (
            f"SELECT f.id AS event_id, f.user_id, {_code('f.user_id')} AS member_code, "
            f"u.nickname, u.avatar AS user_avatar, f.target_user_id, "
            f"{_code('f.target_user_id')} AS target_member_code, t.nickname AS target_nickname, "
            f"t.avatar AS target_avatar, f.created_at AS occurred_at "
            f"{base} ORDER BY occurred_at DESC, event_id DESC LIMIT :limit OFFSET :offset"
        )
        count_sql = f"SELECT COUNT(*) {base}"
        return data_sql, count_sql, params

    if category == "superlike":
        if pay_status is not None:
            clause = f"{clause} AND b.pay_status = :pay_status"
            params["pay_status"] = pay_status
        base = (
            f"FROM user_boost b JOIN users u ON u.id = b.user_id "
            f"LEFT JOIN users t ON t.id = b.target_user_id WHERE {clause}"
        )
        data_sql = (
            f"SELECT b.id AS event_id, b.user_id, {_code('b.user_id')} AS member_code, "
            f"u.nickname, u.avatar AS user_avatar, b.target_user_id, "
            f"{_code('b.target_user_id')} AS target_member_code, t.nickname AS target_nickname, "
            f"t.avatar AS target_avatar, b.amount, b.order_no, b.pay_status, b.pay_method, "
            f"b.status AS event_status, b.created_at AS occurred_at "
            f"{base} ORDER BY occurred_at DESC, event_id DESC LIMIT :limit OFFSET :offset"
        )
        count_sql = f"SELECT COUNT(*) {base}"
        return data_sql, count_sql, params

    if category == "gift":
        if pay_status is not None:
            clause = f"{clause} AND g.pay_status = :pay_status"
            params["pay_status"] = pay_status
        base = (
            f"FROM user_gift_record g JOIN users u ON u.id = g.user_id "
            f"LEFT JOIN users t ON t.id = g.target_user_id WHERE {clause}"
        )
        data_sql = (
            f"SELECT g.id AS event_id, g.user_id, {_code('g.user_id')} AS member_code, "
            f"u.nickname, u.avatar AS user_avatar, g.target_user_id, "
            f"{_code('g.target_user_id')} AS target_member_code, t.nickname AS target_nickname, "
            f"t.avatar AS target_avatar, g.gift_name, g.gift_qty, g.qty_unit, g.point_cost, "
            f"g.paid_amount, g.reward_points, g.order_no, g.pay_status, g.pay_method, "
            f"g.created_at AS occurred_at "
            f"{base} ORDER BY occurred_at DESC, event_id DESC LIMIT :limit OFFSET :offset"
        )
        count_sql = f"SELECT COUNT(*) {base}"
        return data_sql, count_sql, params

    # report
    if report_status is not None:
        clause = f"{clause} AND r.status = :report_status"
        params["report_status"] = report_status
    base = (
        f"FROM user_report r JOIN users u ON u.id = r.user_id "
        f"LEFT JOIN users t ON t.id = r.target_user_id WHERE {clause}"
    )
    data_sql = (
        f"SELECT r.id AS event_id, r.user_id, {_code('r.user_id')} AS member_code, "
        f"u.nickname, u.avatar AS user_avatar, r.target_user_id, "
        f"{_code('r.target_user_id')} AS target_member_code, t.nickname AS target_nickname, "
        f"t.avatar AS target_avatar, r.submit_ip, r.type AS report_type, r.`desc` AS detail, "
        f"r.images, r.status AS report_status, r.created_at AS occurred_at "
        f"{base} ORDER BY occurred_at DESC, event_id DESC LIMIT :limit OFFSET :offset"
    )
    count_sql = f"SELECT COUNT(*) {base}"
    return data_sql, count_sql, params


async def list_behavior(
    db: AsyncSession,
    page: int,
    page_size: int,
    category: str,
    search: str | None = None,
    min_times: int | None = None,
    report_status: int | None = None,
    pay_status: int | None = None,
) -> MemberBehaviorPage:
    if category not in _CATEGORIES:
        raise HTTPException(400, detail=f"不支持的行为类别：{category}")
    data_sql, count_sql, params = _build(category, search, min_times, report_status, pay_status)
    rows = await db.execute(
        text(data_sql), {**params, "limit": page_size, "offset": (page - 1) * page_size}
    )
    total = int((await db.scalar(text(count_sql), params)) or 0)
    return MemberBehaviorPage(
        items=[_row(category, dict(r)) for r in rows.mappings().all()],
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
    )


_DELETE_TABLE = {
    "superlike": "user_boost",
    "gift": "user_gift_record",
    "report": "user_report",
}


async def delete_behavior_event(db: AsyncSession, category: str, event_id: int, actor_id: int) -> dict[str, Any]:
    table = _DELETE_TABLE.get(category)
    if not table:
        raise HTTPException(400, detail=f"该类别不支持删除：{category}")
    if not await db.scalar(text(f"SELECT 1 FROM {table} WHERE id = :id"), {"id": event_id}):
        raise HTTPException(404, detail="记录不存在")
    await db.execute(text(f"DELETE FROM {table} WHERE id = :id"), {"id": event_id})
    await db.execute(
        text(
            "INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id) "
            "VALUES (:actor, :action, :rtype, :rid)"
        ),
        {"actor": actor_id, "action": f"member.behavior.{category}.delete", "rtype": table, "rid": event_id},
    )
    await db.commit()
    return {"id": event_id, "deleted": True}

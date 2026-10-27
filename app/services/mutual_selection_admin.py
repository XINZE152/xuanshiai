"""Mutual-selection (互选) activity service for the back office (M7-A)."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.mutual_selection_admin import (
    MutualActivityCreate,
    MutualActivityItem,
    MutualActivityPage,
    MutualActivityUpdate,
    MutualOption,
    MutualParticipant,
    MutualParticipantAdd,
    MutualRecordItem,
    MutualRecordPage,
)
from app.services.member_crm_filter import split_values

_BASE_URL = "https://www.xuanshi.com/subpages/mutual/index"

_STATUS_BY_TIME = """
CASE
    WHEN a.status = 4 THEN 4
    WHEN UTC_TIMESTAMP() < a.start_time THEN 1
    WHEN UTC_TIMESTAMP() <= a.end_time THEN 2
    ELSE 3
END
"""

_ACTIVITY_SELECT = f"""
SELECT a.*,
    ({_STATUS_BY_TIME}) AS computed_status,
    (SELECT COUNT(*) FROM mutual_selection_signup s JOIN users u ON u.id = s.user_id
      WHERE s.activity_id = a.id AND s.status = 1
      AND COALESCE(u.gender, 0) = 1) AS male_count,
    (SELECT COUNT(*) FROM mutual_selection_signup s JOIN users u ON u.id = s.user_id
      WHERE s.activity_id = a.id AND s.status = 1
      AND COALESCE(u.gender, 0) = 2) AS female_count,
    (SELECT COUNT(*) FROM mutual_selection_signup s
      WHERE s.activity_id = a.id AND s.status = 1) AS participant_count
FROM mutual_selection_activity a
"""


def _bool(value: Any) -> bool:
    try:
        return bool(int(value))
    except (TypeError, ValueError):
        return bool(value)


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _item(row: Any) -> MutualActivityItem:
    m = dict(row)
    return MutualActivityItem(
        id=int(m["id"]),
        title=m["title"],
        cover=m.get("cover"),
        start_time=m["start_time"],
        end_time=m["end_time"],
        pick_limit=int(m.get("pick_limit") or 0),
        virtual_signup=int(m.get("virtual_signup") or 0),
        price_male=_num(m.get("price_male")),
        price_female=_num(m.get("price_female")),
        price_vip=_num(m.get("price_vip")),
        reward_promoter=_num(m.get("reward_promoter")),
        reward_service=_num(m.get("reward_service")),
        require_realname=_bool(m.get("require_realname")),
        require_avatar=_bool(m.get("require_avatar")),
        require_three_photo=_bool(m.get("require_three_photo")),
        intro=m.get("intro"),
        share_title=m.get("share_title"),
        share_desc=m.get("share_desc"),
        share_icon=m.get("share_icon"),
        success_mode=m.get("success_mode") or "show_wechat",
        notice_html=m.get("notice_html"),
        success_notice=m.get("success_notice"),
        signup_start_time=m.get("signup_start_time"),
        signup_end_time=m.get("signup_end_time"),
        mutual_start_time=m.get("mutual_start_time"),
        mutual_end_time=m.get("mutual_end_time"),
        activity_fee=(_num(m["activity_fee"]) if m.get("activity_fee") is not None else None),
        show_wechat_direct=_bool(m.get("show_wechat_direct") if m.get("show_wechat_direct") is not None else 1),
        show_matchmaker_push=_bool(m.get("show_matchmaker_push") if m.get("show_matchmaker_push") is not None else 0),
        status=int(m.get("computed_status") or m.get("status") or 1),
        visible=_bool(m.get("visible") if m.get("visible") is not None else 1),
        sort=int(m.get("sort") or 0),
        male_count=int(m.get("male_count") or 0),
        female_count=int(m.get("female_count") or 0),
        participant_count=int(m.get("participant_count") or 0),
        created_at=m.get("created_at"),
        link_url=f"{_BASE_URL}?id={int(m['id'])}",
    )


async def _get(db: AsyncSession, activity_id: int) -> MutualActivityItem:
    row = (await db.execute(text(f"{_ACTIVITY_SELECT} WHERE a.id = :id"), {"id": activity_id})).mappings().first()
    if not row:
        raise HTTPException(404, detail="互选活动不存在")
    return _item(row)


async def list_activities(
    db: AsyncSession,
    page: int,
    page_size: int,
    keyword: str | None,
    status: int | None,
) -> MutualActivityPage:
    where = ["1=1"]
    params: dict = {"limit": page_size, "offset": (page - 1) * page_size}
    if keyword:
        where.append("a.title LIKE CONCAT('%', :kw, '%')")
        params["kw"] = keyword
    if status is not None:
        where.append(f"({_STATUS_BY_TIME}) = :status")
        params["status"] = status
    clause = " AND ".join(where)
    rows = await db.execute(
        text(f"{_ACTIVITY_SELECT} WHERE {clause} ORDER BY a.id DESC LIMIT :limit OFFSET :offset"), params
    )
    count = await db.execute(
        text(f"SELECT COUNT(*) FROM mutual_selection_activity a WHERE {clause}"),
        {k: v for k, v in params.items() if k not in ("limit", "offset")},
    )
    total = int(count.scalar() or 0)
    return MutualActivityPage(
        items=[_item(r) for r in rows.mappings().all()],
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
    )


async def create_activity(db: AsyncSession, actor_id: int, body: MutualActivityCreate) -> MutualActivityItem:
    payload = {k: (int(v) if isinstance(v, bool) else v) for k, v in body.model_dump().items()}
    payload["created_by"] = actor_id
    columns = ", ".join(payload.keys())
    placeholders = ", ".join(f":{k}" for k in payload)
    result = await db.execute(
        text(f"INSERT INTO mutual_selection_activity ({columns}) VALUES ({placeholders})"), payload
    )
    await db.commit()
    return await _get(db, int(result.lastrowid))


async def update_activity(
    db: AsyncSession, activity_id: int, body: MutualActivityUpdate
) -> MutualActivityItem:
    current = await _get(db, activity_id)
    values = {k: (int(v) if isinstance(v, bool) else v) for k, v in body.model_dump(exclude_unset=True).items()}
    # 局部更新无法在 schema 层交叉校验时间窗（另一半可能未传），这里按「合并后的值」校验，
    # 否则可把 signup_end_time 改到 signup_start_time 之前而绕过录入时的约束。
    merged = {
        "start_time": values.get("start_time", current.start_time),
        "end_time": values.get("end_time", current.end_time),
        "signup_start_time": values.get("signup_start_time", current.signup_start_time),
        "signup_end_time": values.get("signup_end_time", current.signup_end_time),
        "mutual_start_time": values.get("mutual_start_time", current.mutual_start_time),
        "mutual_end_time": values.get("mutual_end_time", current.mutual_end_time),
    }
    if merged["end_time"] <= merged["start_time"]:
        raise HTTPException(422, detail="end_time 必须晚于 start_time")
    if merged["signup_start_time"] and merged["signup_end_time"] and merged["signup_end_time"] <= merged["signup_start_time"]:
        raise HTTPException(422, detail="signup_end_time 必须晚于 signup_start_time")
    if merged["mutual_start_time"] and merged["mutual_end_time"] and merged["mutual_end_time"] <= merged["mutual_start_time"]:
        raise HTTPException(422, detail="mutual_end_time 必须晚于 mutual_start_time")
    updates = ", ".join(f"{k} = :{k}" for k in values)
    await db.execute(
        text(f"UPDATE mutual_selection_activity SET {updates}, updated_at = UTC_TIMESTAMP() WHERE id = :id"),
        {**values, "id": activity_id},
    )
    await db.commit()
    return await _get(db, activity_id)


async def delete_activity(db: AsyncSession, activity_id: int) -> None:
    await _get(db, activity_id)
    await db.execute(text("DELETE FROM mutual_selection_signup WHERE activity_id = :id"), {"id": activity_id})
    await db.execute(text("DELETE FROM mutual_selection_pick WHERE activity_id = :id"), {"id": activity_id})
    await db.execute(text("DELETE FROM mutual_selection_activity WHERE id = :id"), {"id": activity_id})
    await db.commit()


async def copy_activity(db: AsyncSession, actor_id: int, activity_id: int) -> MutualActivityItem:
    await _get(db, activity_id)
    # B2 新增列必须同步进这份硬编码列清单，否则副本会丢报名/互选时间窗与收费配置。
    await db.execute(
        text(
            "INSERT INTO mutual_selection_activity "
            "(title, cover, start_time, end_time, pick_limit, virtual_signup, price_male, price_female, "
            " price_vip, reward_promoter, reward_service, require_realname, require_avatar, require_three_photo, "
            " intro, share_title, share_desc, share_icon, success_mode, notice_html, success_notice, "
            " signup_start_time, signup_end_time, mutual_start_time, mutual_end_time, activity_fee, "
            " show_wechat_direct, show_matchmaker_push, "
            " status, visible, sort, created_by) "
            "SELECT CONCAT(title, '(副本)'), cover, start_time, end_time, pick_limit, virtual_signup, "
            " price_male, price_female, price_vip, reward_promoter, reward_service, require_realname, "
            " require_avatar, require_three_photo, intro, share_title, share_desc, share_icon, success_mode, "
            " notice_html, success_notice, signup_start_time, signup_end_time, mutual_start_time, "
            " mutual_end_time, activity_fee, show_wechat_direct, show_matchmaker_push, "
            " 1, 0, sort, :actor "
            "FROM mutual_selection_activity WHERE id = :id"
        ),
        {"id": activity_id, "actor": actor_id},
    )
    await db.commit()
    fresh = (await db.execute(text("SELECT MAX(id) AS id FROM mutual_selection_activity"))).scalar()
    return await _get(db, int(fresh))


async def set_visible(db: AsyncSession, activity_id: int, visible: bool) -> MutualActivityItem:
    await _get(db, activity_id)
    await db.execute(
        text("UPDATE mutual_selection_activity SET visible = :v, updated_at = UTC_TIMESTAMP() WHERE id = :id"),
        {"v": 1 if visible else 0, "id": activity_id},
    )
    await db.commit()
    return await _get(db, activity_id)


async def offline_activity(
    db: AsyncSession, activity_id: int, reason: str | None, actor_id: int
) -> MutualActivityItem:
    """B2：页面「下线」动作。与 PATCH /visible 同语义，但带原因审计。"""
    await _get(db, activity_id)
    await db.execute(
        text("UPDATE mutual_selection_activity SET visible = 0, updated_at = UTC_TIMESTAMP() WHERE id = :id"),
        {"id": activity_id},
    )
    await db.execute(
        text(
            "INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id, reason) "
            "VALUES (:actor, 'mutual_activity.offline', 'mutual_selection_activity', :id, :reason)"
        ),
        {"actor": actor_id, "id": activity_id, "reason": reason},
    )
    await db.commit()
    return await _get(db, activity_id)


async def list_participants(db: AsyncSession, activity_id: int) -> list[MutualParticipant]:
    await _get(db, activity_id)
    rows = await db.execute(
        text(
            "SELECT s.id AS signup_id, s.user_id, u.nickname, "
            "CASE u.gender WHEN 1 THEN '男' WHEN 2 THEN '女' END AS gender, u.avatar, s.created_at "
            "FROM mutual_selection_signup s LEFT JOIN users u ON u.id = s.user_id "
            "WHERE s.activity_id = :id AND s.status = 1 ORDER BY s.id DESC LIMIT 500"
        ),
        {"id": activity_id},
    )
    return [
        MutualParticipant(
            signup_id=int(r["signup_id"]),
            user_id=int(r["user_id"]),
            nickname=r["nickname"],
            gender=r["gender"],
            avatar=r["avatar"],
            created_at=r["created_at"],
        )
        for r in rows.mappings().all()
    ]


async def add_participant(db: AsyncSession, activity_id: int, body: MutualParticipantAdd) -> MutualParticipant:
    await _get(db, activity_id)
    user = (await db.execute(text("SELECT id, nickname, gender, avatar FROM users WHERE id = :id"), {"id": body.user_id})).mappings().first()
    if not user:
        raise HTTPException(404, detail="会员不存在")
    await db.execute(
        text(
            "INSERT INTO mutual_selection_signup (activity_id, user_id, status) VALUES (:a, :u, 1) "
            "ON DUPLICATE KEY UPDATE status = 1, updated_at = UTC_TIMESTAMP()"
        ),
        {"a": activity_id, "u": body.user_id},
    )
    await db.commit()
    return MutualParticipant(
        user_id=int(user["id"]),
        nickname=user["nickname"],
        gender={1: "男", 2: "女"}.get(user["gender"]),
        avatar=user["avatar"],
    )


async def remove_participant(db: AsyncSession, activity_id: int, user_id: int) -> None:
    result = await db.execute(
        text("UPDATE mutual_selection_signup SET status = 2, updated_at = UTC_TIMESTAMP() "
             "WHERE activity_id = :a AND user_id = :u"),
        {"a": activity_id, "u": user_id},
    )
    if result.rowcount == 0:
        raise HTTPException(404, detail="参与嘉宾不存在")
    await db.commit()


async def list_records(
    db: AsyncSession,
    page: int,
    page_size: int,
    activity_id: int | None,
    actor_id: int | None,
    keyword: str | None,
    result_filter: str | None,
    *,
    age_min: int | None = None,
    age_max: int | None = None,
    income_min: float | None = None,
    income_max: float | None = None,
    income_secret: bool | None = None,
    education: str | None = None,
) -> MutualRecordPage:
    where = ["p.action IN ('pick', 'cancel')"]
    params: dict = {"limit": page_size, "offset": (page - 1) * page_size}
    if activity_id:
        where.append("p.activity_id = :activity_id")
        params["activity_id"] = activity_id
    if actor_id:
        where.append("p.from_user_id = :actor_id")
        params["actor_id"] = actor_id
    if keyword:
        where.append("(fu.nickname LIKE CONCAT('%', :kw, '%') OR tu.nickname LIKE CONCAT('%', :kw, '%'))")
        params["kw"] = keyword
    if result_filter == "success":
        where.append("p.is_success = 1")
    elif result_filter == "fail":
        where.append("p.is_success = 0 AND p.action = 'pick'")
    elif result_filter == "none":
        where.append("p.action = 'cancel'")
    # 会员资料筛选（作用于发起方 from_user）：与会员 CRM 列表同口径——
    # 年龄取 users.birthday、收入取 user_profile.income 区间、学历取 user_auth.education 多选。
    # 用 EXISTS 子查询，不改主 FROM、不让 1:1 之外的关联放大 COUNT。
    if age_min is not None:
        where.append("fu.birthday IS NOT NULL AND TIMESTAMPDIFF(YEAR, fu.birthday, CURDATE()) >= :age_min")
        params["age_min"] = age_min
    if age_max is not None:
        where.append("fu.birthday IS NOT NULL AND TIMESTAMPDIFF(YEAR, fu.birthday, CURDATE()) <= :age_max")
        params["age_max"] = age_max
    income_conds: list[str] = []
    if income_min is not None:
        income_conds.append("fp.income >= :income_min")
        params["income_min"] = income_min
    if income_max is not None:
        income_conds.append("fp.income <= :income_max")
        params["income_max"] = income_max
    if income_conds:
        where.append(
            "EXISTS (SELECT 1 FROM user_profile fp WHERE fp.user_id = fu.id"
            f" AND {' AND '.join(income_conds)})"
        )
    if income_secret:
        # 前端「保密」档：无资料行或 income 未填写（NULL）均视为保密。
        where.append(
            "NOT EXISTS (SELECT 1 FROM user_profile fp WHERE fp.user_id = fu.id AND fp.income IS NOT NULL)"
        )
    education_values = split_values(education)
    if education_values:
        names = []
        for index, value in enumerate(education_values):
            key = f"edu_{index}"
            params[key] = value
            names.append(f":{key}")
        where.append(
            "EXISTS (SELECT 1 FROM user_auth fa WHERE fa.user_id = fu.id"
            f" AND fa.education IN ({', '.join(names)}))"
        )
    clause = " AND ".join(where)
    base = (
        "FROM mutual_selection_pick p "
        "LEFT JOIN users fu ON fu.id = p.from_user_id "
        "LEFT JOIN users tu ON tu.id = p.to_user_id "
        "LEFT JOIN mutual_selection_activity a ON a.id = p.activity_id"
    )
    rows = await db.execute(
        text(
            f"SELECT p.*, a.title AS activity_title, fu.nickname AS from_nickname, tu.nickname AS to_nickname "
            f"{base} WHERE {clause} ORDER BY p.id DESC LIMIT :limit OFFSET :offset"
        ),
        params,
    )
    count = await db.execute(
        text(f"SELECT COUNT(*) {base} WHERE {clause}"),
        {k: v for k, v in params.items() if k not in ("limit", "offset")},
    )
    total = int(count.scalar() or 0)
    action_label = {"pick": "选择心动", "cancel": "取消心动"}
    result_label = {"success": "已成功", "fail": "未成功", "none": "不选"}

    def _result(m: dict) -> str:
        if m.get("action") == "cancel":
            return "none"
        return "success" if _bool(m.get("is_success")) else "fail"

    items = []
    for r in rows.mappings().all():
        m = dict(r)
        res = _result(m)
        items.append(
            MutualRecordItem(
                id=int(m["id"]),
                activity_id=int(m["activity_id"]),
                activity_title=m.get("activity_title"),
                from_user_id=int(m["from_user_id"]),
                from_nickname=m.get("from_nickname"),
                action=m["action"],
                action_label=action_label.get(m["action"], m["action"]),
                to_user_id=int(m["to_user_id"]),
                to_nickname=m.get("to_nickname"),
                is_success=_bool(m.get("is_success")),
                result=res,
                result_label=result_label[res],
                created_at=m.get("created_at"),
            )
        )
    return MutualRecordPage(
        items=items, page=page, page_size=page_size, total=total, has_more=page * page_size < total
    )


async def record_options(db: AsyncSession) -> dict[str, list[MutualOption]]:
    activities = (
        await db.execute(
            text("SELECT id, title FROM mutual_selection_activity ORDER BY id DESC LIMIT 500")
        )
    ).all()
    actors = (
        await db.execute(
            text(
                "SELECT DISTINCT u.id, u.nickname FROM mutual_selection_pick p "
                "JOIN users u ON u.id = p.from_user_id ORDER BY u.id DESC LIMIT 500"
            )
        )
    ).all()
    return {
        "activities": [MutualOption(id=int(r[0]), label=str(r[1])) for r in activities],
        "actors": [MutualOption(id=int(r[0]), label=str(r[1] or f"会员{r[0]}")) for r in actors],
    }

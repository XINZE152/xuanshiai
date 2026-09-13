"""Short-video (短视频) service for the back office (M7-C)."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.short_video_admin import (
    RedPacketClaimItem,
    RedPacketItem,
    RedPacketPage,
    ShortVideoCreate,
    ShortVideoItem,
    ShortVideoPage,
    ShortVideoUpdate,
    VideoBrushRequest,
    VideoBrushResult,
    VideoCategoryCreate,
    VideoCategoryItem,
    VideoCategoryUpdate,
    VideoCommentItem,
    VideoCommentPage,
    VideoCommentUpdate,
    VideoHomepageItem,
    VideoHomepagePage,
    VideoTipItem,
    VideoTipPage,
)

_AUDIT_LABEL = {"pending": "待审", "approved": "通过", "rejected": "未通过"}
_VIEW_LABEL = {"login": "必须先登录", "all": "不限", "member": "仅会员"}
_LINK_LABEL = {"none": "不关联", "custom": "自定义", "member": "关联相亲资料", "activity": "关联平台活动", "home": "关联平台首页"}


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _money(value: Any) -> str:
    return format(_num(value), ".2f")


def _bool(value: Any) -> bool:
    try:
        return bool(int(value))
    except (TypeError, ValueError):
        return bool(value)


def _to_int(value: Any) -> int:
    return 1 if value else 0


# ─────────────────────────── 分类 ───────────────────────────


async def list_categories(db: AsyncSession) -> list[VideoCategoryItem]:
    rows = await db.execute(
        text(
            "SELECT c.*, (SELECT COUNT(*) FROM short_video v WHERE v.category_id = c.id AND v.deleted_at IS NULL) "
            "AS video_count FROM short_video_category c ORDER BY c.sort ASC, c.id ASC"
        )
    )
    return [
        VideoCategoryItem(
            id=int(r["id"]), name=r["name"], sort=int(r["sort"] or 0), status=int(r["status"] or 0),
            video_count=int(r["video_count"] or 0),
        )
        for r in rows.mappings().all()
    ]


async def create_category(db: AsyncSession, body: VideoCategoryCreate) -> VideoCategoryItem:
    dup = (await db.execute(text("SELECT id FROM short_video_category WHERE name = :n"), {"n": body.name})).scalar()
    if dup:
        raise HTTPException(409, detail="分类名称已存在")
    await db.execute(
        text("INSERT INTO short_video_category (name, sort, status) VALUES (:n, :s, :st)"),
        {"n": body.name, "s": body.sort, "st": body.status},
    )
    await db.commit()
    return await _get_category(db, body.name)


async def _get_category(db: AsyncSession, name: str) -> VideoCategoryItem:
    row = (
        await db.execute(
            text("SELECT c.*, 0 AS video_count FROM short_video_category c WHERE c.name = :n"), {"n": name}
        )
    ).mappings().first()
    if not row:
        raise HTTPException(404, detail="视频分类不存在")
    return VideoCategoryItem(
        id=int(row["id"]), name=row["name"], sort=int(row["sort"] or 0), status=int(row["status"] or 0),
        video_count=int(row["video_count"] or 0),
    )


async def update_category(db: AsyncSession, category_id: int, body: VideoCategoryUpdate) -> VideoCategoryItem:
    exists = (await db.execute(text("SELECT name FROM short_video_category WHERE id = :id"), {"id": category_id})).scalar()
    if not exists:
        raise HTTPException(404, detail="视频分类不存在")
    values = body.model_dump(exclude_unset=True)
    if "name" in values:
        dup = (
            await db.execute(
                text("SELECT id FROM short_video_category WHERE name = :n AND id <> :id"),
                {"n": values["name"], "id": category_id},
            )
        ).scalar()
        if dup:
            raise HTTPException(409, detail="分类名称已存在")
    updates = ", ".join(f"{k} = :{k}" for k in values)
    await db.execute(
        text(f"UPDATE short_video_category SET {updates}, updated_at = UTC_TIMESTAMP() WHERE id = :id"),
        {**values, "id": category_id},
    )
    await db.commit()
    return await _get_category(db, values.get("name", exists))


async def delete_category(db: AsyncSession, category_id: int) -> None:
    exists = (await db.execute(text("SELECT id FROM short_video_category WHERE id = :id"), {"id": category_id})).scalar()
    if not exists:
        raise HTTPException(404, detail="视频分类不存在")
    await db.execute(text("DELETE FROM short_video_category WHERE id = :id"), {"id": category_id})
    await db.commit()


# ─────────────────────────── 视频 ───────────────────────────

_VIDEO_SELECT = """
SELECT v.*, u.nickname AS publisher_nickname, c.name AS category_name
FROM short_video v
LEFT JOIN users u ON u.id = v.publisher_user_id
LEFT JOIN short_video_category c ON c.id = v.category_id
"""


def _video_item(row: Any) -> ShortVideoItem:
    m = dict(row)
    seconds = _num(m.get("duration_seconds"))
    audit = m.get("audit_status") or "pending"
    link = m.get("link_type") or "none"
    perm = m.get("view_permission") or "login"
    return ShortVideoItem(
        id=int(m["id"]),
        publisher_user_id=int(m["publisher_user_id"]),
        publisher_nickname=m.get("publisher_nickname"),
        cover=m.get("cover"),
        cover_mode=m.get("cover_mode") or "auto",
        description=m.get("description"),
        category_id=m.get("category_id"),
        category_name=m.get("category_name"),
        duration_seconds=seconds,
        duration_label=f"{seconds:.2f}s",
        video_url=m.get("video_url"),
        link_type=link,
        link_label=_LINK_LABEL.get(link, link),
        link_value=m.get("link_value"),
        view_permission=perm,
        view_permission_label=_VIEW_LABEL.get(perm, perm),
        sort=int(m.get("sort") or 0),
        virtual_views=int(m.get("virtual_views") or 0),
        comment_enabled=_bool(m.get("comment_enabled") if m.get("comment_enabled") is not None else 1),
        tip_enabled=_bool(m.get("tip_enabled") if m.get("tip_enabled") is not None else 1),
        visible=_bool(m.get("visible") if m.get("visible") is not None else 1),
        audit_status=audit,
        audit_label=_AUDIT_LABEL.get(audit, audit),
        is_top=_bool(m.get("is_top")),
        is_recommend=_bool(m.get("is_recommend")),
        is_hot=_bool(m.get("is_hot")),
        has_red_packet=_bool(m.get("has_red_packet")),
        view_count=int(m.get("view_count") or 0),
        comment_count=int(m.get("comment_count") or 0),
        like_count=int(m.get("like_count") or 0),
        tip_amount=_money(m.get("tip_amount")),
        published_at=m.get("published_at"),
        created_at=m.get("created_at"),
    )


async def _get_video(db: AsyncSession, video_id: int) -> ShortVideoItem:
    row = (
        await db.execute(text(f"{_VIDEO_SELECT} WHERE v.id = :id AND v.deleted_at IS NULL"), {"id": video_id})
    ).mappings().first()
    if not row:
        raise HTTPException(404, detail="视频不存在")
    return _video_item(row)


def _video_payload(body: ShortVideoCreate | ShortVideoUpdate, partial: bool) -> dict[str, Any]:
    data = body.model_dump(exclude_unset=partial)
    out: dict[str, Any] = {}
    for key, value in data.items():
        if key in {"comment_enabled", "tip_enabled", "visible", "is_top", "is_recommend", "is_hot", "has_red_packet"}:
            out[key] = _to_int(value)
        else:
            out[key] = value
    return out


async def list_videos(
    db: AsyncSession,
    page: int,
    page_size: int,
    audit_status: str | None,
    category_id: int | None,
    flag: str | None,
    keyword: str | None,
    order_by: str | None,
) -> ShortVideoPage:
    where = ["v.deleted_at IS NULL"]
    params: dict = {"limit": page_size, "offset": (page - 1) * page_size}
    if audit_status:
        where.append("v.audit_status = :audit_status")
        params["audit_status"] = audit_status
    if category_id:
        where.append("v.category_id = :category_id")
        params["category_id"] = category_id
    if flag == "top":
        where.append("v.is_top = 1")
    elif flag == "recommend":
        where.append("v.is_recommend = 1")
    elif flag == "hot":
        where.append("v.is_hot = 1")
    if keyword:
        where.append("v.description LIKE CONCAT('%', :kw, '%')")
        params["kw"] = keyword
    clause = " AND ".join(where)
    order = "v.is_top DESC, v.sort DESC, v.id DESC"
    if order_by == "publish":
        order = "v.published_at DESC, v.id DESC"
    elif order_by == "views":
        order = "v.view_count DESC, v.id DESC"
    rows = await db.execute(
        text(f"{_VIDEO_SELECT} WHERE {clause} ORDER BY {order} LIMIT :limit OFFSET :offset"), params
    )
    count = await db.execute(
        text(f"SELECT COUNT(*) FROM short_video v WHERE {clause}"),
        {k: v for k, v in params.items() if k not in ("limit", "offset")},
    )
    total = int(count.scalar() or 0)
    return ShortVideoPage(
        items=[_video_item(r) for r in rows.mappings().all()],
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
    )


async def create_video(db: AsyncSession, body: ShortVideoCreate) -> ShortVideoItem:
    payload = _video_payload(body, partial=False)
    columns = ", ".join(payload.keys())
    placeholders = ", ".join(f":{k}" for k in payload)
    result = await db.execute(text(f"INSERT INTO short_video ({columns}) VALUES ({placeholders})"), payload)
    await db.commit()
    return await _get_video(db, int(result.lastrowid))


async def update_video(db: AsyncSession, video_id: int, body: ShortVideoUpdate) -> ShortVideoItem:
    await _get_video(db, video_id)
    values = _video_payload(body, partial=True)
    updates = ", ".join(f"{k} = :{k}" for k in values)
    await db.execute(
        text(f"UPDATE short_video SET {updates}, updated_at = UTC_TIMESTAMP() WHERE id = :id"),
        {**values, "id": video_id},
    )
    await db.commit()
    return await _get_video(db, video_id)


async def delete_video(db: AsyncSession, video_id: int) -> None:
    await _get_video(db, video_id)
    await db.execute(
        text("UPDATE short_video SET deleted_at = UTC_TIMESTAMP() WHERE id = :id"), {"id": video_id}
    )
    await db.commit()


async def brush_videos(db: AsyncSession, body: VideoBrushRequest) -> VideoBrushResult:
    span = body.max_value - body.min_value + 1
    if body.brush_type == "views":
        result = await db.execute(
            text("UPDATE short_video SET view_count = view_count + FLOOR(:minv + RAND() * :span) "
                 "WHERE deleted_at IS NULL AND comment_enabled IS NOT NULL"),
            {"minv": body.min_value, "span": span},
        )
    elif body.brush_type == "likes":
        result = await db.execute(
            text("UPDATE short_video SET like_count = like_count + FLOOR(:minv + RAND() * :span) "
                 "WHERE deleted_at IS NULL AND comment_enabled IS NOT NULL"),
            {"minv": body.min_value, "span": span},
        )
    else:  # publish_time：把 ID 区间内视频的发布时间刷新为当前时间
        result = await db.execute(
            text("UPDATE short_video SET published_at = UTC_TIMESTAMP() WHERE id BETWEEN :a AND :b"),
            {"a": body.min_value, "b": body.max_value},
        )
    await db.commit()
    return VideoBrushResult(brush_type=body.brush_type, affected=int(result.rowcount or 0))


# ─────────────────────────── 评论 ───────────────────────────


def _comment_item(row: Any) -> VideoCommentItem:
    m = dict(row)
    return VideoCommentItem(
        id=int(m["id"]),
        video_id=int(m["video_id"]),
        video_description=m.get("video_description"),
        user_id=int(m["user_id"]),
        nickname=m.get("nickname"),
        content=m["content"],
        like_count=int(m.get("like_count") or 0),
        ip=m.get("ip"),
        audit_status=m.get("audit_status") or "pending",
        created_at=m.get("created_at"),
    )


async def list_comments(
    db: AsyncSession, page: int, page_size: int, audit_status: str | None, keyword: str | None
) -> VideoCommentPage:
    where = ["1=1"]
    params: dict = {"limit": page_size, "offset": (page - 1) * page_size}
    if audit_status:
        where.append("c.audit_status = :audit_status")
        params["audit_status"] = audit_status
    if keyword:
        where.append("c.content LIKE CONCAT('%', :kw, '%')")
        params["kw"] = keyword
    clause = " AND ".join(where)
    base = (
        "FROM short_video_comment c LEFT JOIN users u ON u.id = c.user_id "
        "LEFT JOIN short_video v ON v.id = c.video_id"
    )
    rows = await db.execute(
        text(f"SELECT c.*, u.nickname, v.description AS video_description {base} "
             f"WHERE {clause} ORDER BY c.id DESC LIMIT :limit OFFSET :offset"),
        params,
    )
    count = await db.execute(
        text(f"SELECT COUNT(*) {base} WHERE {clause}"),
        {k: v for k, v in params.items() if k not in ("limit", "offset")},
    )
    total = int(count.scalar() or 0)
    return VideoCommentPage(
        items=[_comment_item(r) for r in rows.mappings().all()],
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
    )


async def update_comment(db: AsyncSession, comment_id: int, body: VideoCommentUpdate) -> VideoCommentItem:
    values = body.model_dump(exclude_unset=True)
    updates = ", ".join(f"{k} = :{k}" for k in values)
    result = await db.execute(
        text(f"UPDATE short_video_comment SET {updates}, updated_at = UTC_TIMESTAMP() WHERE id = :id"),
        {**values, "id": comment_id},
    )
    if result.rowcount == 0:
        raise HTTPException(404, detail="评论不存在")
    await db.commit()
    row = (
        await db.execute(
            text("SELECT c.*, u.nickname, v.description AS video_description FROM short_video_comment c "
                 "LEFT JOIN users u ON u.id = c.user_id LEFT JOIN short_video v ON v.id = c.video_id "
                 "WHERE c.id = :id"),
            {"id": comment_id},
        )
    ).mappings().one()
    return _comment_item(row)


async def delete_comment(db: AsyncSession, comment_id: int) -> None:
    result = await db.execute(text("DELETE FROM short_video_comment WHERE id = :id"), {"id": comment_id})
    if result.rowcount == 0:
        raise HTTPException(404, detail="评论不存在")
    await db.execute(
        text("UPDATE short_video v SET v.comment_count = "
             "(SELECT COUNT(*) FROM short_video_comment c WHERE c.video_id = v.id AND c.audit_status = 'approved') "
             "WHERE v.id = (SELECT video_id FROM short_video_comment WHERE id = :id LIMIT 1)"),
        {"id": comment_id},
    )
    await db.commit()


async def batch_delete_comments(db: AsyncSession, ids: list[int]) -> int:
    placeholders = ", ".join(f":id{i}" for i in range(len(ids)))
    params = {f"id{i}": v for i, v in enumerate(ids)}
    result = await db.execute(text(f"DELETE FROM short_video_comment WHERE id IN ({placeholders})"), params)
    await db.commit()
    return int(result.rowcount or 0)


# ─────────────────────────── 打赏 ───────────────────────────

_TIP_SELECT = """
SELECT t.*, tu.nickname AS tipper_nickname, ru.nickname AS receiver_nickname,
       v.description AS video_description
FROM short_video_tip t
LEFT JOIN users tu ON tu.id = t.tipper_user_id
LEFT JOIN users ru ON ru.id = t.receiver_user_id
LEFT JOIN short_video v ON v.id = t.video_id
"""


def _tip_item(row: Any) -> VideoTipItem:
    m = dict(row)
    form = m.get("tip_form") or "cash"
    return VideoTipItem(
        id=int(m["id"]),
        video_id=int(m["video_id"]),
        video_description=m.get("video_description"),
        tipper_user_id=int(m["tipper_user_id"]),
        tipper_nickname=m.get("tipper_nickname"),
        receiver_user_id=int(m["receiver_user_id"]),
        receiver_nickname=m.get("receiver_nickname"),
        message=m.get("message"),
        tip_form=form,
        tip_form_label="礼物" if form == "gift" else "现金",
        amount=_money(m.get("amount")),
        pay_method=m.get("pay_method"),
        order_no=m.get("order_no"),
        status=m.get("status") or "paid",
        created_at=m.get("created_at"),
    )


async def list_tips(
    db: AsyncSession, page: int, page_size: int, keyword: str | None, search_by: str | None
) -> VideoTipPage:
    where = ["1=1"]
    params: dict = {"limit": page_size, "offset": (page - 1) * page_size}
    if keyword:
        if search_by == "video":
            where.append("v.description LIKE CONCAT('%', :kw, '%')")
        else:
            where.append("(tu.nickname LIKE CONCAT('%', :kw, '%') OR ru.nickname LIKE CONCAT('%', :kw, '%'))")
        params["kw"] = keyword
    clause = " AND ".join(where)
    rows = await db.execute(
        text(f"{_TIP_SELECT} WHERE {clause} ORDER BY t.id DESC LIMIT :limit OFFSET :offset"), params
    )
    count = await db.execute(
        text("SELECT COUNT(*), COALESCE(SUM(t.amount), 0) FROM short_video_tip t "
             "LEFT JOIN short_video v ON v.id = t.video_id "
             "LEFT JOIN users tu ON tu.id = t.tipper_user_id "
             "LEFT JOIN users ru ON ru.id = t.receiver_user_id "
             f"WHERE {clause}"),
        {k: v for k, v in params.items() if k not in ("limit", "offset")},
    )
    row = count.first()
    total = int(row[0] or 0) if row else 0
    total_amount = _money(row[1] if row else 0)
    return VideoTipPage(
        items=[_tip_item(r) for r in rows.mappings().all()],
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
        total_amount=total_amount,
    )


# ─────────────────────────── 红包 ───────────────────────────

_PACKET_SELECT = """
SELECT p.*, v.description AS video_description
FROM video_red_packet p
LEFT JOIN short_video v ON v.id = p.video_id
"""


def _packet_item(row: Any) -> RedPacketItem:
    m = dict(row)
    remain_parts = int(m.get("remain_parts") or 0)
    return RedPacketItem(
        id=int(m["id"]),
        video_id=int(m["video_id"]),
        video_description=m.get("video_description"),
        sender_label=m.get("sender_label") or "后台发放",
        amount=_money(m.get("amount")),
        total_parts=int(m.get("total_parts") or 1),
        is_equal=_bool(m.get("is_equal")),
        remain_parts=remain_parts,
        remain_amount=_money(m.get("remain_amount")),
        pay_status=m.get("pay_status") or "unpaid",
        claim_status="unfinished" if remain_parts > 0 else "finished",
        created_at=m.get("created_at"),
    )


async def list_packets(
    db: AsyncSession, page: int, page_size: int, claim_status: str | None, keyword: str | None
) -> RedPacketPage:
    where = ["1=1"]
    params: dict = {"limit": page_size, "offset": (page - 1) * page_size}
    if claim_status == "finished":
        where.append("p.remain_parts = 0")
    elif claim_status == "unfinished":
        where.append("p.remain_parts > 0")
    if keyword:
        where.append("v.description LIKE CONCAT('%', :kw, '%')")
        params["kw"] = keyword
    clause = " AND ".join(where)
    rows = await db.execute(
        text(f"{_PACKET_SELECT} WHERE {clause} ORDER BY p.id DESC LIMIT :limit OFFSET :offset"), params
    )
    count = await db.execute(
        text("SELECT COUNT(*) FROM video_red_packet p LEFT JOIN short_video v ON v.id = p.video_id "
             f"WHERE {clause}"),
        {k: v for k, v in params.items() if k not in ("limit", "offset")},
    )
    total = int(count.scalar() or 0)
    return RedPacketPage(
        items=[_packet_item(r) for r in rows.mappings().all()],
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
    )


async def list_packet_claims(db: AsyncSession, packet_id: int) -> list[RedPacketClaimItem]:
    exists = (await db.execute(text("SELECT id FROM video_red_packet WHERE id = :id"), {"id": packet_id})).scalar()
    if not exists:
        raise HTTPException(404, detail="红包不存在")
    rows = await db.execute(
        text(
            "SELECT c.*, u.nickname FROM video_red_packet_claim c LEFT JOIN users u ON u.id = c.user_id "
            "WHERE c.packet_id = :id ORDER BY c.id DESC LIMIT 1000"
        ),
        {"id": packet_id},
    )
    return [
        RedPacketClaimItem(
            id=int(r["id"]), packet_id=int(r["packet_id"]), user_id=int(r["user_id"]),
            nickname=r["nickname"], amount=_money(r["amount"]), created_at=r["created_at"],
        )
        for r in rows.mappings().all()
    ]


# ─────────────────────────── 会员主页 ───────────────────────────


async def list_homepages(db: AsyncSession, page: int, page_size: int, keyword: str | None) -> VideoHomepagePage:
    where = ["h.deleted_at IS NULL"]
    params: dict = {"limit": page_size, "offset": (page - 1) * page_size}
    if keyword:
        where.append("u.nickname LIKE CONCAT('%', :kw, '%')")
        params["kw"] = keyword
    clause = " AND ".join(where)
    base = "FROM short_video_homepage h LEFT JOIN users u ON u.id = h.user_id"
    rows = await db.execute(
        text(
            "SELECT h.*, u.nickname, u.is_real_name, u.created_at AS user_created_at, "
            "(SELECT COUNT(*) FROM short_video v WHERE v.publisher_user_id = h.user_id AND v.deleted_at IS NULL) AS video_count, "
            "(SELECT COALESCE(SUM(v.view_count), 0) FROM short_video v WHERE v.publisher_user_id = h.user_id AND v.deleted_at IS NULL) AS view_count, "
            "(SELECT COALESCE(SUM(v.like_count), 0) FROM short_video v WHERE v.publisher_user_id = h.user_id AND v.deleted_at IS NULL) AS like_count, "
            "(SELECT COALESCE(SUM(v.tip_amount), 0) FROM short_video v WHERE v.publisher_user_id = h.user_id AND v.deleted_at IS NULL) AS tip_amount "
            f"{base} WHERE {clause} ORDER BY h.id DESC LIMIT :limit OFFSET :offset"
        ),
        params,
    )
    count = await db.execute(
        text(f"SELECT COUNT(*) {base} WHERE {clause}"),
        {k: v for k, v in params.items() if k not in ("limit", "offset")},
    )
    total = int(count.scalar() or 0)
    items = [
        VideoHomepageItem(
            id=int(r["id"]),
            nickname=r["nickname"],
            wechat=r["wechat"],
            bio=r["bio"],
            video_count=int(r["video_count"] or 0),
            view_count=int(r["view_count"] or 0),
            follower_count=int(r["follower_count"] or 0),
            like_count=int(r["like_count"] or 0),
            tip_amount=_money(r["tip_amount"]),
            certified=_bool(r["certified"]) or _bool(r["is_real_name"]),
            created_at=r["created_at"] or r["user_created_at"],
        )
        for r in rows.mappings().all()
    ]
    return VideoHomepagePage(
        items=items, page=page, page_size=page_size, total=total, has_more=page * page_size < total
    )


async def set_homepage_certified(db: AsyncSession, homepage_id: int, certified: bool) -> VideoHomepageItem:
    result = await db.execute(
        text("UPDATE short_video_homepage SET certified = :c, updated_at = UTC_TIMESTAMP() WHERE id = :id"),
        {"c": 1 if certified else 0, "id": homepage_id},
    )
    if result.rowcount == 0:
        raise HTTPException(404, detail="会员主页不存在")
    await db.commit()
    page = await _get_homepage(db, homepage_id)
    return page


async def _get_homepage(db: AsyncSession, homepage_id: int) -> VideoHomepageItem:
    row = (
        await db.execute(
            text(
                "SELECT h.*, u.nickname, u.is_real_name, u.created_at AS user_created_at, "
                "(SELECT COUNT(*) FROM short_video v WHERE v.publisher_user_id = h.user_id AND v.deleted_at IS NULL) AS video_count, "
                "(SELECT COALESCE(SUM(v.view_count), 0) FROM short_video v WHERE v.publisher_user_id = h.user_id AND v.deleted_at IS NULL) AS view_count, "
                "(SELECT COALESCE(SUM(v.like_count), 0) FROM short_video v WHERE v.publisher_user_id = h.user_id AND v.deleted_at IS NULL) AS like_count, "
                "(SELECT COALESCE(SUM(v.tip_amount), 0) FROM short_video v WHERE v.publisher_user_id = h.user_id AND v.deleted_at IS NULL) AS tip_amount "
                "FROM short_video_homepage h LEFT JOIN users u ON u.id = h.user_id WHERE h.id = :id"
            ),
            {"id": homepage_id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(404, detail="会员主页不存在")
    r = row
    return VideoHomepageItem(
        id=int(r["id"]),
        nickname=r["nickname"],
        wechat=r["wechat"],
        bio=r["bio"],
        video_count=int(r["video_count"] or 0),
        view_count=int(r["view_count"] or 0),
        follower_count=int(r["follower_count"] or 0),
        like_count=int(r["like_count"] or 0),
        tip_amount=_money(r["tip_amount"]),
        certified=_bool(r["certified"]) or _bool(r["is_real_name"]),
        created_at=r["created_at"] or r["user_created_at"],
    )


async def update_homepage(
    db: AsyncSession, homepage_id: int, wechat: str | None, bio: str | None
) -> VideoHomepageItem:
    exists = (await db.execute(text("SELECT id FROM short_video_homepage WHERE id = :id"), {"id": homepage_id})).scalar()
    if not exists:
        raise HTTPException(404, detail="会员主页不存在")
    await db.execute(
        text("UPDATE short_video_homepage SET wechat = :w, bio = :b, updated_at = UTC_TIMESTAMP() WHERE id = :id"),
        {"w": wechat, "b": bio, "id": homepage_id},
    )
    await db.commit()
    return await _get_homepage(db, homepage_id)


async def delete_homepage(db: AsyncSession, homepage_id: int) -> None:
    result = await db.execute(
        text("UPDATE short_video_homepage SET deleted_at = UTC_TIMESTAMP() WHERE id = :id"), {"id": homepage_id}
    )
    if result.rowcount == 0:
        raise HTTPException(404, detail="会员主页不存在")
    await db.commit()

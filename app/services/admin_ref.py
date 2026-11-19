"""原站资源镜像 业务逻辑（全只读）。

数据由 scripts/import_refsite.py 一次性灌入 ref_* 表；
本服务只做列表/详情查询，供应用中心、婚创学苑、红娘课堂、用好系统、云端图库使用。
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.admin_ref import (
    RefGuideCategories,
    RefGuideDetail,
    RefGuideItem,
    RefGuidePage,
    RefInstructionItem,
    RefMaterialCategory,
    RefMaterialItem,
    RefMaterialPage,
    RefProduct,
    RefTeacher,
)


def _s(v: Any) -> str | None:
    """空串归一为 None，避免前端拿到 ''。"""
    if v is None:
        return None
    v = str(v).strip()
    return v or None


_GUIDE_SELECT = """
SELECT
    g.id, g.ref_id, g.title, g.guide_type, g.category_name,
    g.teacher_ref_id, g.teacher_name, g.cover_url, g.duration,
    g.likes, g.view_times, g.is_top, g.title_bold, g.title_color,
    g.on_shelf, g.created_at
FROM ref_guide g
"""


def _guide_item(row: dict[str, Any]) -> RefGuideItem:
    payload = dict(row)
    for key in ("cover_url", "category_name", "teacher_name", "title_color"):
        payload[key] = _s(payload.get(key))
    payload["teacher_ref_id"] = payload.get("teacher_ref_id") or None
    return RefGuideItem.model_validate(payload)


# ---------------------------------------------------------------- products


async def list_products(db: AsyncSession) -> list[RefProduct]:
    rows = (
        await db.execute(
            text(
                "SELECT id, ref_id, name, category, intro, detail, cover_url, "
                "album_url, price, purchased FROM ref_product "
                "ORDER BY purchased DESC, sort DESC, ref_id ASC"
            )
        )
    ).mappings().all()
    return [
        RefProduct(
            id=row["id"],
            ref_id=row["ref_id"],
            name=row["name"],
            category=_s(row.get("category")),
            intro=_s(row.get("intro")),
            detail=_s(row.get("detail")),
            cover_url=_s(row.get("cover_url")),
            album_url=_s(row.get("album_url")),
            price=float(row["price"]) if row.get("price") is not None else None,
            purchased=bool(row["purchased"]),
        )
        for row in rows
    ]


# ---------------------------------------------------------------- guides


async def guide_categories(db: AsyncSession) -> RefGuideCategories:
    cat_rows = (
        await db.execute(
            text(
                "SELECT category_name, COUNT(*) AS c FROM ref_guide "
                "WHERE on_shelf = 1 GROUP BY category_name ORDER BY c DESC"
            )
        )
    ).mappings().all()
    teacher_rows = (
        await db.execute(
            text(
                "SELECT id, ref_id, name, avatar_url, intro, course_count "
                "FROM ref_teacher ORDER BY course_count DESC"
            )
        )
    ).mappings().all()
    return RefGuideCategories(
        categories=[_s(row["category_name"]) or "" for row in cat_rows],
        teachers=[
            RefTeacher(
                id=row["id"],
                ref_id=row["ref_id"],
                name=row["name"],
                avatar_url=_s(row.get("avatar_url")),
                intro=_s(row.get("intro")),
                course_count=int(row.get("course_count") or 0),
            )
            for row in teacher_rows
        ],
    )


async def list_guides(
    db: AsyncSession,
    *,
    page: int,
    page_size: int,
    category: str | None,
    teacher_ref_id: int | None,
    guide_type: str | None,
    keyword: str | None,
) -> RefGuidePage:
    where = ["g.on_shelf = 1"]
    params: dict[str, Any] = {"limit": page_size, "offset": (page - 1) * page_size}
    if category and category != "all":
        where.append("g.category_name = :cat")
        params["cat"] = category
    if teacher_ref_id:
        where.append("g.teacher_ref_id = :tid")
        params["tid"] = teacher_ref_id
    if guide_type and guide_type != "all":
        where.append("g.guide_type = :gtype")
        params["gtype"] = guide_type
    if keyword:
        where.append("g.title LIKE :kw")
        params["kw"] = f"%{keyword}%"
    where_sql = f"WHERE {' AND '.join(where)}"

    total = int(
        (
            await db.execute(
                text(f"SELECT COUNT(*) FROM ref_guide g {where_sql}"),
                {k: v for k, v in params.items() if k not in ("limit", "offset")},
            )
        ).scalar()
        or 0
    )
    rows = (
        await db.execute(
            text(
                f"{_GUIDE_SELECT} {where_sql} "
                "ORDER BY g.is_top DESC, g.created_at DESC, g.ref_id DESC "
                "LIMIT :limit OFFSET :offset"
            ),
            params,
        )
    ).mappings().all()
    return RefGuidePage(
        items=[_guide_item(dict(row)) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
    )


async def get_guide(db: AsyncSession, guide_id: int) -> RefGuideDetail:
    row = (
        await db.execute(
            text(
                "SELECT id, ref_id, title, guide_type, category_name, teacher_ref_id, "
                "teacher_name, url, cover_url, content, duration, likes, view_times, "
                "is_top, title_bold, title_color, on_shelf, created_at "
                "FROM ref_guide WHERE id = :id"
            ),
            {"id": guide_id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(404, detail="课程不存在")
    payload = dict(row)
    for key in ("url", "content", "cover_url", "category_name", "teacher_name", "title_color"):
        payload[key] = _s(payload.get(key))
    return RefGuideDetail.model_validate(payload)


# ---------------------------------------------------------------- instructions


async def list_instructions(db: AsyncSession) -> list[RefInstructionItem]:
    rows = (
        await db.execute(
            text("SELECT id, ref_id, title, content, created_at FROM ref_instruction ORDER BY sort ASC, ref_id ASC")
        )
    ).mappings().all()
    return [
        RefInstructionItem(
            id=row["id"],
            ref_id=row["ref_id"],
            title=row["title"],
            content=_s(row.get("content")),
            created_at=row.get("created_at"),
        )
        for row in rows
    ]


# ---------------------------------------------------------------- materials


async def list_materials(
    db: AsyncSession,
    *,
    page: int,
    page_size: int,
    category: str | None,
    sub_category: str | None,
    keyword: str | None,
) -> RefMaterialPage:
    where: list[str] = []
    params: dict[str, Any] = {"limit": page_size, "offset": (page - 1) * page_size}
    if category and category != "all":
        where.append("m.category_name = :cat")
        params["cat"] = category
    if sub_category and sub_category != "all":
        where.append("m.sub_category_name = :sub")
        params["sub"] = sub_category
    if keyword:
        where.append("(m.title LIKE :kw OR CAST(m.ref_id AS CHAR) LIKE :kw)")
        params["kw"] = f"%{keyword}%"
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    total = int(
        (
            await db.execute(
                text(f"SELECT COUNT(*) FROM ref_material m {where_sql}"),
                {k: v for k, v in params.items() if k not in ("limit", "offset")},
            )
        ).scalar()
        or 0
    )
    rows = (
        await db.execute(
            text(
                "SELECT m.id, m.ref_id, m.title, m.category_name, m.sub_category_name, m.img_url "
                f"FROM ref_material m {where_sql} "
                "ORDER BY m.ref_id DESC LIMIT :limit OFFSET :offset"
            ),
            params,
        )
    ).mappings().all()

    cat_rows = (
        await db.execute(
            text(
                "SELECT category_name, sub_category_name, COUNT(*) AS c FROM ref_material "
                "GROUP BY category_name, sub_category_name"
            )
        )
    ).mappings().all()
    cats: dict[str, dict[str, Any]] = {}
    for row in cat_rows:
        name = row["category_name"]
        entry = cats.setdefault(name, {"subs": [], "total": 0})
        entry["total"] += int(row["c"] or 0)
        if row.get("sub_category_name"):
            entry["subs"].append(row["sub_category_name"])
    categories = [
        RefMaterialCategory(name=name, subs=sorted(set(v["subs"])), total=v["total"])
        for name, v in cats.items()
    ]

    return RefMaterialPage(
        items=[
            RefMaterialItem(
                id=row["id"],
                ref_id=row["ref_id"],
                title=_s(row.get("title")),
                category_name=row["category_name"],
                sub_category_name=_s(row.get("sub_category_name")),
                img_url=row["img_url"],
            )
            for row in rows
        ],
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
        categories=categories,
    )

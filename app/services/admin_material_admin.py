"""Cloud material library (云端素材库, A4) service for the back office.

文件落盘在路由层完成（复用 ``profile._read_limited`` / ``_image_outputs`` 与
``media_access.sign_media_url``），本服务只管分类与素材行的 CRUD 与审计。
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.admin_material_admin import (
    MaterialCategoryCreate,
    MaterialCategoryItem,
    MaterialCategoryPage,
    MaterialCategoryUpdate,
    MaterialItem,
    MaterialPage,
    MaterialUpdate,
)

_MATERIAL_CATEGORY_SELECT = """SELECT c.id, c.parent_id, c.name, c.sort, c.enabled, c.created_at,
    (SELECT COUNT(*) FROM admin_material m WHERE m.category_id = c.id AND m.deleted_at IS NULL) material_count
    FROM admin_material_category c"""


def _category_item(row: Any) -> MaterialCategoryItem:
    data = dict(row)
    data["enabled"] = bool(int(data["enabled"]))
    data["material_count"] = int(data.get("material_count") or 0)
    return MaterialCategoryItem(**data)


def _material_item(row: Any) -> MaterialItem:
    data = dict(row)
    data["size"] = int(data.get("size") or 0)
    return MaterialItem(**data)


# ─────────────────────────── 分类 ───────────────────────────


async def list_categories(
    db: AsyncSession, page: int, page_size: int, keyword: str | None, parent_id: int | None
) -> MaterialCategoryPage:
    where: list[str] = []
    params: dict[str, Any] = {}
    if keyword:
        where.append("c.name LIKE :keyword")
        params["keyword"] = f"%{keyword}%"
    if parent_id is not None:
        where.append("c.parent_id = :parent_id")
        params["parent_id"] = parent_id
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    total = int(
        (await db.execute(text(f"SELECT COUNT(*) FROM admin_material_category c {where_sql}"), params)).scalar() or 0
    )
    params["limit"] = page_size
    params["offset"] = (page - 1) * page_size
    rows = (
        await db.execute(
            text(
                f"{_MATERIAL_CATEGORY_SELECT} {where_sql}"
                " ORDER BY c.sort, c.id LIMIT :limit OFFSET :offset"
            ),
            params,
        )
    ).mappings().all()
    return MaterialCategoryPage(
        items=[_category_item(row) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
    )


async def create_category(db: AsyncSession, actor_id: int, body: MaterialCategoryCreate) -> MaterialCategoryItem:
    result = await db.execute(
        text(
            "INSERT INTO admin_material_category (parent_id, name, sort, enabled) "
            "VALUES (:parent_id, :name, :sort, :enabled)"
        ),
        {
            "parent_id": body.parent_id,
            "name": body.name,
            "sort": body.sort,
            "enabled": int(body.enabled),
        },
    )
    item = await _get_category(db, int(result.lastrowid))
    await db.execute(
        text(
            "INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id) "
            "VALUES (:actor, 'material_category.create', 'admin_material_category', :id)"
        ),
        {"actor": actor_id, "id": item.id},
    )
    await db.commit()
    return item


async def _get_category(db: AsyncSession, category_id: int) -> MaterialCategoryItem:
    row = (
        (
            await db.execute(
                text(f"{_MATERIAL_CATEGORY_SELECT} WHERE c.id = :id"),
                {"id": category_id},
            )
        )
        .mappings()
        .first()
    )
    if not row:
        raise HTTPException(404, detail="素材分类不存在")
    return _category_item(row)


async def update_category(
    db: AsyncSession, actor_id: int, category_id: int, body: MaterialCategoryUpdate
) -> MaterialCategoryItem:
    await _get_category(db, category_id)
    values = body.model_dump(exclude_unset=True)
    if "enabled" in values and values["enabled"] is not None:
        values["enabled"] = int(values["enabled"])
    if "parent_id" in values and values["parent_id"] is not None and values["parent_id"] == category_id:
        raise HTTPException(422, detail="父分类不能是自身")
    if values:
        assignments = ", ".join(f"{key} = :{key}" for key in values)
        await db.execute(
            text(f"UPDATE admin_material_category SET {assignments} WHERE id = :id"),
            {**values, "id": category_id},
        )
    await db.execute(
        text(
            "INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id) "
            "VALUES (:actor, 'material_category.update', 'admin_material_category', :id)"
        ),
        {"actor": actor_id, "id": category_id},
    )
    await db.commit()
    return await _get_category(db, category_id)


async def delete_category(db: AsyncSession, actor_id: int, category_id: int) -> None:
    item = await _get_category(db, category_id)
    if item.material_count:
        raise HTTPException(409, detail="分类下还有素材，不能删除")
    has_children = (
        await db.execute(
            text("SELECT COUNT(*) FROM admin_material_category WHERE parent_id = :id"),
            {"id": category_id},
        )
    ).scalar_one()
    if int(has_children or 0):
        raise HTTPException(409, detail="存在下级分类，不能删除")
    await db.execute(text("DELETE FROM admin_material_category WHERE id = :id"), {"id": category_id})
    await db.execute(
        text(
            "INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id) "
            "VALUES (:actor, 'material_category.delete', 'admin_material_category', :id)"
        ),
        {"actor": actor_id, "id": category_id},
    )
    await db.commit()


# ─────────────────────────── 素材 ───────────────────────────


async def list_materials(
    db: AsyncSession, page: int, page_size: int, keyword: str | None, category_id: int | None
) -> MaterialPage:
    where: list[str] = ["m.deleted_at IS NULL"]
    params: dict[str, Any] = {}
    if keyword:
        where.append("m.name LIKE :keyword")
        params["keyword"] = f"%{keyword}%"
    if category_id is not None:
        where.append("m.category_id = :category_id")
        params["category_id"] = category_id
    where_sql = " AND ".join(where)
    total = int(
        (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM admin_material m LEFT JOIN admin_material_category c ON c.id = m.category_id "
                    f"WHERE {where_sql}"
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
            text(
                "SELECT m.id, m.category_id, c.name AS category_name, m.name, m.url, m.thumbnail_url,"
                " m.mime_type, m.size, m.width, m.height, m.uploaded_by, m.created_at, m.updated_at"
                f" FROM admin_material m LEFT JOIN admin_material_category c ON c.id = m.category_id"
                f" WHERE {where_sql} ORDER BY m.id DESC LIMIT :limit OFFSET :offset"
            ),
            params,
        )
    ).mappings().all()
    return MaterialPage(
        items=[_material_item(row) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
    )


async def get_material_source_url(db: AsyncSession, material_id: int) -> str:
    row = (
        await db.execute(
            text("SELECT url FROM admin_material WHERE id = :id AND deleted_at IS NULL"),
            {"id": material_id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(404, detail="素材不存在")
    return str(row["url"])


async def record_material_public_copy(db: AsyncSession, actor_id: int, material_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id) "
            "VALUES (:actor, 'material.copy_to_common', 'admin_material', :id)"
        ),
        {"actor": actor_id, "id": material_id},
    )
    await db.commit()


async def _get_material(db: AsyncSession, material_id: int) -> MaterialItem:
    row = (
        await db.execute(
            text(
                "SELECT m.id, m.category_id, c.name AS category_name, m.name, m.url, m.thumbnail_url,"
                " m.mime_type, m.size, m.width, m.height, m.uploaded_by, m.deleted_at, m.created_at, m.updated_at"
                " FROM admin_material m LEFT JOIN admin_material_category c ON c.id = m.category_id"
                " WHERE m.id = :id"
            ),
            {"id": material_id},
        )
    ).mappings().first()
    if not row or row.get("deleted_at") is not None:
        raise HTTPException(404, detail="素材不存在")
    row.pop("deleted_at", None)
    return _material_item(row)


async def register_material(
    db: AsyncSession,
    actor_id: int,
    *,
    category_id: int,
    name: str,
    url: str,
    thumbnail_url: str,
    mime_type: str,
    size: int,
    width: int | None,
    height: int | None,
) -> MaterialItem:
    """路由层落盘成功后登记素材行（同一事务内，避免落盘孤儿数据）。"""
    await db.execute(
        text(
            "INSERT INTO admin_material (category_id, name, url, thumbnail_url, mime_type, size, width, height, uploaded_by) "
            "VALUES (:category_id, :name, :url, :thumbnail_url, :mime_type, :size, :width, :height, :uploaded_by)"
        ),
        {
            "category_id": category_id,
            "name": name,
            "url": url,
            "thumbnail_url": thumbnail_url,
            "mime_type": mime_type,
            "size": size,
            "width": width,
            "height": height,
            "uploaded_by": actor_id,
        },
    )
    last_id = int((await db.execute(text("SELECT LAST_INSERT_ID() AS id"))).scalar_one())
    item = await _get_material(db, last_id)
    await db.execute(
        text(
            "INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id) "
            "VALUES (:actor, 'material.create', 'admin_material', :id)"
        ),
        {"actor": actor_id, "id": item.id},
    )
    await db.commit()
    return item


async def update_material(db: AsyncSession, actor_id: int, material_id: int, body: MaterialUpdate) -> MaterialItem:
    await _get_material(db, material_id)
    values = body.model_dump(exclude_unset=True)
    if "category_id" in values and values["category_id"] is not None:
        exists = (
            await db.execute(
                text("SELECT 1 FROM admin_material_category WHERE id = :id AND enabled = 1"),
                {"id": values["category_id"]},
            )
        ).scalar_one_or_none()
        if not exists:
            raise HTTPException(422, detail="目标分类不存在或已停用")
    if values:
        assignments = ", ".join(f"{key} = :{key}" for key in values)
        await db.execute(
            text(f"UPDATE admin_material SET {assignments}, updated_at = UTC_TIMESTAMP() WHERE id = :id"),
            {**values, "id": material_id},
        )
    await db.execute(
        text(
            "INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id) "
            "VALUES (:actor, 'material.update', 'admin_material', :id)"
        ),
        {"actor": actor_id, "id": material_id},
    )
    await db.commit()
    return await _get_material(db, material_id)


async def delete_material(db: AsyncSession, actor_id: int, material_id: int) -> None:
    await _get_material(db, material_id)
    await db.execute(text("UPDATE admin_material SET deleted_at = UTC_TIMESTAMP() WHERE id = :id"), {"id": material_id})
    await db.execute(
        text(
            "INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id) "
            "VALUES (:actor, 'material.delete', 'admin_material', :id)"
        ),
        {"actor": actor_id, "id": material_id},
    )
    await db.commit()

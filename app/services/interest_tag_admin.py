"""兴趣标签目录（分类 + 标签）后台服务。

目录写入 `interest_tag_category` / `interest_tag` 两张表，形态与客源来源字典一致：
统一的 list/create/update/delete/reorder。与来源字典的差别在于这里有两层实体
（分类 + 标签），且每次写库成功后必须刷新进程内缓存 `app.core.tag_catalog`，
让前台 `tag-options` 与同步的 `personal_tags` 校验即时看到新目录。

删除保护：
- 分类：其下仍有标签时禁止删除（409），需先清空/迁移标签，避免标签变孤儿。
- 标签：仍被 `user_profile`（interest_tags / personality_tags / tags）引用时禁止删除
  （409），与来源字典「在用即禁删」同口径。

字典为全局配置型数据，不做数据范围收敛。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import tag_catalog
from app.schemas.interest_tag_admin import (
    TagCategoryCreate,
    TagCategoryItem,
    TagCategoryUpdate,
    TagCreate,
    TagItem,
    TagOrder,
    TagUpdate,
)

_CATEGORY_COLUMNS = {"label", "enabled", "sort"}
_TAG_COLUMNS = {"name", "enabled", "sort"}

# 统计仍在使用该标签名的资料行数（命中任一 JSON 数组列即算引用）。
_TAG_REFERENCED_SQL = (
    "SELECT COUNT(*) FROM user_profile "
    "WHERE JSON_CONTAINS(COALESCE(interest_tags, '[]'), :json_value) = 1 "
    "   OR JSON_CONTAINS(COALESCE(personality_tags, '[]'), :json_value) = 1 "
    "   OR JSON_CONTAINS(COALESCE(tags, '[]'), :json_value) = 1"
)


async def _reload(db: AsyncSession) -> None:
    """写库成功后刷新缓存；表被回滚删除时 reload 内部会自动回退常量目录。"""
    await tag_catalog.reload(db)


# ───────────────────────────── 分类 ─────────────────────────────
async def list_categories(db: AsyncSession) -> list[TagCategoryItem]:
    rows = (
        await db.execute(text(
            "SELECT id, `key`, label, enabled, sort FROM interest_tag_category ORDER BY sort ASC, id ASC"
        ))
    ).mappings().all()
    return [_category_item(row) for row in rows]


def _category_item(row: Any) -> TagCategoryItem:
    return TagCategoryItem(
        id=int(row["id"]),
        key=str(row["key"]),
        label=str(row["label"]),
        enabled=int(row["enabled"] or 0),
        sort=int(row["sort"] or 0),
    )


async def _get_category(db: AsyncSession, item_id: int) -> TagCategoryItem:
    row = (
        await db.execute(text(
            "SELECT id, `key`, label, enabled, sort FROM interest_tag_category WHERE id = :id"
        ), {"id": item_id})
    ).mappings().first()
    if not row:
        raise HTTPException(404, detail="分类不存在")
    return _category_item(row)


async def _category_exists(db: AsyncSession, key: str, exclude_id: int | None = None) -> bool:
    where = ["`key` = :key"]
    params: dict[str, Any] = {"key": key}
    if exclude_id is not None:
        where.append("id <> :exclude_id")
        params["exclude_id"] = exclude_id
    return bool((await db.execute(
        text(f"SELECT id FROM interest_tag_category WHERE {' AND '.join(where)}"), params
    )).scalar())


async def create_category(db: AsyncSession, body: TagCategoryCreate) -> TagCategoryItem:
    if await _category_exists(db, body.key):
        raise HTTPException(409, detail="分类 key 已存在")
    values = {k: v for k, v in body.model_dump().items() if k in _CATEGORY_COLUMNS | {"key"}}
    try:
        result = await db.execute(
            text(f"INSERT INTO interest_tag_category ({', '.join(values)}) VALUES ({', '.join(f':{k}' for k in values)})"),
            values,
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, detail="分类 key 已存在") from None
    await _reload(db)
    return await _get_category(db, int(result.lastrowid))


async def update_category(db: AsyncSession, item_id: int, body: TagCategoryUpdate) -> TagCategoryItem:
    await _get_category(db, item_id)  # 存在性校验
    values = {k: v for k, v in body.model_dump(exclude_unset=True).items() if k in _CATEGORY_COLUMNS}
    if values:
        assignments = ", ".join(f"{k} = :{k}" for k in values)
        await db.execute(
            text(f"UPDATE interest_tag_category SET {assignments} WHERE id = :id"),
            {**values, "id": item_id},
        )
        await db.commit()
    await _reload(db)
    return await _get_category(db, item_id)


async def delete_category(db: AsyncSession, item_id: int) -> None:
    category = await _get_category(db, item_id)
    tag_count = int((await db.execute(
        text("SELECT COUNT(*) FROM interest_tag WHERE category_key = :key"), {"key": category.key}
    )).scalar() or 0)
    if tag_count > 0:
        raise HTTPException(409, detail=f"该分类下仍有 {tag_count} 个标签，请先删除或迁移标签")
    await db.execute(text("DELETE FROM interest_tag_category WHERE id = :id"), {"id": item_id})
    await db.commit()
    await _reload(db)


async def reorder_categories(db: AsyncSession, order: TagOrder) -> None:
    for index, item_id in enumerate(order.ids, start=1):
        await db.execute(
            text("UPDATE interest_tag_category SET sort = :sort WHERE id = :id"),
            {"sort": index, "id": int(item_id)},
        )
    await db.commit()
    await _reload(db)


# ───────────────────────────── 标签 ─────────────────────────────
async def list_tags(db: AsyncSession, category_key: str | None = None) -> list[TagItem]:
    if category_key:
        rows = (
            await db.execute(text(
                "SELECT id, category_key, name, enabled, sort FROM interest_tag "
                "WHERE category_key = :category_key ORDER BY sort ASC, id ASC"
            ), {"category_key": category_key})
        ).mappings().all()
    else:
        rows = (
            await db.execute(text(
                "SELECT id, category_key, name, enabled, sort FROM interest_tag ORDER BY category_key ASC, sort ASC, id ASC"
            ))
        ).mappings().all()
    return [_tag_item(row) for row in rows]


def _tag_item(row: Any) -> TagItem:
    return TagItem(
        id=int(row["id"]),
        category_key=str(row["category_key"]),
        name=str(row["name"]),
        enabled=int(row["enabled"] or 0),
        sort=int(row["sort"] or 0),
    )


async def _get_tag(db: AsyncSession, item_id: int) -> TagItem:
    row = (
        await db.execute(text(
            "SELECT id, category_key, name, enabled, sort FROM interest_tag WHERE id = :id"
        ), {"id": item_id})
    ).mappings().first()
    if not row:
        raise HTTPException(404, detail="标签不存在")
    return _tag_item(row)


async def _tag_name_exists(db: AsyncSession, category_key: str, name: str, exclude_id: int | None = None) -> bool:
    where = ["category_key = :category_key", "name = :name"]
    params: dict[str, Any] = {"category_key": category_key, "name": name}
    if exclude_id is not None:
        where.append("id <> :exclude_id")
        params["exclude_id"] = exclude_id
    return bool((await db.execute(
        text(f"SELECT id FROM interest_tag WHERE {' AND '.join(where)}"), params
    )).scalar())


async def create_tag(db: AsyncSession, body: TagCreate) -> TagItem:
    if not await _category_exists(db, body.category_key):
        raise HTTPException(422, detail="所属分类不存在")
    if await _tag_name_exists(db, body.category_key, body.name):
        raise HTTPException(409, detail="该分类下标签名称已存在")
    values = {k: v for k, v in body.model_dump().items() if k in _TAG_COLUMNS | {"category_key"}}
    try:
        result = await db.execute(
            text(f"INSERT INTO interest_tag ({', '.join(values)}) VALUES ({', '.join(f':{k}' for k in values)})"),
            values,
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, detail="该分类下标签名称已存在") from None
    await _reload(db)
    return await _get_tag(db, int(result.lastrowid))


async def update_tag(db: AsyncSession, item_id: int, body: TagUpdate) -> TagItem:
    tag = await _get_tag(db, item_id)
    values = {k: v for k, v in body.model_dump(exclude_unset=True).items() if k in _TAG_COLUMNS}
    if "name" in values and await _tag_name_exists(db, tag.category_key, values["name"], exclude_id=item_id):
        raise HTTPException(409, detail="该分类下标签名称已存在")
    if values:
        assignments = ", ".join(f"{k} = :{k}" for k in values)
        try:
            await db.execute(
                text(f"UPDATE interest_tag SET {assignments} WHERE id = :id"),
                {**values, "id": item_id},
            )
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(409, detail="该分类下标签名称已存在") from None
    await _reload(db)
    return await _get_tag(db, item_id)


async def delete_tag(db: AsyncSession, item_id: int) -> None:
    tag = await _get_tag(db, item_id)
    referenced = int((await db.execute(
        text(_TAG_REFERENCED_SQL), {"json_value": json.dumps(tag.name)}
    )).scalar() or 0)
    if referenced > 0:
        raise HTTPException(409, detail=f"仍有 {referenced} 位用户在用该标签，无法删除")
    await db.execute(text("DELETE FROM interest_tag WHERE id = :id"), {"id": item_id})
    await db.commit()
    await _reload(db)


async def reorder_tags(db: AsyncSession, order: TagOrder) -> None:
    for index, item_id in enumerate(order.ids, start=1):
        await db.execute(
            text("UPDATE interest_tag SET sort = :sort WHERE id = :id"),
            {"sort": index, "id": int(item_id)},
        )
    await db.commit()
    await _reload(db)

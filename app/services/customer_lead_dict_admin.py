"""客源来源字典（A14）后台服务。

来源写入 `customer_lead_source` 表，提供统一的
list/create/update/delete/reorder。

与自由文本 `customer_lead.source` 的兼容：字典是运营维护的「候选来源」，客源
录入仍按名称写自由文本，两者按 name 对齐；删除字典项不影响历史客源的 source
文本，但仍有有效客源引用该名称时禁止删除（409），避免下拉字典与在用数据脱节。

字典为全局配置型数据，不做数据范围收敛（D-16 只约束按归属可见的业务行）。
"""

from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.customer_lead_dict_admin import LeadDictCreate, LeadDictItem, LeadDictUpdate

# kind 与前端契约路径段一致，用复数 sources（对齐
# GET /admin/customer-leads/options 返回的 sources 分组命名）。
_TABLE: dict[str, str] = {"sources": "customer_lead_source"}
_TYPE_LABEL: dict[str, str] = {"sources": "sources"}
# 来源表有 color 列。
_COLUMNS: dict[str, set[str]] = {"sources": {"name", "color", "enabled", "sort"}}

# 统计仍在使用该字典项的有效客源数（来源按名称匹配自由文本）。
_REFERENCED_SQL: dict[str, str] = {
    "sources": (
        "SELECT COUNT(*) FROM customer_lead WHERE source = :name AND deleted_at IS NULL"
    ),
}


def _table(kind: str) -> str:
    if kind not in _TABLE:
        raise HTTPException(422, detail="不支持的字典类型")
    return _TABLE[kind]


async def _name_exists(db: AsyncSession, kind: str, name: str, exclude_id: int | None = None) -> bool:
    table = _table(kind)
    where = ["name = :name"]
    params: dict[str, Any] = {"name": name}
    if exclude_id is not None:
        where.append("id <> :exclude_id")
        params["exclude_id"] = exclude_id
    return bool((await db.execute(text(f"SELECT id FROM {table} WHERE {' AND '.join(where)}"), params)).scalar())


async def list_dict(db: AsyncSession, kind: str) -> list[LeadDictItem]:
    table = _table(kind)
    select_columns = "id, name, enabled, sort" + (", color" if kind == "sources" else "")
    rows = (
        await db.execute(text(f"SELECT {select_columns} FROM {table} ORDER BY sort ASC, id ASC"))
    ).mappings().all()
    return [
        LeadDictItem(
            id=int(row["id"]),
            name=str(row["name"]),
            type=_TYPE_LABEL[kind],
            color=row.get("color") if kind == "sources" else None,
            enabled=int(row["enabled"] or 0),
            sort=int(row["sort"] or 0),
        )
        for row in rows
    ]


async def _get_item(db: AsyncSession, kind: str, item_id: int) -> LeadDictItem:
    table = _table(kind)
    select_columns = "id, name, enabled, sort" + (", color" if kind == "sources" else "")
    row = (
        await db.execute(text(f"SELECT {select_columns} FROM {table} WHERE id = :id"), {"id": item_id})
    ).mappings().first()
    if not row:
        raise HTTPException(404, detail="字典项不存在")
    return LeadDictItem(
        id=int(row["id"]),
        name=str(row["name"]),
        type=_TYPE_LABEL[kind],
        color=row.get("color") if kind == "sources" else None,
        enabled=int(row["enabled"] or 0),
        sort=int(row["sort"] or 0),
    )


async def create_dict(db: AsyncSession, kind: str, body: LeadDictCreate) -> LeadDictItem:
    table = _table(kind)
    if await _name_exists(db, kind, body.name):
        raise HTTPException(409, detail="名称已存在")
    columns = _COLUMNS[kind]
    values = {key: value for key, value in body.model_dump().items() if key in columns}
    try:
        result = await db.execute(
            text(f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join(f':{key}' for key in values)})"),
            values,
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, detail="名称已存在") from None
    return await _get_item(db, kind, int(result.lastrowid))


async def update_dict(db: AsyncSession, kind: str, item_id: int, body: LeadDictUpdate) -> LeadDictItem:
    table = _table(kind)
    await _get_item(db, kind, item_id)
    columns = _COLUMNS[kind]
    values = {key: value for key, value in body.model_dump(exclude_unset=True).items() if key in columns}
    if "name" in values and await _name_exists(db, kind, values["name"], exclude_id=item_id):
        raise HTTPException(409, detail="名称已存在")
    assignments = ", ".join(f"{key} = :{key}" for key in values)
    if assignments:
        try:
            await db.execute(text(f"UPDATE {table} SET {assignments} WHERE id = :id"), {**values, "id": item_id})
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(409, detail="名称已存在") from None
    return await _get_item(db, kind, item_id)


async def delete_dict(db: AsyncSession, kind: str, item_id: int) -> None:
    """删除字典项；仍被有效客源引用时禁止（409），否则物理删除。"""
    item = await _get_item(db, kind, item_id)
    count_params: dict[str, Any] = {"id": item_id, "name": item.name}
    referenced = int(
        (await db.execute(text(_REFERENCED_SQL[kind]), count_params)).scalar() or 0
    )
    if referenced > 0:
        raise HTTPException(409, detail=f"仍有 {referenced} 条有效客源使用该名称，无法删除")
    await db.execute(text(f"DELETE FROM {_table(kind)} WHERE id = :id"), {"id": item_id})
    await db.commit()


async def reorder_dict(db: AsyncSession, kind: str, ids: list[int]) -> None:
    """按传入顺序写 sort（从 1 递增）。仅更新该 kind 现有行，未命中的 id 跳过。"""
    table = _table(kind)
    for index, item_id in enumerate(ids, start=1):
        await db.execute(
            text(f"UPDATE {table} SET sort = :sort WHERE id = :id"),
            {"sort": index, "id": int(item_id)},
        )
    await db.commit()

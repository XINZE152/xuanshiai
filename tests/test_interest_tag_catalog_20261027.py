"""2026-10-27 兴趣标签目录（表 + 进程内缓存 + 后台 CRUD）回归。

无需 DB：覆盖缓存回退/重建、reload 读表查询、CRUD 服务逻辑（查重/引用守卫/重排 +
写后刷新缓存）、schema 边界、路由注册顺序、tag-options 契约、建表 DDL 与读表查询一致。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core import tag_catalog
from app.core.profile_tags import TAG_CATEGORIES
from app.db.business_schema import BUSINESS_TABLES
from app.api.routes import interest_tags_admin as tag_routes
from app.schemas.interest_tag_admin import (
    TagCategoryCreate,
    TagCategoryUpdate,
    TagCreate,
    TagOrder,
    TagUpdate,
)
from app.services import interest_tag_admin as service


class _Result:
    def __init__(self, *, rows=None, mappings_rows=None, scalar=None, lastrowid=None):
        self._rows = list(rows or [])
        self._mappings_rows = list(mappings_rows or [])
        self._scalar = scalar
        self.lastrowid = lastrowid

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._scalar

    def mappings(self):
        return SimpleNamespace(
            all=lambda: self._mappings_rows,
            first=lambda: (self._mappings_rows[0] if self._mappings_rows else None),
        )


class _StubDB:
    """按序返回预设结果；记录写库语句，用于无 DB 环境下的服务逻辑断言。"""

    def __init__(self, results: list[_Result]):
        self._results = list(results)
        self.writes: list[tuple[str, dict]] = []
        self.reads: list[str] = []
        self.commits = 0

    async def execute(self, statement, params=None):
        sql = str(statement)
        result = self._results.pop(0) if self._results else _Result(rows=[], mappings_rows=[])
        if sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            self.writes.append((sql, dict(params or {})))
        else:
            self.reads.append(sql)
        return result

    async def commit(self):
        self.commits += 1


@pytest.fixture(autouse=True)
def _reset_catalog():
    tag_catalog.reset_to_defaults()
    yield
    tag_catalog.reset_to_defaults()


# --------------------------------------------------------------------------- #
# 缓存层
# --------------------------------------------------------------------------- #


def test_catalog_defaults_match_constants() -> None:
    assert len(tag_catalog.category_rows()) == len(TAG_CATEGORIES) == 17
    assert tag_catalog.all_tag_options() == frozenset(
        option for _, _, options in TAG_CATEGORIES for option in options
    )
    assert "外向开朗" in tag_catalog.all_tag_options()
    assert tag_catalog.personality_options() == frozenset(
        next(options for key, _, options in TAG_CATEGORIES if key == "personality")
    )


def test_casefold_map_rebuilt_after_load() -> None:
    tag_catalog.load_from_rows(
        [("taste", "口味")],
        {"taste": ["甜", "咸"]},
    )
    assert tag_catalog.all_tag_options() == frozenset({"甜", "咸"})
    assert tag_catalog.casefold_map() == {"甜": "甜", "咸": "咸"}
    assert tag_catalog.options_by_category() == {"taste": frozenset({"甜", "咸"})}
    # 非 personality 分类时性格集合清空
    assert tag_catalog.personality_options() == frozenset()


def test_reset_to_defaults_restores_constants() -> None:
    tag_catalog.load_from_rows([("x", "X")], {"x": ["a"]})
    assert tag_catalog.all_tag_options() == frozenset({"a"})
    tag_catalog.reset_to_defaults()
    assert len(tag_catalog.category_rows()) == 17


@pytest.mark.asyncio
async def test_reload_builds_from_rows() -> None:
    db = _StubDB([
        _Result(mappings_rows=[{"key": "personality", "label": "性格"}, {"key": "sports", "label": "运动"}]),
        _Result(mappings_rows=[
            {"category_key": "personality", "name": "乐观"},
            {"category_key": "sports", "name": "跑步"},
        ]),
    ])
    await tag_catalog.reload(db)
    assert [key for key, _, _ in tag_catalog.category_rows()] == ["personality", "sports"]
    assert tag_catalog.all_tag_options() == frozenset({"乐观", "跑步"})
    # 读表查询指向正确的表与列
    assert "FROM interest_tag_category" in db.reads[0]
    assert "`key`, label" in db.reads[0]
    assert "FROM interest_tag" in db.reads[1]
    assert "category_key, name" in db.reads[1]


@pytest.mark.asyncio
async def test_reload_empty_falls_back_to_defaults() -> None:
    # 先加载一份自定义目录
    tag_catalog.load_from_rows([("x", "X")], {"x": ["a"]})
    # 表为空（分类查询无行）→ reload 回退常量目录，不抛错
    db = _StubDB([_Result(mappings_rows=[])])
    await tag_catalog.reload(db)
    assert len(tag_catalog.category_rows()) == 17


# --------------------------------------------------------------------------- #
# 校验接线随缓存变化（personal_tags 走 profile_tags → tag_catalog）
# --------------------------------------------------------------------------- #


def test_validate_selection_follows_cache() -> None:
    from app.core.profile_tags import validate_personal_tag_selection

    # 默认目录下：目录内标签合法
    assert validate_personal_tag_selection(["外向开朗"]) == ["外向开朗"]
    # 重建目录后，新目录值合法命中
    tag_catalog.load_from_rows([("t", "T")], {"t": ["围棋"]})
    assert validate_personal_tag_selection(["围棋"]) == ["围棋"]
    with pytest.raises(ValueError):
        validate_personal_tag_selection(["围棋", "围棋"])  # 重复


# --------------------------------------------------------------------------- #
# CRUD 服务逻辑
# --------------------------------------------------------------------------- #


@pytest.fixture
def _no_reload(monkeypatch):
    calls: list[int] = []

    async def _fake_reload(db):
        calls.append(1)

    monkeypatch.setattr(service, "_reload", _fake_reload)
    return calls


@pytest.mark.asyncio
async def test_create_category_rejects_duplicate_key(_no_reload) -> None:
    from fastapi import HTTPException

    db = _StubDB([_Result(scalar=1)])  # key 已存在
    with pytest.raises(HTTPException) as exc:
        await service.create_category(db, TagCategoryCreate(key="food", label="美食"))
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_create_category_success_and_reloads(_no_reload) -> None:
    db = _StubDB([
        _Result(rows=[]),                       # key 不存在
        _Result(lastrowid=7),                   # INSERT
        _Result(mappings_rows=[{"id": 7, "key": "books", "label": "书籍", "enabled": 1, "sort": 0}]),
    ])
    item = await service.create_category(db, TagCategoryCreate(key="books", label="书籍"))
    assert item.id == 7 and item.key == "books"
    assert any(sql.lstrip().upper().startswith("INSERT INTO INTEREST_TAG_CATEGORY") for sql, _ in db.writes)
    assert _no_reload  # 写后刷新被调用
    assert len(_no_reload) == 1


@pytest.mark.asyncio
async def test_create_tag_rejects_unknown_category(_no_reload) -> None:
    from fastapi import HTTPException

    db = _StubDB([_Result(rows=[])])  # 分类不存在
    with pytest.raises(HTTPException) as exc:
        await service.create_tag(db, TagCreate(category_key="ghost", name="x"))
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_create_tag_rejects_duplicate_name(_no_reload) -> None:
    from fastapi import HTTPException

    db = _StubDB([
        _Result(scalar=1),   # 分类存在
        _Result(scalar=1),   # 同名标签已存在
    ])
    with pytest.raises(HTTPException) as exc:
        await service.create_tag(db, TagCreate(category_key="food", name="火锅"))
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_delete_tag_guard_when_referenced(_no_reload) -> None:
    from fastapi import HTTPException

    db = _StubDB([
        _Result(mappings_rows=[{"id": 3, "category_key": "food", "name": "火锅", "enabled": 1, "sort": 1}]),
        _Result(rows=[], scalar=5),  # 5 位用户在用
    ])
    with pytest.raises(HTTPException) as exc:
        await service.delete_tag(db, 3)
    assert exc.value.status_code == 409
    assert not db.writes  # 被守卫拦下，未删除


@pytest.mark.asyncio
async def test_delete_tag_success_when_unused(_no_reload) -> None:
    db = _StubDB([
        _Result(mappings_rows=[{"id": 3, "category_key": "food", "name": "火锅", "enabled": 1, "sort": 1}]),
        _Result(rows=[], scalar=0),  # 无引用
    ])
    await service.delete_tag(db, 3)
    assert any("DELETE FROM interest_tag" in sql for sql, _ in db.writes)
    assert len(_no_reload) == 1


@pytest.mark.asyncio
async def test_delete_category_guard_when_tags_exist(_no_reload) -> None:
    from fastapi import HTTPException

    db = _StubDB([
        _Result(mappings_rows=[{"id": 9, "key": "food", "label": "美食", "enabled": 1, "sort": 1}]),
        _Result(rows=[], scalar=2),  # 分类下 2 个标签
    ])
    with pytest.raises(HTTPException) as exc:
        await service.delete_category(db, 9)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_reorder_tags_writes_sort_by_index(_no_reload) -> None:
    db = _StubDB([])
    await service.reorder_tags(db, TagOrder(ids=[10, 20, 30]))
    sorts = [params["sort"] for _, params in db.writes]
    assert sorts == [1, 2, 3]
    assert db.commits == 1
    assert len(_no_reload) == 1


# --------------------------------------------------------------------------- #
# schema 边界
# --------------------------------------------------------------------------- #


def test_category_key_pattern() -> None:
    TagCategoryCreate(key="knowledge_growth", label="知识成长")
    for bad in ("Food", "food tv", "兴趣", ""):
        with pytest.raises(Exception):
            TagCategoryCreate(key=bad, label="x")


def test_update_requires_field() -> None:
    with pytest.raises(Exception):
        TagCategoryUpdate()
    with pytest.raises(Exception):
        TagUpdate()
    assert TagUpdate(enabled=0).enabled == 0


def test_order_bounds() -> None:
    TagOrder(ids=[1, 2])
    with pytest.raises(Exception):
        TagOrder(ids=[])


# --------------------------------------------------------------------------- #
# 路由注册
# --------------------------------------------------------------------------- #


def test_tag_routes_registered_and_static_before_dynamic() -> None:
    paths = [route.path for route in tag_routes.router.routes]
    for static in (
        "/admin/interest-tags/categories",
        "/admin/interest-tags/categories/reorder",
        "/admin/interest-tags/tags",
        "/admin/interest-tags/tags/reorder",
    ):
        assert static in paths
    # reorder 静态段先于 /{item_id} 动态段注册
    assert paths.index("/admin/interest-tags/categories/reorder") < paths.index("/admin/interest-tags/categories/{item_id}")
    assert paths.index("/admin/interest-tags/tags/reorder") < paths.index("/admin/interest-tags/tags/{item_id}")


def test_tag_routes_declare_permission() -> None:
    from app.api.dependencies import PERMISSION_ATTR

    for route in tag_routes.router.routes:
        assert getattr(route.endpoint, PERMISSION_ATTR, None) == "interest_tag.manage"


# --------------------------------------------------------------------------- #
# tag-options 契约（读缓存）
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_get_tag_options_structure_unchanged() -> None:
    from app.services.profile import get_tag_options

    response = await get_tag_options()
    assert response.version == "personal-v2"
    assert len(response.categories) == 17
    first = response.categories[0]
    assert {first.key, first.label, isinstance(first.options, list)}


# --------------------------------------------------------------------------- #
# 建表 DDL 与读表查询列一致
# --------------------------------------------------------------------------- #


def test_ddl_matches_reload_columns() -> None:
    cat_ddl = BUSINESS_TABLES["interest_tag_category"]
    tag_ddl = BUSINESS_TABLES["interest_tag"]
    assert "CREATE TABLE IF NOT EXISTS `interest_tag_category`" in cat_ddl
    assert "`key`" in cat_ddl and "uk_interest_tag_category_key" in cat_ddl
    assert "CREATE TABLE IF NOT EXISTS `interest_tag`" in tag_ddl
    assert "uk_interest_tag_category_name" in tag_ddl
    for column in ("category_key", "name", "sort", "enabled"):
        assert f"`{column}`" in tag_ddl, column

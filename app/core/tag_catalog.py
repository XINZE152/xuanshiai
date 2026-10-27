"""兴趣标签目录的运行时缓存（表 + 进程内缓存）。

设计：目录数据源是 `interest_tag_category` / `interest_tag` 两张表，后台 CRUD 可维护；
`personal_tags` 的校验发生在 pydantic 同步 validator 里，无法直接查库，因此本模块把
当前目录常驻进程内内存，同步校验器只读内存。

- 未加载（表不存在/迁移未跑/测试环境）时，缓存以 `profile_tags` 里的常量目录为准，
  行为与改造前完全一致（安全回退）。
- `reload(db)` 从两张表重建缓存；后台每次写库成功后调用它即时生效（单进程部署下
  无跨进程陈旧问题；如未来启用多 worker，需要改成 TTL 或广播失效）。

依赖方向：本模块在顶层 import `profile_tags` 取默认常量；`profile_tags` 的函数只在
被调用时惰性 import 本模块，避免模块加载期循环依赖。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import profile_tags as _defaults


class _CatalogState:
    """当前生效的目录快照（可被 DB 行整体替换）。"""

    def __init__(self) -> None:
        self.reset_to_defaults()

    def reset_to_defaults(self) -> None:
        self.category_rows: list[tuple[str, str, tuple[str, ...]]] = [
            (key, label, tuple(options)) for key, label, options in _defaults.TAG_CATEGORIES
        ]
        self.options_by_category: dict[str, frozenset[str]] = dict(_defaults.TAG_OPTIONS_BY_CATEGORY)
        self.all_options: frozenset[str] = _defaults.ALL_TAG_OPTIONS
        self.casefold_map: dict[str, str] = dict(_defaults._TAG_OPTION_BY_CASEFOLD)
        self.personality: frozenset[str] = _defaults.PERSONALITY_OPTIONS

    def load(self, categories: list[tuple[str, str]], tags_by_category: dict[str, list[str]]) -> None:
        rows: list[tuple[str, str, tuple[str, ...]]] = []
        by_category: dict[str, frozenset[str]] = {}
        all_options: set[str] = set()
        casefold: dict[str, str] = {}
        personality: frozenset[str] = frozenset()
        for key, label in categories:
            options = tuple(tags_by_category.get(key, []))
            rows.append((key, label, options))
            fs = frozenset(options)
            by_category[key] = fs
            all_options.update(options)
            for option in options:
                casefold[option.casefold()] = option
            if key == "personality":
                personality = fs
        self.category_rows = rows
        self.options_by_category = by_category
        self.all_options = frozenset(all_options)
        self.casefold_map = casefold
        self.personality = personality


_state = _CatalogState()


def all_tag_options() -> frozenset[str]:
    """当前目录内全部系统标签（去重集合）。"""
    return _state.all_options


def options_by_category() -> dict[str, frozenset[str]]:
    """分类 key → 该分类标签集合。"""
    return _state.options_by_category


def casefold_map() -> dict[str, str]:
    """大小写折叠 → 原始标签，用于目录命中判定。"""
    return _state.casefold_map


def personality_options() -> frozenset[str]:
    """性格分类（key=personality）标签集合。"""
    return _state.personality


def category_rows() -> list[tuple[str, str, tuple[str, ...]]]:
    """按 sort 有序的 (key, label, options) 列表，供 tag-options 组装。"""
    return _state.category_rows


def reset_to_defaults() -> None:
    """恢复为常量目录（迁移未跑 / 表为空时的回退，也是测试隔离入口）。"""
    _state.reset_to_defaults()


def load_from_rows(
    categories: list[tuple[str, str]],
    tags_by_category: dict[str, list[str]],
) -> None:
    """从 (key,label) 分类与各分类有序标签重建缓存（同步入口，供测试/回填使用）。"""
    if not categories:
        reset_to_defaults()
        return
    _state.load(categories, tags_by_category)


async def reload(db: AsyncSession) -> None:
    """从 interest_tag_category / interest_tag 重建缓存。

    表缺失或未播种（分类为空）时回退常量目录，不抛错——保证迁移未执行的环境
    （含本地/测试）行为不变。调用方（启动钩子、后台写）无需再判空。
    """
    category_rows = (
        await db.execute(text(
            "SELECT `key`, label FROM interest_tag_category WHERE enabled = 1 ORDER BY sort ASC, id ASC"
        ))
    ).mappings().all()
    categories: list[tuple[str, str]] = [(str(row["key"]), str(row["label"])) for row in category_rows]
    if not categories:
        reset_to_defaults()
        return
    tag_rows = (
        await db.execute(text(
            "SELECT category_key, name FROM interest_tag WHERE enabled = 1 ORDER BY category_key ASC, sort ASC, id ASC"
        ))
    ).mappings().all()
    tags_by_category: dict[str, list[str]] = {}
    for row in tag_rows:
        tags_by_category.setdefault(str(row["category_key"]), []).append(str(row["name"]))
    _state.load(categories, tags_by_category)


def snapshot() -> dict[str, Any]:
    """当前缓存快照（供调试/断言，不参与业务判定）。"""
    return {
        "categories": [(key, label, list(options)) for key, label, options in _state.category_rows],
        "total_options": len(_state.all_options),
    }

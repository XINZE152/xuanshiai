"""回归测试：M4–M7 补列迁移不再静默失败。

背景（生产事故根因）
    提交 ``8ec8ff1`` 给 ``_ensure_m4_columns`` / ``_ensure_m5_columns`` /
    ``_ensure_m6_columns`` / ``_ensure_m7_columns`` 传入的列定义缺少 ``字段名`` 前缀，
    而 ``_ensure_table_columns`` 的模板是 ``ALTER TABLE ... ADD COLUMN {column_def}``，
    于是生成语法非法语句（MySQL 1064），异常又被 ``except`` 降级成 WARNING。
    结果这批列在旧库上从未被创建；生产 ``AUTO_INIT_DB=false``，修复后的初始化脚本
    从未在线上跑过，最终表现成接口 500：
        (1054, "Unknown column 'audit_status' in 'field list'")

本文件锁住三件事：
1. ``_ensure_table_columns`` 对缺前缀的列定义快速失败，不再静默跳过；
2. 四个 M4–M7 补列 helper 生成的 ALTER 语句列名合法；
3. 服务层 SELECT 的列必须能在建表定义里找到（同类漂移的通用防线）。
"""

import pathlib
import re

import pytest

from app.db.business_schema import BUSINESS_TABLES
from app.services import customer_lead_admin as lead_service
from database_setup_marriage import DatabaseManager

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


class _AllColumnsMissingCursor:
    """SHOW COLUMNS 返回空，令所有目标列都走“需要新增”的分支并记录 ALTER 语句。"""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str, params=None) -> None:  # noqa: ANN001
        self.statements.append(statement)

    def fetchall(self) -> list:
        return []

    def fetchone(self):
        return None

    @property
    def rowcount(self) -> int:
        return 0


def _added_columns(statements: list[str]) -> list[str]:
    columns: list[str] = []
    for statement in statements:
        match = re.search(r"ADD COLUMN\s+(.*)$", statement, re.IGNORECASE | re.DOTALL)
        if match:
            columns.append(match.group(1).strip())
    return columns


@pytest.mark.parametrize(
    "helper",
    ["_ensure_m4_columns", "_ensure_m5_columns", "_ensure_m6_columns", "_ensure_m7_columns"],
)
def test_backoffice_column_helpers_emit_named_alter(helper: str) -> None:
    """补列 helper 生成的每条 ALTER 都必须以反引号列名开头（本次事故的直接防线）。"""
    manager = DatabaseManager.__new__(DatabaseManager)
    cursor = _AllColumnsMissingCursor()

    getattr(manager, helper)(cursor)

    columns = _added_columns(cursor.statements)
    assert columns, f"{helper} 没有生成任何 ADD COLUMN 语句"
    for definition in columns:
        assert definition.startswith("`"), f"{helper} 生成了没有列名的 ALTER 片段: {definition!r}"


def test_m4_helper_covers_audit_status_and_promoter_id() -> None:
    manager = DatabaseManager.__new__(DatabaseManager)
    cursor = _AllColumnsMissingCursor()

    manager._ensure_m4_columns(cursor)

    columns = _added_columns(cursor.statements)
    assert any(item.startswith("`audit_status`") for item in columns)
    assert any(item.startswith("`promoter_id`") for item in columns)


def test_ensure_table_columns_rejects_definition_without_name_prefix() -> None:
    """缺 `字段名` 前缀的列定义必须立刻抛错，不能再被 except 吞成 WARNING。"""
    manager = DatabaseManager.__new__(DatabaseManager)
    cursor = _AllColumnsMissingCursor()

    with pytest.raises(ValueError, match="缺少 `audit_status` 前缀"):
        manager._ensure_table_columns(
            cursor, "`customer_lead`", {"audit_status": "varchar(16) NOT NULL DEFAULT 'active'"}
        )


def test_named_columns_adds_backtick_prefix() -> None:
    assert DatabaseManager._named_columns({"level_id": "tinyint unsigned NOT NULL DEFAULT 1"}) == {
        "level_id": "`level_id` tinyint unsigned NOT NULL DEFAULT 1"
    }


def test_customer_lead_select_columns_exist_in_create_table() -> None:
    """服务层 SELECT 的列必须都能在建表定义里找到（同一类漂移的通用检查）。"""
    ddl = BUSINESS_TABLES["customer_lead"]
    select_head = lead_service.LEAD_SELECT.split("FROM customer_lead", 1)[0]
    select_head = select_head.split("SELECT", 1)[1]

    parsed = []
    for item in select_head.split(","):
        name = item.strip()
        if not name or "(" in name or ")" in name or " " in name:
            continue
        parsed.append(name)

    assert "audit_status" in parsed
    missing = [name for name in parsed if f"`{name}`" not in ddl]
    assert not missing, f"customer_lead SELECT 引用了建表定义中不存在的列: {missing}"


def test_m4_m7_migration_scripts_are_paired() -> None:
    """定向迁移脚本必须 up/down 成对存在，且 up 覆盖本次事故的列。"""
    migration_dir = REPO_ROOT / "migrations" / "m4_m7"
    up = migration_dir / "20260916_01_m4_m7_backoffice_columns_up.sql"
    down = migration_dir / "20260916_01_m4_m7_backoffice_columns_down.sql"

    assert up.exists() and down.exists()
    up_sql = up.read_text(encoding="utf-8")
    assert "`audit_status`" in up_sql
    assert "`promoter_id`" in up_sql
    assert "group_concat_max_len" in up_sql, "长 ALTER 拼接必须放开 GROUP_CONCAT 上限"

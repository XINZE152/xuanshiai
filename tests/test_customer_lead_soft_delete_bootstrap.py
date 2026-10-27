"""customer_lead 软删除/来源字典 bootstrap 的回归测试（PR #65 CI 事故）。

背景：``DatabaseManager._ensure_customer_lead_soft_delete_and_source`` 用
``row[0]`` 读 ``information_schema.COLUMNS`` 的 ``GENERATION_EXPRESSION``，
但 ``initialize_database`` 建的是 ``pymysql.cursors.DictCursor``，字典行只能
按键取值：``row[0]`` 抛 ``KeyError(0)``，而 ``str(KeyError(0))`` 恰好是
``"0"``。于是整段迁移被记成阻断项 ``customer_lead 软删除列/来源字典表: 0``，
``_raise_on_schema_failures`` 在启动阶段直接 RuntimeError，AI 集成测试的
session 级 bootstrap 全部 ERROR（kx-learn/xuanshiai job 122250935037）。

本测试用假游标（复刻 DictCursor 的字典行）驱动该函数，锁死三点：
1. 字典行不得再被下标访问（否则这里会以 KeyError(0) 复现同样的 "0"）；
2. 生成列缺 deleted_at 时按新定义重建，并恢复唯一索引；
3. 旧库只有生成列、没有同名唯一索引时，不得执行 DROP INDEX（1091 会让
   整段迁移失败）。
"""

from __future__ import annotations

import re

import database_setup_marriage as setup

REBUILD_MARK = "GENERATED ALWAYS AS"


class _DictRowCursor:
    """复刻 ``pymysql.cursors.DictCursor``：fetchone() 返回字典。"""

    def __init__(
        self,
        expressions: dict[str, str],
        *,
        existing_unique_indexes: set[str],
        duplicate_columns: set[str] | None = None,
    ):
        self._expressions = expressions
        self._existing_unique_indexes = existing_unique_indexes
        self._duplicate_columns = duplicate_columns or set()
        self.executed: list[str] = []
        self._current: dict | None = None

    def execute(self, sql: str, params=None) -> None:
        statement = " ".join(sql.split())
        self.executed.append(statement)
        if statement.startswith("SHOW TABLES LIKE 'customer_lead'"):
            self._current = {"Tables_in_x": "customer_lead"}
            return
        if statement.startswith("SELECT GENERATION_EXPRESSION"):
            column = (params or ("",))[0]
            if column not in self._expressions:
                self._current = None
                return
            self._current = {"generation_expression": self._expressions[column]}
            return
        if statement.startswith("SHOW INDEX FROM `customer_lead`"):
            key_name = (params or ("",))[0]
            self._current = {"Key_name": key_name} if key_name in self._existing_unique_indexes else None
            return
        for column in ("active_phone", "active_wechat"):
            if statement.startswith(f"SELECT 1 FROM `customer_lead` WHERE `{column}` IS NOT NULL"):
                self._current = {"1": 1} if column in self._duplicate_columns else None
                return
        self._current = None

    def fetchone(self):
        return self._current


def _manager() -> setup.DatabaseManager:
    """绕过 ``__init__``（会连库建库），只保留失败台账。"""
    manager = setup.DatabaseManager.__new__(setup.DatabaseManager)
    manager._schema_failures = []
    # 两个辅助方法各自有自己的幂等逻辑与单测，这里只关心生成列判定与重建顺序。
    manager._ensure_table_columns = lambda *a, **k: None
    manager._ensure_optional_index = lambda *a, **k: None
    return manager


def _statements(cursor: _DictRowCursor, pattern: str) -> list[str]:
    return [sql for sql in cursor.executed if re.search(pattern, sql)]


def _failure_texts(manager: setup.DatabaseManager) -> list[str]:
    return [message for _blocking, message in manager._schema_failure_log()]


def test_dict_row_with_soft_delete_expression_is_a_noop() -> None:
    """新定义已含 deleted_at：只读判定，不重建、不记失败。"""

    expression = (
        "(case when ((`status` in (_utf8mb4'LOST',_utf8mb4'CLOSED')) "
        "or (`deleted_at` is not null)) then NULL else nullif(trim(`phone`),_utf8mb4'') end)"
    )
    cursor = _DictRowCursor(
        {"active_phone": expression, "active_wechat": expression},
        existing_unique_indexes={"uk_customer_lead_active_phone", "uk_customer_lead_active_wechat"},
    )
    manager = _manager()

    manager._ensure_customer_lead_soft_delete_and_source(cursor)

    assert manager._schema_failures == [], (
        "字典行必须按键取值；用 row[0] 会抛 KeyError(0) 并记为阻断项 "
        f"（错误文本就是 '0'），当前失败: {manager._schema_failures}"
    )
    assert _statements(cursor, r"^ALTER TABLE `customer_lead` (DROP|ADD COLUMN)") == []


def test_legacy_expression_is_rebuilt_with_unique_index_restored() -> None:
    """旧定义（不含 deleted_at）应删索引、删列、按新定义重建并恢复唯一索引。"""

    legacy = (
        "(case when (`status` in (_utf8mb4'LOST',_utf8mb4'CLOSED')) "
        "then NULL else nullif(trim(`phone`),_utf8mb4'') end)"
    )
    cursor = _DictRowCursor(
        {"active_phone": legacy, "active_wechat": legacy},
        existing_unique_indexes={"uk_customer_lead_active_phone", "uk_customer_lead_active_wechat"},
    )
    manager = _manager()

    manager._ensure_customer_lead_soft_delete_and_source(cursor)

    assert _failure_texts(manager) == []
    for column in ("active_phone", "active_wechat"):
        assert _statements(cursor, rf"DROP INDEX `uk_customer_lead_{column}`"), column
        assert _statements(cursor, rf"DROP COLUMN `{column}`"), column
        rebuilt = _statements(cursor, rf"ADD COLUMN `{column}`.*{REBUILD_MARK}.*deleted_at")
        assert len(rebuilt) == 1, f"{column} 未按含 deleted_at 的新定义重建: {cursor.executed}"
        assert _statements(cursor, rf"ADD UNIQUE KEY `uk_customer_lead_{column}`"), column


def test_legacy_expression_without_unique_index_skips_drop_index() -> None:
    """旧库只有生成列没有唯一索引时，DROP INDEX 会 1091，必须先探测。"""

    legacy = "(case when (`status` in (_utf8mb4'LOST')) then NULL else trim(`wechat`) end)"
    cursor = _DictRowCursor(
        {"active_phone": legacy, "active_wechat": legacy},
        existing_unique_indexes=set(),
    )
    manager = _manager()

    manager._ensure_customer_lead_soft_delete_and_source(cursor)

    assert _failure_texts(manager) == []
    assert _statements(cursor, r"DROP INDEX") == [], "索引不存在时不得下发 DROP INDEX"
    assert _statements(cursor, r"ADD UNIQUE KEY `uk_customer_lead_active_phone`")
    assert _statements(cursor, r"ADD UNIQUE KEY `uk_customer_lead_active_wechat`")


def test_duplicate_contacts_skip_unique_index_without_blocking_startup() -> None:
    """存量有重复有效联系方式时，建唯一索引必 1062。

    这类数据问题不能记成阻断项——否则测试服这种「有 2 条同号客源」的旧库
    一升级就拒绝启动。口径与 _ensure_customer_lead_contact_uniqueness 一致：
    重建生成列、跳过唯一索引、留 warning 提示跑去重脚本。
    """

    legacy = "(case when (`status` in (_utf8mb4'LOST')) then NULL else trim(`phone`) end)"
    cursor = _DictRowCursor(
        {"active_phone": legacy, "active_wechat": legacy},
        existing_unique_indexes=set(),
        duplicate_columns={"active_phone"},
    )
    manager = _manager()

    manager._ensure_customer_lead_soft_delete_and_source(cursor)

    assert _failure_texts(manager) == [], "重复数据不得升级为阻断项（会让服务拒绝启动）"
    # 生成列仍然按新定义重建，功能不受影响
    assert _statements(cursor, rf"ADD COLUMN `active_phone`.*{REBUILD_MARK}.*deleted_at")
    # 有重复的那列跳过唯一索引；没重复的列照常建
    assert _statements(cursor, r"ADD UNIQUE KEY `uk_customer_lead_active_phone`") == []
    assert _statements(cursor, r"ADD UNIQUE KEY `uk_customer_lead_active_wechat`")

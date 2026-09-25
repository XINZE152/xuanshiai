from __future__ import annotations

from typing import Any

import pytest

from database_setup_marriage import DatabaseManager


class SchemaCursor:
    def __init__(
        self,
        *,
        column_exists: bool,
        index_exists: bool,
        column_collation: str = "utf8mb4_bin",
    ) -> None:
        self.column_exists = column_exists
        self.index_exists = index_exists
        self.column_collation = column_collation
        self.statements: list[tuple[str, Any]] = []
        self._next_row: tuple[Any, ...] | None = None

    def execute(self, statement: str, params: Any = None) -> None:
        self.statements.append((statement, params))
        if statement.startswith("SHOW FULL COLUMNS FROM `chat_message`"):
            self._next_row = (
                ("client_message_id", "varchar(128)", self.column_collation)
                if self.column_exists
                else None
            )
        elif statement.startswith("SHOW INDEX FROM `chat_message`"):
            self._next_row = (
                ("uq_chat_message_sender_session_client_message",)
                if self.index_exists
                else None
            )

    def fetchone(self) -> tuple[Any, ...] | None:
        row = self._next_row
        self._next_row = None
        return row


@pytest.mark.parametrize(
    (
        "column_exists",
        "index_exists",
        "column_collation",
        "expected_add_column",
        "expected_modify_column",
        "expected_add_index",
    ),
    [
        (False, False, "utf8mb4_bin", True, False, True),
        (True, False, "utf8mb4_bin", False, False, True),
        (True, True, "utf8mb4_bin", False, False, False),
        (True, True, "utf8mb4_unicode_ci", False, True, False),
    ],
)
def test_legacy_chat_schema_upgrade_is_additive_and_repeatable(
    column_exists: bool,
    index_exists: bool,
    column_collation: str,
    expected_add_column: bool,
    expected_modify_column: bool,
    expected_add_index: bool,
) -> None:
    cursor = SchemaCursor(
        column_exists=column_exists,
        index_exists=index_exists,
        column_collation=column_collation,
    )
    manager = DatabaseManager.__new__(DatabaseManager)

    manager._ensure_chat_message_idempotency(cursor)

    statements = [statement for statement, _params in cursor.statements]
    add_column = [statement for statement in statements if "ADD COLUMN `client_message_id`" in statement]
    modify_column = [statement for statement in statements if "MODIFY COLUMN `client_message_id`" in statement]
    add_index = [statement for statement in statements if "ADD UNIQUE KEY" in statement]
    assert bool(add_column) is expected_add_column
    assert bool(modify_column) is expected_modify_column
    assert bool(add_index) is expected_add_index
    for column_ddl in add_column + modify_column:
        assert "varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL" in column_ddl
        assert "AFTER `media_url`" in column_ddl
    if add_index:
        assert "(`from_user_id`, `session_id`, `client_message_id`)" in add_index[0]


def test_new_chat_table_uses_case_sensitive_idempotency_keys() -> None:
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "database_setup_marriage.py").read_text(encoding="utf-8")
    table_sql = source.split('"chat_message": """', 1)[1].split('""",', 1)[0].lower()

    assert (
        "`client_message_id` varchar(128) character set utf8mb4 "
        "collate utf8mb4_bin default null"
    ) in table_sql
    assert (
        "unique key `uq_chat_message_sender_session_client_message` "
        "(`from_user_id`, `session_id`, `client_message_id`)"
    ) in table_sql


def test_chat_idempotency_migration_matches_schema_contract() -> None:
    from pathlib import Path

    migration_dir = Path(__file__).resolve().parents[1] / "migrations" / "chat"
    up_sql = (migration_dir / "20260923_01_chat_message_idempotency_up.sql").read_text(encoding="utf-8").lower()
    verify_sql = (migration_dir / "20260923_01_chat_message_idempotency_verify.sql").read_text(encoding="utf-8").lower()
    down_sql = (migration_dir / "20260923_01_chat_message_idempotency_down.sql").read_text(encoding="utf-8").lower()
    migration_docs = (migration_dir / "README.md").read_text(encoding="utf-8").lower()

    assert "collate utf8mb4_bin" in up_sql
    assert "coalesce(@chat_message_client_message_id_collation, '') <> 'utf8mb4_bin'" in up_sql
    assert "collation_name" in verify_sql
    assert "duplicate_scoped_client_message_keys" in verify_sql
    assert "drop column `client_message_id`" in down_sql
    assert "删除已经写入的 `client_message_id` 值" in migration_docs
    assert "`collation_name=utf8mb4_bin`" in migration_docs


def test_chat_schema_initializer_invokes_legacy_upgrade() -> None:
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "database_setup_marriage.py").read_text(encoding="utf-8")
    assert "self._ensure_chat_message_idempotency(cursor)" in source

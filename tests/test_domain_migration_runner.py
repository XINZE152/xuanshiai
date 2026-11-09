"""Contracts for the paper_plane/chat domain migration runner."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/manage_domain_migration.py"


def _load_script() -> Any:
    spec = importlib.util.spec_from_file_location("manage_domain_migration", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeCursor:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.queries: list[str] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self.queries.append(sql)

    def fetchall(self) -> list[dict[str, Any]]:
        return self.rows


def test_paper_plane_and_chat_manifests_are_executable() -> None:
    module = _load_script()
    paper = module._manifest("paper_plane")
    chat = module._manifest("chat")
    assert [item["version"] for item in paper][-1] == "20261109_01_unlock_cost_default"
    assert [item["version"] for item in chat] == [
        "20260923_01_chat_message_idempotency",
        "20261106_01_chat_message_hidden",
    ]
    assert all(item["verify_mode"] == "info" for item in chat)


def test_unknown_domain_is_rejected() -> None:
    module = _load_script()
    with pytest.raises(module.MigrationError):
        module._manifest("business")


def test_statement_splitter_rejects_delimiter(tmp_path: Path) -> None:
    module = _load_script()
    path = tmp_path / "bad.sql"
    path.write_text("DELIMITER //\nSELECT 1;\n", encoding="utf-8")
    with pytest.raises(module.MigrationError):
        module._statements(path)


def test_verify_empty_mode_rejects_rows_and_accepts_empty() -> None:
    module = _load_script()
    entry = {
        "version": "20261109_01_unlock_cost_default",
        "verify": "20261109_01_unlock_cost_default_verify.sql",
        "verify_mode": "empty",
    }
    assert module._run_verify(FakeCursor([]), "paper_plane", entry, "verify") == []
    with pytest.raises(module.MigrationError):
        module._run_verify(FakeCursor([{"issue": "bad"}]), "paper_plane", entry, "verify")


def test_verify_info_mode_runs_all_statements() -> None:
    module = _load_script()
    entry = {
        "version": "20261106_01_chat_message_hidden",
        "verify": "20261106_01_chat_message_hidden_verify.sql",
        "verify_mode": "info",
    }
    cursor = FakeCursor([{"column_name": "id"}])
    rows = module._run_verify(cursor, "chat", entry, "verify")
    assert len(rows) == 3
    assert len(cursor.queries) == 3


def test_selected_versions_filter_and_unknown_is_rejected() -> None:
    module = _load_script()
    versions = module._manifest("paper_plane")
    selected = module._selected(versions, ["20261109_01_unlock_cost_default"])
    assert [item["version"] for item in selected] == ["20261109_01_unlock_cost_default"]
    with pytest.raises(module.MigrationError):
        module._selected(versions, ["missing"])


def test_database_url_must_be_complete() -> None:
    module = _load_script()
    with pytest.raises(module.MigrationError):
        module._connection_config("development", "not-a-url")

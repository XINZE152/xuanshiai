"""Q23/Q45/Q63/Q74 release preflight contract."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check_release_schema.py"


def _load_script() -> Any:
    spec = importlib.util.spec_from_file_location("check_release_schema", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeCursor:
    def __init__(self, responses: dict[str, dict[str, Any]]) -> None:
        self._responses = responses
        self._current: dict[str, Any] | None = None

    def execute(self, sql: str, params: Any = None) -> None:
        self._current = None
        for marker, row in self._responses.items():
            if marker in sql:
                self._current = row
                return

    def fetchone(self) -> dict[str, Any] | None:
        return self._current


def test_collect_reports_all_four_release_gates() -> None:
    module = _load_script()
    cursor = FakeCursor(
        {
            "COLUMN_NAME = 'points_cost'": {"default_value": "50"},
            "CHECK_CONSTRAINTS": {"clause": "(`points_cost` in (50,80))"},
            "INFORMATION_SCHEMA.COLUMNS": {"present": 1},
            "INFORMATION_SCHEMA.TABLES": {"present": 1},
        }
    )
    results = module.collect(cursor)
    assert [item.name for item in results] == [
        "user_profile_completion.completion_grandfathered",
        "table community_post_remind",
        "table chat_message_hidden",
        "paper_plane_profile_unlock.points_cost",
    ]
    assert all(item.ok for item in results)


def test_unlock_cost_gate_rejects_legacy_default_or_check() -> None:
    module = _load_script()
    cursor = FakeCursor(
        {
            "COLUMN_NAME = 'points_cost'": {"default_value": "80"},
            "CHECK_CONSTRAINTS": {"clause": "(`points_cost` = 80)"},
        }
    )
    result = module._unlock_cost(cursor)
    assert result.ok is False
    assert "default=80" in result.detail
    assert "= 80" in result.detail


def test_missing_table_and_column_are_failures() -> None:
    module = _load_script()
    cursor = FakeCursor(
        {
            "COLUMN_NAME = 'points_cost'": {"default_value": "50"},
            "CHECK_CONSTRAINTS": {"clause": "(`points_cost` in (50,80))"},
            "INFORMATION_SCHEMA.COLUMNS": {"present": 0},
            "INFORMATION_SCHEMA.TABLES": {"present": 0},
        }
    )
    results = module.collect(cursor)
    assert results[0].ok is False
    assert results[1].ok is False
    assert results[2].ok is False


def test_database_url_must_be_complete() -> None:
    module = _load_script()
    try:
        module._connection_config("development", "not-a-url")
    except ValueError as exc:
        assert "mysql://" in str(exc)
    else:
        raise AssertionError("incomplete --database-url must be rejected")


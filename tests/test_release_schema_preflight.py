"""Release preflight contract, including Q7 settings write dependencies."""

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
    def __init__(
        self,
        responses: dict[str, dict[str, Any]],
        missing_columns: set[tuple[str, str]] | None = None,
    ) -> None:
        self._responses = responses
        self._missing_columns = missing_columns or set()
        self._current: dict[str, Any] | None = None

    def execute(self, sql: str, params: Any = None) -> None:
        self._current = None
        if "INFORMATION_SCHEMA.COLUMNS" in sql and params in self._missing_columns:
            self._current = {"present": 0}
            return
        for marker, row in self._responses.items():
            if marker in sql:
                self._current = row
                return

    def fetchone(self) -> dict[str, Any] | None:
        return self._current


def test_collect_reports_all_release_gates() -> None:
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
    # 4 项历史门禁 + 恋 AI BOT 门禁（20261117_01/02/03 + 20261118_01 timeline）：
    # aibot_user_assignment / aibot_session / aibot_turn / aibot_love_report /
    # aibot_timeline_message，以及 user_profile.bot_intake_json 列。
    assert [item.name for item in results] == [
        "user_profile_completion.completion_grandfathered",
        "table community_post_remind",
        "table chat_message_hidden",
        "paper_plane_profile_unlock.points_cost",
        "table aibot_user_assignment",
        "table aibot_session",
        "table aibot_turn",
        "table aibot_love_report",
        "table aibot_timeline_message",
        "table aibot_story_play",
        "table aibot_story_peer_shown",
        "user_profile.bot_intake_json",
    ] + [
        f"{table}.{column}"
        for table, columns in module._SETTINGS_WRITE_COLUMNS.items()
        for column in columns
    ]
    assert all(item.ok for item in results)


def test_settings_gate_reports_missing_write_dependencies() -> None:
    module = _load_script()
    missing = {
        ("user_privacy", "anonymous_browse_enabled"),
        ("user_privacy", "notify_apply"),
        ("user_revision_state", "privacy_revision"),
        ("derivation_outbox", "payload_minimal"),
    }
    cursor = FakeCursor(
        {
            "COLUMN_NAME = 'points_cost'": {"default_value": "50"},
            "CHECK_CONSTRAINTS": {"clause": "(`points_cost` in (50,80))"},
            "INFORMATION_SCHEMA.COLUMNS": {"present": 1},
            "INFORMATION_SCHEMA.TABLES": {"present": 1},
        },
        missing_columns=missing,
    )
    assert {item.name for item in module.collect(cursor) if not item.ok} == {
        f"{table}.{column}" for table, column in missing
    }


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

"""Q23 legacy completion-row backfill contract."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/business/20261109_01_profile_completion_missing_rows"
MARKER = "legacy-backfill-20261109"


def _read(suffix: str) -> str:
    return Path(f"{MIGRATION}_{suffix}.sql").read_text(encoding="utf-8")


def test_incremental_bootstrap_columns_include_grandfather_flag() -> None:
    source = (ROOT / "database_setup_marriage.py").read_text(encoding="utf-8")
    assert '"completion_grandfathered": "`completion_grandfathered` tinyint NOT NULL DEFAULT \'0\'' in source


def test_backfill_only_touches_missing_legacy_100_percent_users() -> None:
    up = _read("up")
    assert MARKER in up
    assert "data_complete_rate" in up
    assert ">= 100" in up
    assert "LEFT JOIN `user_profile_completion`" in up
    assert "c.`user_id` IS NULL" in up
    assert "INSERT INTO `user_profile_completion`" in up


def test_rollback_removes_only_unedited_backfill_rows() -> None:
    down = _read("down")
    assert MARKER in down
    assert "DELETE FROM `user_profile_completion`" in down
    assert "completion_grandfathered` = 1" in down


def test_verify_files_are_single_queries() -> None:
    for suffix in ("verify", "verify_previous"):
        sql = _read(suffix)
        assert sql.count(";") == 1, f"{suffix} must contain exactly one statement"


def test_manifest_registers_the_new_migration_with_checksums() -> None:
    manifest = json.loads(
        (ROOT / "migrations/business/manifest.json").read_text(encoding="utf-8")
    )
    entry = next(
        item
        for item in manifest["versions"]
        if item["version"] == "20261109_01_profile_completion_missing_rows"
    )
    assert entry["up"].endswith("_up.sql")
    assert entry["down"].endswith("_down.sql")
    assert entry["verify"].endswith("_verify.sql")
    assert entry["verify_previous"].endswith("_verify_previous.sql")
    assert all(len(entry["sha256"][key]) == 64 for key in ("up", "down", "verify", "verify_previous"))

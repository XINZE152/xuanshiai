"""Q45 price migration contract (50 new, 80 historical)."""

from __future__ import annotations

import json
from pathlib import Path

from app.services import paper_plane_unlock as service


ROOT = Path(__file__).resolve().parents[1]


def test_constant_is_50_and_constructor_rejects_old_price() -> None:
    assert service.PAPER_PLANE_PROFILE_UNLOCK_COST == 50
    try:
        service.PaperPlaneProfileUnlockService(store=object(), points_cost=80)
    except ValueError as exc:
        assert "50" in str(exc)
    else:
        raise AssertionError("80-point constructor must be rejected")


def test_migration_manifest_registers_widened_cost_check() -> None:
    manifest = json.loads((ROOT / "migrations/paper_plane/manifest.json").read_text(encoding="utf-8"))
    entry = next(item for item in manifest["versions"] if item["version"] == "20261106_01_unlock_cost")
    assert entry["verify"] == "20261106_01_unlock_cost_verify.sql"
    up = (ROOT / "migrations/paper_plane" / entry["up"]).read_text(encoding="utf-8")
    down = (ROOT / "migrations/paper_plane" / entry["down"]).read_text(encoding="utf-8")
    assert "CHECK (`points_cost` IN (50,80))" in up
    # Down must stay executable by the reviewed runner: no DELIMITER/procedure blocks.
    assert "DELIMITER" not in down
    assert "SIGNAL SQLSTATE" not in down
    assert "SET DEFAULT 80" in down


def test_default_migration_is_registered_and_executable() -> None:
    manifest = json.loads((ROOT / "migrations/paper_plane/manifest.json").read_text(encoding="utf-8"))
    entry = next(
        item
        for item in manifest["versions"]
        if item["version"] == "20261109_01_unlock_cost_default"
    )
    up = (ROOT / "migrations/paper_plane" / entry["up"]).read_text(encoding="utf-8")
    down = (ROOT / "migrations/paper_plane" / entry["down"]).read_text(encoding="utf-8")
    verify = (ROOT / "migrations/paper_plane" / entry["verify"]).read_text(encoding="utf-8")
    verify_previous = (ROOT / "migrations/paper_plane" / entry["verify_previous"]).read_text(
        encoding="utf-8"
    )
    assert "SET DEFAULT 50" in up
    assert "CHECK (`points_cost` IN (50,80))" in up
    assert "SET DEFAULT 80" in down
    assert "DELIMITER" not in up
    assert "DELIMITER" not in down
    assert "UNION ALL" in verify
    assert "UNION ALL" in verify_previous



def test_bootstrap_schema_allows_both_prices() -> None:
    schema = (ROOT / "app/db/business_schema.py").read_text(encoding="utf-8")
    assert "`points_cost` int unsigned NOT NULL DEFAULT 50" in schema
    assert "CHECK (`points_cost` IN (50,80))" in schema


def test_check_constraint_queries_join_table_constraints() -> None:
    # MySQL 8 INFORMATION_SCHEMA.CHECK_CONSTRAINTS has no TABLE_NAME column;
    # every lookup must join TABLE_CONSTRAINTS instead.
    for path in sorted((ROOT / "migrations/paper_plane").glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        if "CHECK_CONSTRAINTS" not in sql:
            continue
        assert "TABLE_CONSTRAINTS" in sql, f"{path.name} queries CHECK_CONSTRAINTS without the table join"

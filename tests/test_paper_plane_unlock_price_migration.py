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
    assert "points_cost` = 50" in down
    assert "SIGNAL SQLSTATE '45000'" in down
    assert "CHECK (`points_cost` = 80)" in down


def test_bootstrap_schema_allows_both_prices() -> None:
    schema = (ROOT / "app/db/business_schema.py").read_text(encoding="utf-8")
    assert "`points_cost` int unsigned NOT NULL DEFAULT 50" in schema
    assert "CHECK (`points_cost` IN (50,80))" in schema

"""Memory Projection Phase 2 migration contract tests (Task 2).

File-level contract (no database): the up/down migration pair must declare
exactly the two projection tables with the frozen constraints (tuple-unique
grant, version- and input-hash-unique projection, revoke/invalidation
columns, minimal JSON payload), stay registered in
``migrations/ai/manifest.json`` with LF-normalised sha256, and must never
touch Core memory tables or the legacy ``ai_feature_projection``.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_ROOT = REPO_ROOT / "migrations" / "ai"
UP_FILE = MIGRATION_ROOT / "20260905_02_memory_projection_up.sql"
DOWN_FILE = MIGRATION_ROOT / "20260905_02_memory_projection_down.sql"
MANIFEST_FILE = MIGRATION_ROOT / "manifest.json"
RUNNER_FILE = REPO_ROOT / "scripts" / "manage_ai_migration.py"

PROJECTION_TABLES = (
    "ai_memory_projection_grant",
    "ai_memory_projection",
)


def _read(path: Path) -> str:
    assert path.is_file(), f"missing migration file: {path}"
    return path.read_text(encoding="utf-8")


def _table_blocks(up_sql: str) -> dict[str, str]:
    blocks: dict[str, str] = {}
    for match in re.finditer(
        r"CREATE TABLE IF NOT EXISTS `(\w+)` \((.*?)\) ENGINE=\w+ DEFAULT CHARSET=\w+",
        up_sql,
        re.S,
    ):
        blocks[match.group(1)] = match.group(0)
    return blocks


def _table_order(up_sql: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r"CREATE TABLE IF NOT EXISTS `(\w+)`", up_sql)]


# ---------------------------------------------------------------------------
# 表清单与引擎
# ---------------------------------------------------------------------------


def test_up_declares_exactly_the_two_projection_tables() -> None:
    order = _table_order(_read(UP_FILE))
    assert set(order) == set(PROJECTION_TABLES), f"unexpected table set: {order}"


def test_grant_created_before_projection() -> None:
    order = _table_order(_read(UP_FILE))
    assert order.index("ai_memory_projection_grant") < order.index("ai_memory_projection")


def test_projection_tables_are_innodb_utf8mb4() -> None:
    blocks = _table_blocks(_read(UP_FILE))
    assert len(blocks) == len(PROJECTION_TABLES)
    for table, block in blocks.items():
        assert "ENGINE=InnoDB" in block, f"{table} must be InnoDB"
        assert "CHARSET=utf8mb4" in block, f"{table} must be utf8mb4"


@pytest.mark.parametrize("table", PROJECTION_TABLES)
def test_every_projection_table_is_owner_scoped(table: str) -> None:
    block = _table_blocks(_read(UP_FILE))[table]
    assert "`owner_user_id` bigint unsigned NOT NULL" in block


# ---------------------------------------------------------------------------
# grant 表约束
# ---------------------------------------------------------------------------


def test_grant_tuple_unique_and_revocable() -> None:
    block = _table_blocks(_read(UP_FILE))["ai_memory_projection_grant"]
    assert re.search(
        r"UNIQUE KEY `\w+` \(`owner_user_id`, `function_key`, `purpose`, `data_category`\)",
        block,
    ), "grant must be unique per owner+function+purpose+category"
    for column in (
        "`grant_id`",
        "`status`",
        "`consent_snapshot_id`",
        "`policy_revision`",
        "`granted_at`",
        "`revoked_at`",
    ):
        assert column in block, f"grant missing column {column}"


# ---------------------------------------------------------------------------
# projection 表约束
# ---------------------------------------------------------------------------


def test_projection_version_and_input_hash_unique() -> None:
    block = _table_blocks(_read(UP_FILE))["ai_memory_projection"]
    assert re.search(
        r"UNIQUE KEY `\w+` \(`owner_user_id`, `function_key`, `purpose`, "
        r"`data_category`, `projection_version`\)",
        block,
    ), "projection version must be unique per owner+function+purpose+category"
    assert re.search(
        r"UNIQUE KEY `\w+` \(`owner_user_id`, `function_key`, `purpose`, "
        r"`data_category`, `projection_input_hash`\)",
        block,
    ), "same input hash must not create a duplicate version (idempotent build)"


def test_projection_freeze_contract_columns() -> None:
    block = _table_blocks(_read(UP_FILE))["ai_memory_projection"]
    for column in (
        "`projection_id`",
        "`subject`",
        "`projection_version`",
        "`projection_input_hash`",
        "`status`",
        "`invalidated_at`",
        "`invalidated_reason`",
        "`entries_json`",
        "`policy_revision`",
        "`consent_snapshot_id`",
        "`built_at`",
    ):
        assert column in block, f"projection missing column {column}"
    assert re.search(r"`entries_json` json NOT NULL", block), (
        "projection payload must be a NOT NULL minimal JSON document"
    )


def test_projection_json_is_minimal_no_quote_or_transcript() -> None:
    up_sql = _read(UP_FILE)
    for banned in ("`transcript", "`source_quote`", "`raw_text`", "`full_quote`"):
        assert banned not in up_sql, f"banned column {banned} in projection migration"


def test_up_does_not_touch_existing_tables() -> None:
    up_sql = _read(UP_FILE)
    assert not re.search(r"\bALTER TABLE\b", up_sql, re.I), "up must not ALTER tables"
    assert not re.search(r"\bDROP TABLE\b", up_sql, re.I), "up must not DROP tables"
    for banned in ("ai_memory_event", "ai_memory_claim", "ai_feature_projection"):
        assert banned not in up_sql, f"up must not reference {banned}"


# ---------------------------------------------------------------------------
# down 迁移逆序回滚
# ---------------------------------------------------------------------------


def test_down_drops_projection_then_grant_only() -> None:
    down_sql = _read(DOWN_FILE)
    drops = [m.group(1) for m in re.finditer(r"DROP TABLE IF EXISTS `(\w+)`", down_sql)]
    assert drops == ["ai_memory_projection", "ai_memory_projection_grant"], (
        f"down must drop projection then grant only: {drops}"
    )


def test_down_does_not_touch_core_or_legacy_tables() -> None:
    down_sql = _read(DOWN_FILE).lower()
    for banned in (
        "ai_memory_event",
        "ai_memory_claim",
        "ai_memory_owner_sequence",
        "ai_feature_projection",
        "ai_profile_",
        "ai_task",
        "ai_consent",
    ):
        assert banned not in down_sql, f"down migration must not touch {banned}"


# ---------------------------------------------------------------------------
# manifest 登记与执行器门控
# ---------------------------------------------------------------------------

VERSION = "20260905_02_memory_projection"


def _lf_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def test_manifest_registers_projection_version() -> None:
    manifest = json.loads(_read(MANIFEST_FILE))
    versions = manifest.get("versions")
    assert isinstance(versions, list)
    entry = next((v for v in versions if v.get("version") == VERSION), None)
    assert entry is not None, f"manifest must register {VERSION}"
    assert entry["up"] == UP_FILE.name
    assert entry["down"] == DOWN_FILE.name
    assert entry["sha256"]["up"] == _lf_sha256(UP_FILE.read_bytes())
    assert entry["sha256"]["down"] == _lf_sha256(DOWN_FILE.read_bytes())


def test_manifest_requires_list_does_not_gain_projection_tables() -> None:
    manifest = json.loads(_read(MANIFEST_FILE))
    for table in PROJECTION_TABLES:
        assert table not in manifest.get("requires", [])


def test_migration_runner_verifies_projection_version() -> None:
    source = _read(RUNNER_FILE)
    assert "20260905_02" in source, (
        "manage_ai_migration._verify must gate on the projection version prefix"
    )
    for table in ("ai_memory_projection", "ai_memory_projection_grant"):
        assert table in source, f"verify rules must cover {table}"


def test_sql_files_split_into_statements_cleanly() -> None:
    from scripts.manage_ai_migration import _statements

    for path in (UP_FILE, DOWN_FILE):
        statements = _statements(path)
        assert statements, f"{path.name} produced no statements"
        for statement in statements:
            assert not statement.lstrip().startswith("--"), (
                f"{path.name} left a comment inside a statement: {statement[:60]}"
            )

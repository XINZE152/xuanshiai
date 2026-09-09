"""Memory Kernel Core v1 migration contract tests (Task 2).

File-level contract (no database): the reviewed up/down migration pair must
declare the seven memory-kernel tables with the frozen constraints, be
registered in ``migrations/ai/manifest.json`` with LF-normalised sha256, and
reverse cleanly in the down file.  Real-database up/down/idempotency is
covered later by ``tests/integration/ai/test_ai_memory_real_db.py``.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_ROOT = REPO_ROOT / "migrations" / "ai"
UP_FILE = MIGRATION_ROOT / "20260905_01_memory_kernel_core_up.sql"
DOWN_FILE = MIGRATION_ROOT / "20260905_01_memory_kernel_core_down.sql"
MANIFEST_FILE = MIGRATION_ROOT / "manifest.json"
RUNNER_FILE = REPO_ROOT / "scripts" / "manage_ai_migration.py"

MEMORY_TABLES = (
    "ai_memory_owner_sequence",
    "ai_memory_event",
    "ai_memory_observation",
    "ai_memory_claim",
    "ai_memory_insight",
    "ai_memory_state",
    "ai_memory_suppression",
)


def _read(path: Path) -> str:
    assert path.is_file(), f"missing migration file: {path}"
    return path.read_text(encoding="utf-8")


def _table_blocks(up_sql: str) -> dict[str, str]:
    """Map table name -> full CREATE TABLE statement block."""
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
# 表清单与建表顺序
# ---------------------------------------------------------------------------


def test_up_declares_exactly_the_seven_memory_tables() -> None:
    up_sql = _read(UP_FILE)
    order = _table_order(up_sql)
    assert set(order) == set(MEMORY_TABLES), f"unexpected table set: {order}"


def test_owner_sequence_is_created_before_event() -> None:
    order = _table_order(_read(UP_FILE))
    assert order.index("ai_memory_owner_sequence") < order.index("ai_memory_event"), (
        "owner sequence lock table must be created before the append-only event ledger"
    )


def test_all_memory_tables_are_innodb_utf8mb4() -> None:
    blocks = _table_blocks(_read(UP_FILE))
    assert len(blocks) == len(MEMORY_TABLES)
    for table, block in blocks.items():
        assert "ENGINE=InnoDB" in block, f"{table} must be InnoDB"
        assert "CHARSET=utf8mb4" in block, f"{table} must be utf8mb4"


# ---------------------------------------------------------------------------
# 事件账本约束
# ---------------------------------------------------------------------------


def test_event_table_frozen_columns_and_keys() -> None:
    block = _table_blocks(_read(UP_FILE))["ai_memory_event"]
    for column in (
        "`event_id`",
        "`owner_user_id`",
        "`server_seq`",
        "`subject`",
        "`namespace`",
        "`node_type`",
        "`event_type`",
        "`payload_json`",
        "`source_kind`",
        "`source_turn_id`",
        "`source_ref`",
        "`source_quote`",
        "`causal_event_ids_json`",
        "`consent_scope`",
        "`idempotency_key`",
        "`occurred_at`",
    ):
        assert column in block, f"ai_memory_event missing column {column}"
    assert re.search(r"`source_quote` varchar\(512\)", block)
    assert re.search(r"`payload_json` json NOT NULL", block)
    assert re.search(r"`causal_event_ids_json` json NOT NULL", block)
    assert re.search(r"UNIQUE KEY `\w+` \(`owner_user_id`, `server_seq`\)", block), (
        "owner+server_seq must be unique (per-owner ordered ledger)"
    )
    assert re.search(r"UNIQUE KEY `\w+` \(`owner_user_id`, `idempotency_key`\)", block), (
        "owner+idempotency_key must be unique (replay idempotency)"
    )


def test_event_table_declares_no_update_path() -> None:
    up_sql = _read(UP_FILE)
    assert not re.search(r"ON UPDATE CURRENT_TIMESTAMP`[^\n]*`server_seq", up_sql)
    # append-only 账本：up 迁移里不允许出现对事件的 UPDATE/DELETE 语句。
    statements = [s.strip() for s in up_sql.split(";")]
    for statement in statements:
        if re.match(r"^(UPDATE|DELETE)\b", statement, re.I):
            raise AssertionError(f"append-only up migration must not mutate rows: {statement[:80]}")


# ---------------------------------------------------------------------------
# 视图表约束
# ---------------------------------------------------------------------------


def test_claim_table_freeze_contract_columns() -> None:
    block = _table_blocks(_read(UP_FILE))["ai_memory_claim"]
    for column in (
        "`canonical_key`",
        "`status`",
        "`stability`",
        "`importance`",
        "`constraint_type`",
        "`importance_confirmed`",
    ):
        assert column in block, f"ai_memory_claim missing column {column}"
    assert re.search(
        r"UNIQUE KEY `\w+` \(`owner_user_id`, `subject`, `namespace`, `canonical_key`\)", block
    ), "claim must be canonical-unique per owner+subject+namespace"


def test_observation_table_carries_evidence_columns() -> None:
    block = _table_blocks(_read(UP_FILE))["ai_memory_observation"]
    for column in ("`confidence`", "`fact_kind`", "`source_kind`", "`source_quote`"):
        assert column in block, f"ai_memory_observation missing column {column}"


def test_insight_table_stores_claim_ids_not_transcript() -> None:
    block = _table_blocks(_read(UP_FILE))["ai_memory_insight"]
    assert re.search(r"`claim_ids_json` json NOT NULL", block)
    assert re.search(r"`summary` varchar\(200\)", block)


def test_state_table_requires_valid_until() -> None:
    block = _table_blocks(_read(UP_FILE))["ai_memory_state"]
    assert re.search(r"`valid_until` datetime(3)? NOT NULL", block), (
        "state.valid_until must be NOT NULL (TTL is mandatory)"
    )


def test_suppression_canonical_unique() -> None:
    block = _table_blocks(_read(UP_FILE))["ai_memory_suppression"]
    assert re.search(
        r"UNIQUE KEY `\w+` \(`owner_user_id`, `subject`, `namespace`, `canonical_key`\)", block
    ), "suppression must be unique per owner+subject+namespace+canonical_key"


def test_no_transcript_column_anywhere() -> None:
    up_sql = _read(UP_FILE)
    assert "transcript" not in up_sql.lower().replace("禁止 transcript", ""), (
        "memory tables must not carry transcript columns"
    )
    for banned in ("transcript_json", "transcript_text", "raw_text"):
        assert banned not in up_sql, f"banned column {banned} in memory migration"


# ---------------------------------------------------------------------------
# down 迁移逆序回滚
# ---------------------------------------------------------------------------


def test_down_drops_all_memory_tables_in_reverse_order() -> None:
    down_sql = _read(DOWN_FILE)
    drops = [
        m.group(1)
        for m in re.finditer(r"DROP TABLE IF EXISTS `(\w+)`", down_sql)
    ]
    expected = list(reversed(MEMORY_TABLES))
    assert drops == expected, f"down must drop in reverse creation order: {drops}"


def test_down_does_not_touch_legacy_business_tables() -> None:
    down_sql = _read(DOWN_FILE).lower()
    for banned in ("ai_profile_", "ai_task", "ai_consent", "derivation_", "ai_search"):
        assert banned not in down_sql, f"down migration must not touch {banned}"


# ---------------------------------------------------------------------------
# manifest 登记与校验门控
# ---------------------------------------------------------------------------

VERSION = "20260905_01_memory_kernel_core"


def _lf_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def test_manifest_registers_memory_kernel_version() -> None:
    manifest = json.loads(_read(MANIFEST_FILE))
    versions = manifest.get("versions")
    assert isinstance(versions, list)
    entry = next((v for v in versions if v.get("version") == VERSION), None)
    assert entry is not None, f"manifest must register {VERSION}"
    assert entry["up"] == UP_FILE.name
    assert entry["down"] == DOWN_FILE.name
    assert entry["sha256"]["up"] == _lf_sha256(UP_FILE.read_bytes())
    assert entry["sha256"]["down"] == _lf_sha256(DOWN_FILE.read_bytes())


def test_manifest_requires_list_does_not_gain_memory_tables() -> None:
    manifest = json.loads(_read(MANIFEST_FILE))
    for table in MEMORY_TABLES:
        assert table not in manifest.get("requires", []), (
            "memory tables must stay in versions' up SQL, not in requires "
            "(down then verify --expect previous would fail)"
        )


def test_migration_runner_verifies_memory_kernel_version() -> None:
    source = _read(RUNNER_FILE)
    assert "20260905_01" in source, "manage_ai_migration._verify must gate on the new version prefix"
    for table in ("ai_memory_event", "ai_memory_owner_sequence", "ai_memory_claim"):
        assert table in source, f"verify rules must cover {table}"


# ---------------------------------------------------------------------------
# 迁移执行器可加载两个 SQL 文件（语句切分不炸）
# ---------------------------------------------------------------------------


def test_sql_files_split_into_statements_cleanly() -> None:
    from scripts.manage_ai_migration import _statements

    for path in (UP_FILE, DOWN_FILE):
        statements = _statements(path)
        assert statements, f"{path.name} produced no statements"
        for statement in statements:
            assert not statement.lstrip().startswith("--"), (
                f"{path.name} left a comment inside a statement: {statement[:60]}"
            )


@pytest.mark.parametrize("table", MEMORY_TABLES)
def test_every_memory_table_has_owner_scoping(table: str) -> None:
    block = _table_blocks(_read(UP_FILE))[table]
    assert "`owner_user_id` bigint unsigned NOT NULL" in block, (
        f"{table} must be owner-scoped"
    )

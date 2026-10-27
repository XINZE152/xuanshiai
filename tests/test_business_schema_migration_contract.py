"""Business schema lifecycle migration contract tests."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.db.business_schema import BUSINESS_TABLES
from scripts.manage_business_migration import (
    MANIFEST_PATH,
    MIGRATION_ROOT,
    MigrationError,
    _checksum,
    _database_config_for_target,
    _manifest,
    _run,
)


def _manifest_data() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _expected(name: str) -> str:
    data = _manifest_data()
    entry = next(item for item in data["versions"] if item["version"] == "20261027_01_core_schema_gaps")
    return entry["sha256"][name]


def test_manifest_checksums_are_lf_normalised() -> None:
    manifest = _manifest()
    assert manifest["requires"] == [
        "users",
        "user_auth",
        "chat_session",
        "user_profile",
        "user_privacy",
        "user_report",
        "user_login_log",
        "payment_order",
        "user_boost",
        "user_points",
        "user_membership",
    ]
    assert [item["version"] for item in manifest["versions"]] == [
        "20261027_01_core_schema_gaps",
        "20261027_02_account_security_gaps",
        "20261027_03_profile_payment_gaps",
        "20261027_04_fk_delete_policy",
    ]
    for entry in manifest["versions"]:
        for direction in ("up", "down", "verify", "verify_previous"):
            sql_path = MIGRATION_ROOT / str(entry[direction])
            actual = hashlib.sha256(sql_path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            assert actual == entry["sha256"][direction], f"{entry['version']}.{direction} checksum mismatch"


def test_gap_migrations_cover_all_bootstrap_only_columns() -> None:
    """每个版本都能幂等补列，且 verify 文件是单条查询。"""
    from scripts.schema_contract import bootstrap_only_columns

    expected = {
        "20261027_02_account_security_gaps": {"users", "user_auth", "user_privacy", "user_report", "user_login_log"},
        "20261027_03_profile_payment_gaps": {"user_profile", "payment_order", "user_boost"},
    }
    covered: set[tuple[str, str]] = set()
    already = {
        ("users", "deleted_at"),
        ("user_auth", "id_card_hash"),
        ("user_auth", "realname_status"),
        ("chat_session", "user1_pinned_at"),
        ("chat_session", "user2_pinned_at"),
    }
    for version, tables in expected.items():
        up = (MIGRATION_ROOT / f"{version}_up.sql").read_text(encoding="utf-8")
        verify = (MIGRATION_ROOT / f"{version}_verify.sql").read_text(encoding="utf-8")
        down = (MIGRATION_ROOT / f"{version}_down.sql").read_text(encoding="utf-8")
        for table in tables:
            assert f"TABLE_NAME = '{table}'" in up
            for column, _ in bootstrap_only_columns()[table]:
                if (table, column) in already:
                    continue
                assert f"'{column}' AS column_name" in up, f"{table}.{column} missing from {version}_up.sql"
                assert f"'{column}' AS column_name" in verify, f"{table}.{column} missing from {version}_verify.sql"
                covered.add((table, column))
        assert verify.count(";") == 1
        assert "DROP COLUMN `" in down
    assert covered == {item for item in covered if item not in already}
    assert len(covered) + len(already) == 101
    assert len(covered) == 96


def test_core_gap_up_sql_declares_required_schema_objects() -> None:
    sql = (MIGRATION_ROOT / "20261027_01_core_schema_gaps_up.sql").read_text(encoding="utf-8")
    for expected in (
        "`deleted_at`",
        "`id_card_hash`",
        "`realname_status`",
        "`user1_pinned_at`",
        "`user2_pinned_at`",
        "idx_users_status_deleted",
        "idx_user_auth_id_card_realname",
    ):
        assert expected in sql


def test_core_gap_down_sql_is_reversible_and_guarded() -> None:
    sql = (MIGRATION_ROOT / "20261027_01_core_schema_gaps_down.sql").read_text(encoding="utf-8")
    assert "DROP INDEX `idx_users_status_deleted`" in sql
    assert "DROP INDEX `idx_user_auth_id_card_realname`" in sql
    assert "DROP COLUMN `" in sql
    assert "执行前必须备份" in sql


def test_fk_delete_policy_is_restrict_for_money_and_report_tables() -> None:
    """P0-2：金额/积分/会员/举报证据一律 RESTRICT，bootstrap 与迁移必须同源。"""
    from database_setup_marriage import FK_DEFAULT_ON_DELETE, FK_DELETE_POLICY

    expected = {
        ("payment_order", "user_id"),
        ("user_points", "user_id"),
        ("user_membership", "user_id"),
        ("user_report", "user_id"),
        ("user_report", "target_user_id"),
        ("report_appeal", "appellant_user_id"),
    }
    assert set(FK_DELETE_POLICY) == expected
    assert set(FK_DELETE_POLICY.values()) == {"RESTRICT"}
    assert FK_DEFAULT_ON_DELETE == "CASCADE"

    sql = (MIGRATION_ROOT / "20261027_04_fk_delete_policy_up.sql").read_text(encoding="utf-8")
    for table, column in expected:
        name = f"fk_{table}_{column}"
        assert name in sql
        assert "ON DELETE RESTRICT" in sql
    assert sql.count("ON DELETE RESTRICT") == len(expected)


def test_paper_plane_profile_unlock_is_registered_in_business_tables() -> None:
    ddl = BUSINESS_TABLES["paper_plane_profile_unlock"]
    assert "CREATE TABLE IF NOT EXISTS `paper_plane_profile_unlock`" in ddl
    assert "uk_paper_plane_unlock_viewer_key" in ddl
    assert "fk_paper_plane_unlock_viewer" in ddl
    assert "ck_paper_plane_unlock_cost" in ddl


def test_business_migration_sql_uses_lf_checkout_rule() -> None:
    content = (MIGRATION_ROOT.parents[1] / ".gitattributes").read_text(encoding="utf-8")
    assert "migrations/business/*.sql text eol=lf" in content


def test_documented_runner_invocation_needs_no_pythonpath() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "scripts/manage_business_migration.py", "--help"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Manage reviewed business schema migrations" in result.stdout


def test_runner_refuses_down_without_explicit_allow_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BUSINESS_MIGRATION_ALLOW_DOWN", raising=False)
    with pytest.raises(MigrationError, match="refusing down"):
        _run("down", "test")


def test_test_target_refuses_development_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BUSINESS_TEST_DATABASE_URL", raising=False)
    with pytest.raises(MigrationError, match="BUSINESS_TEST_DATABASE_URL is required"):
        _database_config_for_target("test")


def test_checksum_rejects_tampered_sql(tmp_path: Path) -> None:
    path = tmp_path / "migration.sql"
    path.write_bytes(b"SELECT 1;\n")
    digest = hashlib.sha256(b"SELECT 1;\n").hexdigest()
    _checksum(path, digest)
    path.write_bytes(b"SELECT 1;\r\n")
    _checksum(path, digest)
    path.write_bytes(b"SELECT 2;\n")
    with pytest.raises(MigrationError, match="checksum mismatch"):
        _checksum(path, digest)

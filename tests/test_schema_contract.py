"""Schema contract parser/diff tests (no database required)."""

from __future__ import annotations

from scripts.schema_contract import (
    bootstrap_only_columns,
    diff,
    normalize_type,
    parse_code_schema,
)


def test_normalize_type_unifies_int_width_and_constraints() -> None:
    assert normalize_type("bigint(20) unsigned") == "bigint unsigned"
    assert normalize_type("`balance` int(11) NOT NULL DEFAULT '0'") == "int"
    assert normalize_type("decimal(12,2) NOT NULL DEFAULT 0.00") == "decimal(12,2)"
    assert normalize_type("varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin") == "varchar(32)"
    assert normalize_type("enum('a','b')") == "enum('a','b')"


def test_code_schema_covers_every_table_with_a_primary_key() -> None:
    schema = parse_code_schema()
    # 257 基线 + 微信支付改造 5 张表（payment_refund / payment_fulfillment_job /
    # payment_notify_log / payment_reconcile_batch / payment_reconcile_diff）。
    assert len(schema) == 262
    assert all(spec["has_primary_key"] for spec in schema.values()), "所有表都应有主键"
    assert "paper_plane_profile_unlock" in schema
    assert "payment_refund" in schema
    assert schema["users"]["columns"]["id"] == "bigint unsigned"


def test_bootstrap_only_columns_exclude_base_ddl_columns() -> None:
    gaps = bootstrap_only_columns()
    total = sum(len(columns) for columns in gaps.values())
    assert total == 101
    assert "deleted_at" in {name for name, _ in gaps["users"]}
    assert "realname_status" in {name for name, _ in gaps["user_auth"]}
    # 基础 DDL 已有的列不应算作补列。
    assert all(name not in {"id", "created_at"} for name, _ in gaps["users"])


def test_diff_reports_missing_columns_types_and_indexes() -> None:
    expected = {
        "users": {
            "columns": {"id": "bigint unsigned", "status": "tinyint"},
            "indexes": [{"name": "idx_users_status", "unique": False, "columns": ["status"]}],
            "has_primary_key": True,
            "source": "code",
        }
    }
    actual = {
        "users": {
            "columns": {"id": "int", "extra": "varchar(8)"},
            "indexes": [],
            "has_primary_key": False,
            "source": "database",
        }
    }
    report = diff(expected, actual)
    assert report["missing_tables"] == []
    assert report["missing_columns"] == ["users.status"]
    assert report["type_mismatches"] == ["users.id: expected bigint unsigned, got int"]
    assert report["missing_indexes"] == ["users.idx_users_status"]
    assert report["missing_primary_keys"] == ["users"]
    assert report["extra_columns"] == ["users.extra"]


def test_diff_flags_missing_tables_only_for_expected_ones() -> None:
    expected = {"users": {"columns": {}, "indexes": [], "has_primary_key": True, "source": "code"}}
    report = diff(expected, {})
    assert report["missing_tables"] == ["users"]
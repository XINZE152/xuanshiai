"""D-8 回归：活动报名删除改为软删除 + 审计 + 已支付/已签到拦截。"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
ROUTE_SOURCE = (ROOT / "app/api/routes/activity_admin.py").read_text(encoding="utf-8")
MIGRATION_DIR = ROOT / "migrations/m4_m7"


def test_signup_delete_no_longer_hard_deletes() -> None:
    assert 'DELETE FROM activity_signup WHERE id = :id' not in ROUTE_SOURCE
    assert "deleted_at = UTC_TIMESTAMP(), deleted_by = :actor" in ROUTE_SOURCE


def test_signup_delete_writes_audit_snapshot() -> None:
    assert "'activity_signup.delete'" in ROUTE_SOURCE
    assert "before_json" in ROUTE_SOURCE
    assert "_snapshot_json(snapshot)" in ROUTE_SOURCE


def test_signup_delete_blocks_paid_and_checked_in() -> None:
    assert 'str(snapshot.get("pay_status") or "") == "paid"' in ROUTE_SOURCE
    assert 'int(snapshot.get("checked_in") or 0) == 1' in ROUTE_SOURCE
    assert "该报名已支付，不能删除，请走退款流程" in ROUTE_SOURCE
    assert "该报名已签到，不能删除" in ROUTE_SOURCE


def test_signup_queries_exclude_soft_deleted_rows() -> None:
    """列表 / 统计 / 导出 / 详情 / 修改都必须过滤已软删除记录。"""
    assert 'where = ["1=1", "s.deleted_at IS NULL"]' in ROUTE_SOURCE
    assert 'where = ["s.activity_id = :activity_id", "s.deleted_at IS NULL"]' in ROUTE_SOURCE
    assert 'where = "WHERE 1=1 AND deleted_at IS NULL"' in ROUTE_SOURCE
    assert "WHERE s.id = :id AND s.deleted_at IS NULL" in ROUTE_SOURCE
    assert "WHERE id = :id AND deleted_at IS NULL" in ROUTE_SOURCE


def test_activity_delete_is_blocked_when_paid_signups_exist() -> None:
    assert "pay_status = 'paid' OR COALESCE(checked_in, 0) = 1" in ROUTE_SOURCE
    assert "不能删除，请先处理退款或撤销签到" in ROUTE_SOURCE


def test_soft_delete_columns_are_ensured_and_migrated() -> None:
    setup_source = (ROOT / "database_setup_marriage.py").read_text(encoding="utf-8")
    assert '"deleted_at": "datetime DEFAULT NULL COMMENT' in setup_source
    assert '"deleted_by": "bigint unsigned DEFAULT NULL COMMENT' in setup_source
    assert 'idx_activity_signup_deleted' in setup_source

    up = (MIGRATION_DIR / "20261004_01_activity_signup_soft_delete_up.sql").read_text(encoding="utf-8")
    down = (MIGRATION_DIR / "20261004_01_activity_signup_soft_delete_down.sql").read_text(
        encoding="utf-8"
    )
    assert "`deleted_at` datetime DEFAULT NULL" in up
    assert "`deleted_by` bigint unsigned DEFAULT NULL" in up
    assert "idx_activity_signup_deleted" in up
    assert "DROP INDEX `idx_activity_signup_deleted`" in down
    assert "AS ord, 'deleted_at' AS column_name" in down
    assert "UNION ALL SELECT 2, 'deleted_by'" in down

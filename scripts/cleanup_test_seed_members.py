"""清理 ``users.is_test_seed=1`` 的测试种子账号（默认软删除，不动真实会员）。

用法::

    # 预览（不写库）
    python scripts/cleanup_test_seed_members.py --dry-run

    # 本地/测试：软删除（status=3 + deleted_at）
    python scripts/cleanup_test_seed_members.py --confirm

    # 生产需显式放行
    python scripts/cleanup_test_seed_members.py --confirm --allow-production

安全约定：
    - 只匹配 ``is_test_seed=1``，绝不按手机号盲删。
    - 默认软删除；``--hard`` 会删除种子用户行及其常见附属表行（不可恢复）。
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


ROOT = Path(__file__).resolve().parents[1]

# 常见附属表：user_id 指向种子用户时一并清理（硬删）。顺序：子表先、users 最后。
_HARD_CHILD_TABLES: tuple[str, ...] = (
    "user_media",
    "user_profile",
    "user_preference",
    "user_privacy",
    "user_auth",
    "user_profile_completion",
    "user_registration_intent",
    "user_role",
    "user_agreement_acceptance",
    "user_points",
    "user_task",
    "user_checkin",
    "user_membership",
    "user_wallet",
    "user_quota",
    "user_quota_usage",
    "user_boost",
    "user_favorite",
    "user_view_history",
    "user_notification",
    "user_recommendation",
)


def _connect() -> Any:
    import pymysql
    from database_setup_marriage import get_db_config

    return pymysql.connect(
        **get_db_config(),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def _assert_environment(*, allow_production: bool) -> str:
    env = (os.getenv("ENV") or os.getenv("ENVIRONMENT") or "development").lower()
    if env in {"development", "testing"}:
        return env
    allow = allow_production or os.getenv("XUANSHIAI_ALLOW_SEED_PRODUCTION", "").strip() in {
        "1", "true", "yes", "on",
    }
    if allow and env in {"production", "prod", "staging"}:
        return env
    raise RuntimeError(
        "清理测试种子默认只允许 development/testing；"
        "生产/预发请加 --allow-production 或设置 XUANSHIAI_ALLOW_SEED_PRODUCTION=1"
    )


def _table_exists(cur: Any, table: str) -> bool:
    cur.execute(
        """SELECT COUNT(*) AS c FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME=%s""",
        (table,),
    )
    row = cur.fetchone()
    return int(row["c"] if isinstance(row, dict) else row[0]) > 0


def _column_exists(cur: Any, table: str, column: str) -> bool:
    cur.execute(
        """SELECT COUNT(*) AS c FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s""",
        (table, column),
    )
    row = cur.fetchone()
    return int(row["c"] if isinstance(row, dict) else row[0]) > 0


def list_test_seed_users(cur: Any) -> list[dict[str, Any]]:
    if not _column_exists(cur, "users", "is_test_seed"):
        return []
    cur.execute(
        """SELECT id, phone, nickname, status, is_test_seed, created_at
        FROM users WHERE is_test_seed=1 ORDER BY id"""
    )
    return [dict(row) for row in cur.fetchall()]


def cleanup_test_seed_members(
    *,
    confirm: bool = False,
    hard: bool = False,
    dry_run: bool = False,
    allow_production: bool = False,
    connection: Any = None,
) -> dict[str, Any]:
    env = _assert_environment(allow_production=allow_production)
    owned = connection is None
    if owned:
        connection = _connect()
    cur = connection.cursor()
    result: dict[str, Any] = {
        "environment": env,
        "mode": "dry_run" if dry_run or not confirm else ("hard" if hard else "soft"),
        "users": [],
        "deleted_child_rows": {},
        "soft_deleted": 0,
        "hard_deleted_users": 0,
    }
    try:
        users = list_test_seed_users(cur)
        result["users"] = [
            {"id": int(u["id"]), "phone": u["phone"], "nickname": u["nickname"], "status": u["status"]}
            for u in users
        ]
        if not users:
            return result
        if dry_run or not confirm:
            return result

        ids = [int(u["id"]) for u in users]
        placeholders = ",".join(["%s"] * len(ids))

        if hard:
            for table in _HARD_CHILD_TABLES:
                if not _table_exists(cur, table):
                    continue
                # 只删带 user_id 的表；表结构不符则跳过
                if not _column_exists(cur, table, "user_id"):
                    continue
                cur.execute(
                    f"DELETE FROM `{table}` WHERE user_id IN ({placeholders})",
                    ids,
                )
                result["deleted_child_rows"][table] = int(cur.rowcount)
            cur.execute(f"DELETE FROM users WHERE is_test_seed=1 AND id IN ({placeholders})", ids)
            result["hard_deleted_users"] = int(cur.rowcount)
        else:
            if _column_exists(cur, "users", "deleted_at"):
                cur.execute(
                    f"""UPDATE users SET status=3, deleted_at=UTC_TIMESTAMP(), updated_at=UTC_TIMESTAMP()
                    WHERE is_test_seed=1 AND id IN ({placeholders})""",
                    ids,
                )
            else:
                cur.execute(
                    f"""UPDATE users SET status=3, updated_at=UTC_TIMESTAMP()
                    WHERE is_test_seed=1 AND id IN ({placeholders})""",
                    ids,
                )
            result["soft_deleted"] = int(cur.rowcount)

        connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        cur.close()
        if owned:
            connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="清理 is_test_seed=1 的测试种子账号")
    parser.add_argument("--dry-run", action="store_true", help="只列出将处理的账号，不写库")
    parser.add_argument("--confirm", action="store_true", help="确认执行清理（缺省等同 dry-run）")
    parser.add_argument("--hard", action="store_true", help="硬删除种子用户及常见附属行（不可恢复）")
    parser.add_argument(
        "--allow-production",
        action="store_true",
        help="允许在 production/staging 执行",
    )
    args = parser.parse_args()
    out = cleanup_test_seed_members(
        confirm=args.confirm,
        hard=args.hard,
        dry_run=args.dry_run or not args.confirm,
        allow_production=args.allow_production,
    )
    print(json.dumps(out, ensure_ascii=False, default=str))

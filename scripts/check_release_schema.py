#!/usr/bin/env python
"""Assert release-critical schema objects introduced by Q23/Q45/Q63/Q74.

This is a database-backed preflight, not a migration runner. Run it after the
reviewed migration SQL has been applied and before starting the service:

    python scripts/check_release_schema.py --target development
    python scripts/check_release_schema.py --target test
    python scripts/check_release_schema.py --database-url mysql://... --allow-production

Exit codes:
    0  every gate is present
    1  at least one gate is missing or inconsistent
    2  configuration or connection error (including an unapproved remote URL)
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit

import pymysql
from pymysql.cursors import DictCursor

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from database_setup_marriage import get_db_config  # noqa: E402
from manage_business_migration import _database_config_for_target  # noqa: E402


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


def _connection_config(target: str, database_url: str | None) -> dict[str, Any]:
    if database_url:
        parsed = urlsplit(database_url.replace("mysql+aiomysql://", "mysql://", 1))
        if parsed.scheme != "mysql" or not parsed.hostname or not parsed.port:
            raise ValueError("--database-url must be a complete mysql://host:port/database URL")
        expected_database = unquote(parsed.path.lstrip("/"))
        previous = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = database_url
        try:
            config = get_db_config()
        finally:
            if previous is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous
        if str(config.get("database")) != expected_database:
            raise ValueError("--database-url did not resolve to the requested database")
        return config
    return _database_config_for_target(target)


def _column_present(cursor: Any, table: str, column: str) -> CheckResult:
    cursor.execute(
        "SELECT COUNT(*) AS present FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (table, column),
    )
    row = cursor.fetchone() or {}
    present = int(row.get("present") or 0)
    return CheckResult(
        f"{table}.{column}",
        bool(present),
        "present" if present else "missing",
    )


def _table_present(cursor: Any, table: str) -> CheckResult:
    cursor.execute(
        "SELECT COUNT(*) AS present FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (table,),
    )
    row = cursor.fetchone() or {}
    present = int(row.get("present") or 0)
    return CheckResult(
        f"table {table}",
        bool(present),
        "present" if present else "missing",
    )


def _unlock_cost(cursor: Any) -> CheckResult:
    cursor.execute(
        "SELECT COLUMN_DEFAULT AS default_value FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'paper_plane_profile_unlock' "
        "AND COLUMN_NAME = 'points_cost'"
    )
    column = cursor.fetchone()
    cursor.execute(
        "SELECT cc.CHECK_CLAUSE AS clause FROM INFORMATION_SCHEMA.CHECK_CONSTRAINTS cc "
        "JOIN INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc "
        "ON tc.CONSTRAINT_SCHEMA = cc.CONSTRAINT_SCHEMA "
        "AND tc.CONSTRAINT_NAME = cc.CONSTRAINT_NAME "
        "WHERE cc.CONSTRAINT_SCHEMA = DATABASE() "
        "AND tc.TABLE_NAME = 'paper_plane_profile_unlock' "
        "AND cc.CONSTRAINT_NAME = 'ck_paper_plane_unlock_cost'"
    )
    constraint = cursor.fetchone()
    default_value = None if column is None else str(column.get("default_value"))
    clause = "" if constraint is None else str(constraint.get("clause") or "")
    ok = column is not None and default_value == "50" and "50" in clause
    return CheckResult(
        "paper_plane_profile_unlock.points_cost",
        ok,
        f"default={default_value or 'NULL'} check={clause or 'missing'}",
    )


def collect(cursor: Any) -> list[CheckResult]:
    return [
        _column_present(cursor, "user_profile_completion", "completion_grandfathered"),
        _table_present(cursor, "community_post_remind"),
        _table_present(cursor, "chat_message_hidden"),
        _unlock_cost(cursor),
    ]


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check Q23/Q45/Q63/Q74 release-critical schema objects"
    )
    parser.add_argument(
        "--target",
        choices=("development", "test"),
        default="development",
        help="development uses DATABASE_URL; test requires BUSINESS_TEST_DATABASE_URL",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="explicit database URL; requires --allow-production",
    )
    parser.add_argument(
        "--allow-production",
        action="store_true",
        help="allow --database-url so a production preflight can be run deliberately",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.database_url and not args.allow_production:
        print(
            "release schema check: refusing --database-url without --allow-production",
            file=sys.stderr,
        )
        return 2

    try:
        config = _connection_config(args.target, args.database_url)
        connection = pymysql.connect(**config, cursorclass=DictCursor, autocommit=True)
    except Exception as exc:  # pragma: no cover - connection failures are environment-dependent
        print(
            f"release schema check: ERROR: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2

    try:
        with connection.cursor() as cursor:
            results = collect(cursor)
    except Exception as exc:
        print(
            f"release schema check: ERROR: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2
    finally:
        connection.close()

    for item in results:
        print(f"{'PASS' if item.ok else 'FAIL'} {item.name}: {item.detail}")
    failed = [item for item in results if not item.ok]
    print(
        f"release schema check: {'OK' if not failed else 'FAIL'} "
        f"checks={len(results)} failed={len(failed)}"
    )
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())

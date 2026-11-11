#!/usr/bin/env python
"""One-off reconciliation for profile completion (profile-v5, 27 weighted items).

This script does not change the schema. It recomputes every user's
``user_profile_completion.score`` and the mirror ``users.data_complete_rate``
with the current 27-item model, while preserving the Q23 grandfather flag for
users who already have it.

Run from the repository root:

    python scripts/recalculate_profile_completion.py --target development --dry-run
    python scripts/recalculate_profile_completion.py --target test --limit 100
    python scripts/recalculate_profile_completion.py --target test
    python scripts/recalculate_profile_completion.py --database-url mysql://... --target production --allow-production

Exit codes:
    0  reconciliation finished without failures
    1  at least one user failed
    2  configuration / connection error
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Recompute profile completion scores with the frozen profile-v5 rules."
    )
    parser.add_argument(
        "--target",
        choices=("development", "test", "production"),
        default="development",
        help="Logical target used for safety checks and database URL resolution.",
    )
    parser.add_argument(
        "--database-url",
        default="",
        help="Explicit mysql:// or mysql+aiomysql:// URL. Required for production.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Only report changes; never write.")
    parser.add_argument("--batch-size", type=int, default=500, help="Users fetched per batch.")
    parser.add_argument("--user-id", type=int, default=0, help="Only process one user id.")
    parser.add_argument("--limit", type=int, default=0, help="Stop after N users (0 = all).")
    parser.add_argument(
        "--allow-production",
        action="store_true",
        help="Required when the target or environment is production.",
    )
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be >= 1")
    if args.limit < 0:
        parser.error("--limit must be >= 0")
    if args.user_id < 0:
        parser.error("--user-id must be >= 0")
    return args


def _async_url(target: str, database_url: str) -> str:
    if database_url:
        raw = database_url.replace("mysql+aiomysql://", "mysql://", 1)
        parsed = urlsplit(raw)
        if parsed.scheme != "mysql" or not parsed.hostname or not parsed.port:
            raise ValueError("--database-url must be mysql://host:port/database")
        database = parsed.path.lstrip("/")
        if not database:
            raise ValueError("--database-url must include a database name")
        return (
            f"mysql+aiomysql://{quote(parsed.username or '', safe='')}:"
            f"{quote(parsed.password or '', safe='')}@{parsed.hostname}:{parsed.port}/"
            f"{quote(database, safe='')}?charset=utf8mb4"
        )

    if target == "test":
        sys.path.insert(0, str(ROOT / "scripts"))
        from manage_business_migration import _database_config_for_target

        config = _database_config_for_target("test")
    elif target == "development":
        from database_setup_marriage import get_db_config

        config = get_db_config()
    else:
        raise ValueError(
            "--database-url is required for --target production; "
            "refusing to guess a production database"
        )
    return (
        f"mysql+aiomysql://{quote(str(config.get('user') or ''), safe='')}:"
        f"{quote(str(config.get('password') or ''), safe='')}@"
        f"{config.get('host')}:{config.get('port')}/{quote(str(config.get('database') or ''), safe='')}"
        "?charset=utf8mb4"
    )


def _configure_environment(args: argparse.Namespace) -> None:
    if args.target == "production" and not args.allow_production:
        raise ValueError("production requires --allow-production")
    if args.target == "production" and not args.database_url:
        raise ValueError("production requires --database-url")
    if args.allow_production and not args.database_url:
        raise ValueError("--allow-production requires an explicit --database-url")
    os.environ["DATABASE_URL"] = _async_url(args.target, args.database_url)


async def _user_ids(session, args: argparse.Namespace) -> list[int]:
    from sqlalchemy import text

    if args.user_id:
        return [args.user_id]
    ids: list[int] = []
    last_id = 0
    remaining = args.limit
    while True:
        batch = min(args.batch_size, remaining) if remaining else args.batch_size
        rows = (
            await session.execute(
                text("SELECT id FROM users WHERE id > :last_id ORDER BY id LIMIT :batch"),
                {"last_id": last_id, "batch": batch},
            )
        ).mappings().all()
        if not rows:
            break
        ids.extend(int(row["id"]) for row in rows)
        last_id = int(rows[-1]["id"])
        if remaining:
            remaining -= len(rows)
            if remaining <= 0:
                break
    return ids


async def _run(args: argparse.Namespace) -> int:
    from sqlalchemy import text

    from app.db.session import session_factory
    from app.services.profile import (
        _COMPLETION_ROW_SELECT,
        _completion_score,
        _completion_state,
        recalculate_completion_state,
    )

    if session_factory is None:
        print("database driver unavailable: install project runtime dependencies", file=sys.stderr)
        return 2

    scanned = changed = unchanged = grandfathered = failed = 0
    score_buckets = {"zero": 0, "middle": 0, "full": 0}

    async with session_factory() as session:
        ids = await _user_ids(session, args)
        print(f"target={args.target} dry_run={args.dry_run} users={len(ids)}")
        for index, user_id in enumerate(ids, start=1):
            try:
                row = (
                    await session.execute(text(_COMPLETION_ROW_SELECT), {"id": user_id})
                ).mappings().first()
                if not row:
                    continue
                scanned += 1
                is_grandfathered = bool(row.get("completion_grandfathered"))
                completed = _completion_state(row, legacy=is_grandfathered)
                new_score = _completion_score(completed)
                old_score = float(row.get("completion_score") or 0)
                if is_grandfathered:
                    grandfathered += 1
                if abs(new_score - old_score) > 0.001:
                    changed += 1
                else:
                    unchanged += 1
                if new_score <= 0:
                    score_buckets["zero"] += 1
                elif new_score >= 100:
                    score_buckets["full"] += 1
                else:
                    score_buckets["middle"] += 1
                if not args.dry_run:
                    await recalculate_completion_state(
                        session, user_id, clear_grandfathered=False
                    )
                    await session.commit()
                if index % args.batch_size == 0:
                    print(
                        f"progress={index}/{len(ids)} scanned={scanned} "
                        f"changed={changed} failed={failed}"
                    )
            except Exception as exc:  # noqa: BLE001 - one bad row must not stop the batch
                failed += 1
                await session.rollback()
                print(f"FAILED user_id={user_id}: {exc}", file=sys.stderr)

    print(
        "summary "
        f"scanned={scanned} changed={changed} unchanged={unchanged} "
        f"grandfathered={grandfathered} failed={failed} "
        f"zero={score_buckets['zero']} middle={score_buckets['middle']} "
        f"full={score_buckets['full']}"
    )
    if args.dry_run:
        mirror = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM users u "
                    "JOIN user_profile_completion c ON c.user_id = u.id "
                    "WHERE ABS(u.data_complete_rate - c.score) > 0.001"
                )
            )
        ).scalar()
        print(f"mirror_mismatch={int(mirror or 0)}")
        print("dry-run: no rows were written")
    return 1 if failed else 0


def main() -> int:
    args = _parse_args()
    try:
        _configure_environment(args)
    except Exception as exc:  # noqa: BLE001 - configuration errors must exit 2 without a traceback
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    try:
        return asyncio.run(_run(args))
    except Exception as exc:  # noqa: BLE001 - surface configuration/connection errors
        print(f"fatal: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

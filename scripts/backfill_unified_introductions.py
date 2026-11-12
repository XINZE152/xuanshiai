"""Backfill legacy match applications; run only while writes are stopped.

Set INTRODUCTION_MIGRATION_DATABASE_URL explicitly. Dry run is the default;
--apply requires expected source/target counts recorded immediately before cutover.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.introduction_backfill import backfill_admin_introductions  # noqa: E402


async def run(args: argparse.Namespace) -> None:
    url = os.environ.get("INTRODUCTION_MIGRATION_DATABASE_URL")
    if not url or not url.startswith(("mysql+asyncmy://", "mysql+aiomysql://")):
        raise SystemExit("INTRODUCTION_MIGRATION_DATABASE_URL must explicitly name a MySQL database")
    if args.apply and (args.expect_applications is None or args.expect_introductions is None):
        raise SystemExit("--apply requires both expected counts")
    engine = create_async_engine(url, pool_pre_ping=True)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            locked = await db.scalar(text("SELECT GET_LOCK('xsa_introduction_backfill', 5)"))
            if locked != 1:
                raise RuntimeError("Could not acquire backfill lock")
            try:
                # Lock existing rows and index gaps during the count and migration.
                await db.execute(text("SELECT id FROM match_apply ORDER BY id FOR UPDATE"))
                await db.execute(text("SELECT id FROM matchmaker_introduction ORDER BY id FOR UPDATE"))
                applications = int(await db.scalar(text("SELECT COUNT(*) FROM match_apply")) or 0)
                introductions = int(await db.scalar(text("SELECT COUNT(*) FROM matchmaker_introduction")) or 0)
                if args.expect_applications is not None and applications != args.expect_applications:
                    raise RuntimeError("Application count changed; recheck before applying")
                if args.expect_introductions is not None and introductions != args.expect_introductions:
                    raise RuntimeError("Introduction count changed; recheck before applying")
                result = await backfill_admin_introductions(db, apply=args.apply)
                if args.apply:
                    await db.commit()
                else:
                    await db.rollback()
                print(f"mode={'apply' if args.apply else 'dry-run'} introductions_before={introductions} "
                      f"applications={result['applications']} linked={result['linked']} missing={result['missing']} deleted={result['deleted']}")
            except BaseException:
                await db.rollback()
                raise
            finally:
                await db.execute(text("SELECT RELEASE_LOCK('xsa_introduction_backfill')"))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expect-applications", type=int)
    parser.add_argument("--expect-introductions", type=int)
    asyncio.run(run(parser.parse_args()))

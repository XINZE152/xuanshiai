"""Backfill legacy profile data into the memory kernel (Memory Kernel Core v1).

Fixed legacy mapping rules — the script never guesses a source kind from
confidence, and never copies any transcript:

- ``ai_profile_candidate`` (active/promoted)        -> proposed Observation
  with ``source_kind=inferred`` and
  ``idempotency_key=legacy:ai_profile_candidate:<id>``;
- ``ai_profile_revision_field`` (published history)  -> confirmed Claim
  (claim_proposed + claim_confirmed events) with
  ``idempotency_key=legacy:ai_profile_revision_field:<id>``.

Unknown subjects/dimensions are skipped and counted; active tombstones reject
re-extraction; reruns are resumable because the idempotency key is stable per
legacy primary key.

Usage::

    python scripts/backfill_ai_memory.py --database-url $AI_DATABASE_URL --batch-size 200 --dry-run
    python scripts/backfill_ai_memory.py --database-url $AI_DATABASE_URL --batch-size 200 --resume-from 0

Output lines only ever contain read/created/skipped/rejected counters and
rejection reasons — never user content.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.ai_schema import PROFILE_DIMENSION_SET  # noqa: E402
from app.schemas.ai_memory import (  # noqa: E402
    MEMORY_SUBJECTS,
    MemoryEventInput,
    MemoryEventType,
    MemoryNodeType,
    MemorySourceKind,
)
from app.services.ai.memory.ledger import MemoryLedger  # noqa: E402
from app.services.ai.memory.policy import MemoryPolicy, MemoryPolicyDenied  # noqa: E402
from app.services.ai.memory.service import MemoryService  # noqa: E402

LEGACY_IDEMPOTENCY_PREFIX = "legacy:{table}:{pk}"

_CANDIDATE_SELECT = (
    "SELECT id, candidate_id, user_id, subject, profile_dimension, field_kind, "
    "field_key, category, content, value_json, confidence, source_span, status, "
    "content_hash FROM ai_profile_candidate "
    "WHERE id >= :resume_from AND user_id > 0 AND status IN ('active', 'promoted') "
    "ORDER BY id LIMIT :batch"
)
_REVISION_FIELD_SELECT = (
    "SELECT rf.id AS id, rf.revision_id AS revision_id, r.user_id AS user_id, "
    "r.subject AS subject, rf.profile_dimension AS profile_dimension, "
    "rf.field_kind AS field_kind, rf.field_key AS field_key, rf.category AS category, "
    "rf.content AS content, rf.value_json AS value_json, rf.confidence AS confidence, "
    "rf.content_hash AS content_hash FROM ai_profile_revision_field rf "
    "INNER JOIN ai_profile_revision r ON r.id = rf.revision_id "
    "WHERE rf.id >= :resume_from AND r.user_id > 0 "
    "ORDER BY rf.id LIMIT :batch"
)


def candidate_identity(row: dict[str, Any]) -> str:
    """旧表行 → identity（与 Shadow Write 同一规则，防漂移）。"""

    return MemoryPolicy.candidate_identity(
        str(row.get("field_kind") or ""),
        row.get("field_key"),
        row.get("category"),
        row.get("content_hash"),
    )


def _loads(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _reject(stats: dict[str, Any], reason: str) -> None:
    stats["rejected"][reason] = stats["rejected"].get(reason, 0) + 1


async def _backfill_candidate(
    db: AsyncSession, row: dict[str, Any], *, dry_run: bool, stats: dict[str, Any]
) -> None:
    subject = str(row["subject"])
    if subject not in MEMORY_SUBJECTS:
        _reject(stats, "unknown_subject")
        return
    dimension = row["profile_dimension"]
    if dimension is not None and str(dimension) not in PROFILE_DIMENSION_SET:
        _reject(stats, "unknown_dimension")
        return
    idempotency_key = LEGACY_IDEMPOTENCY_PREFIX.format(
        table="ai_profile_candidate", pk=int(row["id"])
    )
    service = MemoryService(db)
    ledger = MemoryLedger(db)
    if await ledger.find_by_idempotency(int(row["user_id"]), idempotency_key):
        stats["skipped"] += 1
        return
    identity = candidate_identity(row)
    canonical_key = MemoryPolicy.canonical_key(
        subject, str(dimension) if dimension else None, identity
    )
    tombstone_row = await service.read_suppression(
        owner_user_id=int(row["user_id"]),
        subject=subject,
        namespace="moxiang",
        canonical_key=canonical_key,
    )
    if tombstone_row is not None and str(tombstone_row["status"]) == "active":
        _reject(stats, "suppressed")
        return
    if dry_run:
        stats["created"] += 1
        return
    await service.propose(
        owner_user_id=int(row["user_id"]),
        subject=subject,
        canonical_key=canonical_key,
        dimension=str(dimension) if dimension else None,
        value=_loads(row.get("value_json"))
        if row.get("value_json") is not None
        else row.get("content"),
        confidence=float(row.get("confidence") or 0.0),
        source_kind=MemorySourceKind.INFERRED.value,
        fact_kind="about_user" if subject == "personal" else "partner_preference",
        source_quote=(str(row["source_span"])[:512] if row.get("source_span") else None),
        source_ref=f"legacy-candidate:{row['candidate_id']}",
        idempotency_key=idempotency_key,
    )
    stats["created"] += 1


async def _backfill_revision_field(
    db: AsyncSession, row: dict[str, Any], *, dry_run: bool, stats: dict[str, Any]
) -> None:
    subject = str(row["subject"])
    if subject not in MEMORY_SUBJECTS:
        _reject(stats, "unknown_subject")
        return
    dimension = row["profile_dimension"]
    if dimension is None or str(dimension) not in PROFILE_DIMENSION_SET:
        _reject(stats, "unknown_dimension")
        return
    idempotency_key = LEGACY_IDEMPOTENCY_PREFIX.format(
        table="ai_profile_revision_field", pk=int(row["id"])
    )
    ledger = MemoryLedger(db)
    if await ledger.find_by_idempotency(int(row["user_id"]), idempotency_key):
        stats["skipped"] += 1
        return
    identity = candidate_identity(row)
    canonical_key = MemoryPolicy.canonical_key(subject, str(dimension), identity)
    value = _loads(row.get("value_json")) if row.get("value_json") is not None else row.get("content")
    confirm_key = f"{idempotency_key}:confirm"
    if dry_run:
        stats["created"] += 1
        return
    await ledger.append(
        MemoryEventInput(
            owner_user_id=int(row["user_id"]),
            subject=subject,  # type: ignore[arg-type]
            node_type=MemoryNodeType.CLAIM,
            event_type=MemoryEventType.CLAIM_PROPOSED,
            payload={
                "canonical_key": canonical_key,
                "dimension": str(dimension),
                "value": value,
                "confidence": float(row.get("confidence") or 0.0)
                if row.get("confidence") is not None
                else None,
                "stability": MemoryPolicy.derive_stability(MemorySourceKind.USER_CONFIRMED, 1.0),
                "importance": 0.5,
                "importance_confirmed": False,
                "fact_kind": "about_user" if subject == "personal" else "partner_preference",
            },
            source_kind=MemorySourceKind.USER_CONFIRMED,
            source_ref=f"legacy-revision:{row['revision_id']}",
            idempotency_key=idempotency_key,
        )
    )
    await ledger.append(
        MemoryEventInput(
            owner_user_id=int(row["user_id"]),
            subject=subject,  # type: ignore[arg-type]
            node_type=MemoryNodeType.CLAIM,
            event_type=MemoryEventType.CLAIM_CONFIRMED,
            payload={
                "canonical_key": canonical_key,
                "dimension": str(dimension),
                "value": value,
                "confidence": float(row.get("confidence") or 0.0)
                if row.get("confidence") is not None
                else None,
                "stability": MemoryPolicy.derive_stability(MemorySourceKind.USER_CONFIRMED, 1.0),
                "importance": 0.5,
                "constraint_type": None,
                "importance_confirmed": True,
                "fact_kind": "about_user" if subject == "personal" else "partner_preference",
            },
            source_kind=MemorySourceKind.USER_CONFIRMED,
            source_ref=f"legacy-revision:{row['revision_id']}",
            idempotency_key=confirm_key,
        )
    )
    stats["created"] += 1


async def run_backfill(
    db: AsyncSession,
    *,
    batch_size: int,
    dry_run: bool,
    resume_from: int,
) -> dict[str, Any]:
    """扫描旧表并回填；返回 read/created/skipped/rejected 计数（不含用户内容）。"""

    stats: dict[str, Any] = {"read": 0, "created": 0, "skipped": 0, "rejected": {}}
    # 两张 legacy 表的主键序列各自独立；cursor 必须分别从调用方的
    # resume_from 起步，不能共用 candidate 循环推进后的值，否则
    # revision_field 表里 id 较小的行会被整段跳过（真实库回归钉住）。
    # 1) legacy candidates -> proposed observations
    candidate_cursor = resume_from
    while True:
        rows = (
            await db.execute(
                text(_CANDIDATE_SELECT),
                {"resume_from": candidate_cursor, "batch": batch_size},
            )
        ).mappings().all()
        if not rows:
            break
        for row in rows:
            stats["read"] += 1
            await _backfill_candidate(db, dict(row), dry_run=dry_run, stats=stats)
            candidate_cursor = max(candidate_cursor, int(row["id"]) + 1)
        if len(rows) < batch_size:
            break
    # 2) legacy confirmed revision fields -> confirmed claims
    revision_cursor = resume_from
    while True:
        rows = (
            await db.execute(
                text(_REVISION_FIELD_SELECT),
                {"resume_from": revision_cursor, "batch": batch_size},
            )
        ).mappings().all()
        if not rows:
            break
        for row in rows:
            stats["read"] += 1
            await _backfill_revision_field(db, dict(row), dry_run=dry_run, stats=stats)
            revision_cursor = max(revision_cursor, int(row["id"]) + 1)
        if len(rows) < batch_size:
            break
    await db.commit()
    return stats


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill legacy AI profile data into the memory kernel"
    )
    parser.add_argument("--database-url", required=True, help="SQLAlchemy async database URL")
    parser.add_argument("--batch-size", type=int, default=200, help="scan batch size")
    parser.add_argument("--dry-run", action="store_true", help="count would-be writes only")
    parser.add_argument("--resume-from", type=int, default=0, help="resume after legacy id")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    async def _run() -> dict[str, Any]:
        engine = create_async_engine(args.database_url, pool_pre_ping=True)
        try:
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as db:
                return await run_backfill(
                    db,
                    batch_size=args.batch_size,
                    dry_run=args.dry_run,
                    resume_from=args.resume_from,
                )
        finally:
            await engine.dispose()

    try:
        stats = asyncio.run(_run())
    except MemoryPolicyDenied as exc:
        print(f"backfill policy denied: {exc}", file=sys.stderr)
        return 1
    mode = "dry-run" if args.dry_run else "apply"
    print(f"backfill mode={mode} read={stats['read']} created={stats['created']} skipped={stats['skipped']}")
    for reason, count in sorted(stats["rejected"].items()):
        print(f"backfill rejected reason={reason} count={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

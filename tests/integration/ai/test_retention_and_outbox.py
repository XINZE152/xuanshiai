"""Real-DB retention lifecycle checks for transcripts, audit, and outbox.

The dedicated integration fixture intentionally fails when MySQL is unavailable;
retention must never be silently skipped in a unit-only fallback.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.services.ai.memory.derivations import run_retention_cleanup
from app.workers import ai_worker
from tests.integration.ai.conftest import TEST_DATABASE_NAME

_OWNER = 9_876_543_821
_PREFIX = "retention-task9-"
_RETENTION_INDEXES = {
    "voice_transcript": {"idx_voice_transcript_retention": ("created_at", "id")},
    "ai_generation_audit": {
        "idx_ai_generation_audit_retention_batch": ("created_at", "id")
    },
    "derivation_outbox": {
        "idx_derivation_outbox_retention_succeeded": (
            "status",
            "occurred_at",
            "event_id",
        ),
        "idx_derivation_outbox_retention_dead_letter": (
            "status",
            "dead_letter_at",
            "event_id",
        ),
    },
}


async def _index_columns(
    db: AsyncSession, table_name: str
) -> dict[str, tuple[str, ...]]:
    rows = await db.execute(
        text(
            # MySQL 8 的 information_schema 列元数据固定返回大写，必须用
            # 显式别名把结果标签钉回小写，mappings() 才能按小写键取值。
            "SELECT index_name AS index_name, column_name AS column_name "
            "FROM information_schema.statistics "
            "WHERE table_schema = :schema AND table_name = :table "
            "ORDER BY index_name, seq_in_index"
        ),
        {"schema": TEST_DATABASE_NAME, "table": table_name},
    )
    indexes: dict[str, list[str]] = {}
    for row in rows.mappings():
        indexes.setdefault(str(row["index_name"]), []).append(str(row["column_name"]))
    return {name: tuple(columns) for name, columns in indexes.items()}


@pytest_asyncio.fixture
async def session_factory(
    real_db_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(real_db_engine, expire_on_commit=False)


async def _clear_seed_rows(db: AsyncSession) -> None:
    await db.execute(
        text("DELETE FROM voice_transcript WHERE task_id LIKE :prefix"),
        {"prefix": f"{_PREFIX}%"},
    )
    await db.execute(
        text("DELETE FROM ai_generation_audit WHERE request_id LIKE :prefix"),
        {"prefix": f"{_PREFIX}%"},
    )
    await db.execute(
        text("DELETE FROM derivation_consumer_receipt WHERE event_id LIKE :prefix"),
        {"prefix": f"{_PREFIX}%"},
    )
    await db.execute(
        text("DELETE FROM derivation_outbox WHERE event_id LIKE :prefix"),
        {"prefix": f"{_PREFIX}%"},
    )
    await db.commit()


async def _seed_outbox(
    db: AsyncSession,
    *,
    event_id: str,
    status: str,
    occurred_at: datetime,
    dead_letter_at: datetime | None = None,
) -> None:
    await db.execute(
        text(
            "INSERT INTO derivation_outbox "
            "(event_id, aggregate_type, aggregate_id, event_type, changed_fields, "
            "source_revision_json, occurred_at, priority, published_at, status, dead_letter_at) "
            "VALUES (:event_id, 'ai_memory', :owner, 'memory_projection', :changed, "
            ":revision, :occurred_at, 50, :occurred_at, :status, :dead_letter_at)"
        ),
        {
            "event_id": event_id,
            "owner": _OWNER,
            "changed": json.dumps([]),
            "revision": json.dumps({}),
            "occurred_at": occurred_at,
            "status": status,
            "dead_letter_at": dead_letter_at,
        },
    )
    if status in {"succeeded", "dead_letter"}:
        await db.execute(
            text(
                "INSERT INTO derivation_consumer_receipt "
                "(event_id, consumer_name, event_type, outcome, duration_ms, processed_at) "
                "VALUES (:event_id, 'cleanup', 'memory_projection', :outcome, 0, :processed_at)"
            ),
            {
                "event_id": event_id,
                "outcome": "processed" if status == "succeeded" else "dead_letter",
                "processed_at": dead_letter_at or occurred_at,
            },
        )


@pytest.mark.asyncio
async def test_retention_cleanup_keeps_live_outbox_and_purges_expired_terminal_data(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Each category is bounded; pending/processing work remains consumer-owned."""
    now = datetime.now(UTC).replace(tzinfo=None)
    old = now - timedelta(hours=73)
    fresh = now - timedelta(hours=1)
    old_dead_letter = now - timedelta(hours=49)
    async with session_factory() as db:
        await _clear_seed_rows(db)
        await db.execute(
            text(
                "INSERT INTO voice_transcript "
                "(task_id, owner_user_id, transcript, created_at) "
                "VALUES (:old_task, :owner, 'expired', :old), "
                "(:old_task_2, :owner, 'expired-2', :old), "
                "(:fresh_task, :owner, 'fresh', :fresh)"
            ),
            {
                "old_task": f"{_PREFIX}transcript-old",
                "old_task_2": f"{_PREFIX}transcript-old-2",
                "fresh_task": f"{_PREFIX}transcript-fresh",
                "owner": _OWNER,
                "old": old,
                "fresh": fresh,
            },
        )
        await db.execute(
            text(
                "INSERT INTO ai_generation_audit "
                "(request_id, scene, provider, created_at) "
                "VALUES (:old_id, 'retention', 'mock', :old), "
                "(:old_id_2, 'retention', 'mock', :old), "
                "(:fresh_id, 'retention', 'mock', :fresh)"
            ),
            {
                "old_id": f"{_PREFIX}audit-old",
                "old_id_2": f"{_PREFIX}audit-old-2",
                "fresh_id": f"{_PREFIX}audit-fresh",
                "old": old,
                "fresh": fresh,
            },
        )
        await _seed_outbox(
            db,
            event_id=f"{_PREFIX}succeeded-old",
            status="succeeded",
            occurred_at=old,
        )
        await _seed_outbox(
            db,
            event_id=f"{_PREFIX}succeeded-fresh",
            status="succeeded",
            occurred_at=fresh,
        )
        await _seed_outbox(
            db,
            event_id=f"{_PREFIX}succeeded-old-2",
            status="succeeded",
            occurred_at=old,
        )
        await _seed_outbox(
            db,
            event_id=f"{_PREFIX}dead-old",
            status="dead_letter",
            occurred_at=old,
            dead_letter_at=old_dead_letter,
        )
        await _seed_outbox(
            db,
            event_id=f"{_PREFIX}dead-old-2",
            status="dead_letter",
            occurred_at=old,
            dead_letter_at=old_dead_letter,
        )
        await _seed_outbox(
            db,
            event_id=f"{_PREFIX}pending-old",
            status="pending",
            occurred_at=old,
        )
        await _seed_outbox(
            db,
            event_id=f"{_PREFIX}processing-old",
            status="processing",
            occurred_at=old,
        )
        await db.commit()

    stats = await run_retention_cleanup(
        session_factory,
        now=now,
        batch_size=1,
        voice_transcript_retention_hours=48,
        outbox_succeeded_retention_hours=48,
        outbox_dead_letter_retention_hours=24,
        generation_audit_retention_hours=48,
    )

    assert stats.voice_transcripts == 1
    assert stats.generation_audits == 1
    assert stats.outbox_succeeded == 1
    assert stats.outbox_dead_letters == 1

    stats = await run_retention_cleanup(
        session_factory,
        now=now,
        batch_size=1,
        voice_transcript_retention_hours=48,
        outbox_succeeded_retention_hours=48,
        outbox_dead_letter_retention_hours=24,
        generation_audit_retention_hours=48,
    )
    assert stats.voice_transcripts == 1
    assert stats.generation_audits == 1
    assert stats.outbox_succeeded == 1
    assert stats.outbox_dead_letters == 1

    async with session_factory() as db:
        transcripts = await db.execute(
            text("SELECT task_id FROM voice_transcript WHERE task_id LIKE :prefix"),
            {"prefix": f"{_PREFIX}%"},
        )
        assert {row[0] for row in transcripts} == {f"{_PREFIX}transcript-fresh"}
        audits = await db.execute(
            text("SELECT request_id FROM ai_generation_audit WHERE request_id LIKE :prefix"),
            {"prefix": f"{_PREFIX}%"},
        )
        assert {row[0] for row in audits} == {f"{_PREFIX}audit-fresh"}
        outbox = await db.execute(
            text("SELECT event_id FROM derivation_outbox WHERE event_id LIKE :prefix"),
            {"prefix": f"{_PREFIX}%"},
        )
        assert {row[0] for row in outbox} == {
            f"{_PREFIX}succeeded-fresh",
            f"{_PREFIX}pending-old",
            f"{_PREFIX}processing-old",
        }
        receipts = await db.execute(
            text("SELECT event_id FROM derivation_consumer_receipt WHERE event_id LIKE :prefix"),
            {"prefix": f"{_PREFIX}%"},
        )
        assert {row[0] for row in receipts} == {f"{_PREFIX}succeeded-fresh"}
        await _clear_seed_rows(db)


@pytest.mark.asyncio
async def test_real_bootstrap_has_retention_indexes_and_cleanup_explain_uses_them(
    real_db_session: AsyncSession,
) -> None:
    """Fresh bootstrap and retention predicates agree on index shape."""
    for table_name, expected in _RETENTION_INDEXES.items():
        actual = await _index_columns(real_db_session, table_name)
        for index_name, columns in expected.items():
            assert actual.get(index_name) == columns

    cutoff = datetime.now(UTC).replace(tzinfo=None)
    old = cutoff - timedelta(hours=1)
    await _clear_seed_rows(real_db_session)
    try:
        await real_db_session.execute(
            text(
                "INSERT INTO voice_transcript "
                "(task_id, owner_user_id, transcript, created_at) "
                "VALUES (:task_id, :owner, 'index probe', :created_at)"
            ),
            {
                "task_id": f"{_PREFIX}explain-transcript",
                "owner": _OWNER,
                "created_at": old,
            },
        )
        await real_db_session.execute(
            text(
                "INSERT INTO ai_generation_audit "
                "(request_id, scene, provider, created_at) "
                "VALUES (:request_id, 'retention', 'mock', :created_at)"
            ),
            {"request_id": f"{_PREFIX}explain-audit", "created_at": old},
        )
        await _seed_outbox(
            real_db_session,
            event_id=f"{_PREFIX}explain-succeeded",
            status="succeeded",
            occurred_at=old,
        )
        await _seed_outbox(
            real_db_session,
            event_id=f"{_PREFIX}explain-dead-letter",
            status="dead_letter",
            occurred_at=old,
            dead_letter_at=old,
        )
        await real_db_session.commit()

        explain_cases = (
            (
                # 生产清理 SQL 对 outbox 的 SELECT 做了 FORCE（见
                # _purge_terminal_outbox_rows）：真实 MySQL 8.0.46 的优化器
                # 在等价索引间会摇摆（实测选中 publish 索引并引入 filesort）。
                # EXPLAIN 镜像生产 SQL 的 FORCE 写法，验证被强制的索引存在
                # 且被采用；voice/audit 是单表 DELETE，不支持索引提示，其
                # 确定性依赖 20260909_01 迁移替换掉旧单列索引后唯一保留的
                # (created_at, id) 索引，按原样 EXPLAIN 断言。
                "EXPLAIN SELECT id FROM voice_transcript "
                "WHERE created_at < :cutoff "
                "ORDER BY created_at, id LIMIT 1",
                "idx_voice_transcript_retention",
            ),
            (
                "EXPLAIN SELECT id FROM ai_generation_audit "
                "WHERE created_at < :cutoff "
                "ORDER BY created_at, id LIMIT 1",
                "idx_ai_generation_audit_retention_batch",
            ),
            (
                "EXPLAIN SELECT event_id FROM derivation_outbox "
                "FORCE INDEX (`idx_derivation_outbox_retention_succeeded`) "
                "WHERE status = 'succeeded' AND occurred_at < :cutoff "
                "ORDER BY occurred_at, event_id LIMIT 1",
                "idx_derivation_outbox_retention_succeeded",
            ),
            (
                "EXPLAIN SELECT event_id FROM derivation_outbox "
                "FORCE INDEX (`idx_derivation_outbox_retention_dead_letter`) "
                "WHERE status = 'dead_letter' AND dead_letter_at < :cutoff "
                "ORDER BY dead_letter_at, event_id LIMIT 1",
                "idx_derivation_outbox_retention_dead_letter",
            ),
        )
        for query, expected_key in explain_cases:
            row = (
                await real_db_session.execute(text(query), {"cutoff": cutoff})
            ).mappings().one()
            assert row["key"] == expected_key, row
    finally:
        await _clear_seed_rows(real_db_session)


@pytest.mark.asyncio
async def test_worker_once_runs_retention_but_consumer_once_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The non-consumer --once command is the deterministic retention entrypoint."""
    calls: list[str] = []

    async def fake_run_round(*_args: object) -> tuple[int, int, int]:
        calls.append("tasks")
        return 0, 0, 0

    async def fake_retention() -> dict[str, int]:
        calls.append("retention")
        return {}

    async def fake_consumer(*_args: object) -> dict[str, int]:
        calls.append("consumers")
        return {
            "claimed": 0,
            "applied": 0,
            "superseded": 0,
            "duplicate": 0,
            "skipped": 0,
        }

    monkeypatch.setattr(ai_worker, "_run_round", fake_run_round)
    monkeypatch.setattr(ai_worker, "_run_retention_cleanup_round", fake_retention)
    monkeypatch.setattr(ai_worker, "_run_cleanup_round", fake_consumer)

    # worker 的 --once 入口在自身线程内 asyncio.run（生产为独立进程），
    # 必须放到无事件循环的线程执行，避免与 pytest 的事件循环冲突。
    assert await asyncio.to_thread(ai_worker.main, ["--once"]) == 0
    assert calls == ["tasks", "retention"]
    calls.clear()
    assert await asyncio.to_thread(ai_worker.main, ["--consumers", "--once"]) == 0
    assert calls == ["consumers"]

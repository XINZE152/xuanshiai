"""Memory Kernel Core v1 ledger: append-only event store with owner sequencing.

Transaction order is fixed and must never change:

1. lock the owner's ``ai_memory_owner_sequence`` row (ensure-if-missing via
   idempotent upsert FIRST, then ``SELECT ... FOR UPDATE`` — this order keeps
   concurrent first writes serialized on the row lock instead of deadlocking
   on gap locks);
2. idempotency lookup by ``(owner_user_id, idempotency_key)`` — a matching
   fingerprint replays the stored event *without* re-materializing; a mismatching
   fingerprint raises the stable :class:`MemoryIdempotencyConflict`;
3. allocate ``server_seq`` and INSERT the event (append-only: no UPDATE/DELETE
   path exists for ``ai_memory_event``);
4. materialize the views via :class:`MemoryMaterializer`;
5. return the record.  The caller owns commit/rollback so event + views commit
   atomically.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.ai_memory import (
    MemoryEventInput,
    MemoryEventRecord,
    MemoryIdempotencyConflict,
)
from app.services.ai.memory.materializer import (
    MemoryMaterializationError,
    MemoryMaterializer,
)

__all__ = ["MemoryLedger"]

_EVENT_COLUMNS = (
    "event_id, owner_user_id, server_seq, subject, namespace, node_type, event_type, "
    "payload_json, source_kind, source_turn_id, source_ref, source_quote, "
    "causal_event_ids_json, consent_scope, idempotency_key, occurred_at"
)


def _default_clock() -> datetime:
    # MySQL DATETIME 无时区：统一存 naive UTC。
    return datetime.now(UTC).replace(tzinfo=None)


def _default_event_id() -> str:
    return uuid.uuid4().hex


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def _fingerprint(
    *,
    subject: str,
    namespace: str,
    node_type: str,
    event_type: str,
    payload: dict[str, Any],
    source_kind: str,
    source_turn_id: str | None,
    source_ref: str | None,
    source_quote: str | None,
    causal_event_ids: Iterable[str],
    consent_scope: str,
) -> str:
    material = [
        subject,
        namespace,
        node_type,
        event_type,
        _canonical_json(payload),
        source_kind,
        source_turn_id,
        source_ref,
        source_quote,
        list(causal_event_ids),
        consent_scope,
    ]
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _record_from_row(row: Any) -> MemoryEventRecord:
    payload = row["payload_json"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    causal = row["causal_event_ids_json"]
    if isinstance(causal, str):
        causal = json.loads(causal)
    return MemoryEventRecord(
        event_id=str(row["event_id"]),
        server_seq=int(row["server_seq"]),
        owner_user_id=int(row["owner_user_id"]),
        subject=str(row["subject"]),
        namespace=str(row["namespace"]),
        node_type=str(row["node_type"]),
        event_type=str(row["event_type"]),
        payload=payload,
        source_kind=str(row["source_kind"]),
        source_turn_id=row["source_turn_id"],
        source_ref=row["source_ref"],
        source_quote=row["source_quote"],
        causal_event_ids=tuple(causal or ()),
        consent_scope=str(row["consent_scope"]),
        idempotency_key=str(row["idempotency_key"]),
        occurred_at=row["occurred_at"],
    )


class MemoryLedger:
    """Append-only event ledger; the single write path into ``ai_memory_event``."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        materializer: MemoryMaterializer | None = None,
        clock: Callable[[], datetime] | None = None,
        event_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._db = db
        self._materializer = materializer or MemoryMaterializer(db)
        self._clock = clock or _default_clock
        self._event_id_factory = event_id_factory or _default_event_id

    _SQL_LOCK_OWNER = (
        "SELECT next_seq FROM ai_memory_owner_sequence "
        "WHERE owner_user_id = :owner_user_id FOR UPDATE"
    )
    _SQL_ENSURE_OWNER = (
        "INSERT INTO ai_memory_owner_sequence (owner_user_id, next_seq) "
        "VALUES (:owner_user_id, 1) ON DUPLICATE KEY UPDATE next_seq = next_seq"
    )
    _SQL_IDEMPOTENCY_LOOKUP = (
        f"SELECT {_EVENT_COLUMNS} FROM ai_memory_event "
        "WHERE owner_user_id = :owner_user_id AND idempotency_key = :idempotency_key"
    )
    _SQL_INSERT_EVENT = (
        f"INSERT INTO ai_memory_event ({_EVENT_COLUMNS}) VALUES "
        "(:event_id, :owner_user_id, :server_seq, :subject, :namespace, :node_type, "
        ":event_type, :payload_json, :source_kind, :source_turn_id, :source_ref, "
        ":source_quote, :causal_event_ids_json, :consent_scope, :idempotency_key, "
        ":occurred_at)"
    )
    _SQL_BUMP_OWNER = (
        "UPDATE ai_memory_owner_sequence SET next_seq = :next_seq "
        "WHERE owner_user_id = :owner_user_id"
    )
    _SQL_LIST_EVENTS = (
        f"SELECT {_EVENT_COLUMNS} FROM ai_memory_event "
        "WHERE owner_user_id = :owner_user_id AND server_seq > :after_seq "
        "ORDER BY server_seq ASC LIMIT :limit"
    )

    async def append(self, event_input: MemoryEventInput) -> MemoryEventRecord:
        """Append one event and materialize it; replays return the stored record."""

        fingerprint = self._input_fingerprint(event_input)
        next_seq = await self._lock_owner_sequence(event_input.owner_user_id)

        existing_row = (
            await self._db.execute(
                text(self._SQL_IDEMPOTENCY_LOOKUP),
                {
                    "owner_user_id": event_input.owner_user_id,
                    "idempotency_key": event_input.idempotency_key,
                },
            )
        ).mappings().first()
        if existing_row is not None:
            stored = _record_from_row(existing_row)
            if self._row_fingerprint(existing_row) != fingerprint:
                raise MemoryIdempotencyConflict(
                    owner_user_id=event_input.owner_user_id,
                    idempotency_key=event_input.idempotency_key,
                    existing_event_id=stored.event_id,
                )
            return stored  # 回放：不再次物化。

        event_id = self._event_id_factory()
        occurred_at = self._clock()
        await self._db.execute(
            text(self._SQL_INSERT_EVENT),
            {
                "event_id": event_id,
                "owner_user_id": event_input.owner_user_id,
                "server_seq": next_seq,
                "subject": event_input.subject.value,
                "namespace": event_input.namespace.value,
                "node_type": event_input.node_type.value,
                "event_type": event_input.event_type.value,
                "payload_json": _canonical_json(event_input.payload),
                "source_kind": event_input.source_kind.value,
                "source_turn_id": event_input.source_turn_id,
                "source_ref": event_input.source_ref,
                "source_quote": event_input.source_quote,
                "causal_event_ids_json": _canonical_json(list(event_input.causal_event_ids)),
                "consent_scope": event_input.consent_scope,
                "idempotency_key": event_input.idempotency_key,
                "occurred_at": occurred_at,
            },
        )
        await self._db.execute(
            text(self._SQL_BUMP_OWNER),
            {"owner_user_id": event_input.owner_user_id, "next_seq": next_seq + 1},
        )
        record = MemoryEventRecord(
            event_id=event_id,
            server_seq=next_seq,
            owner_user_id=event_input.owner_user_id,
            subject=event_input.subject,
            namespace=event_input.namespace,
            node_type=event_input.node_type,
            event_type=event_input.event_type,
            payload=event_input.payload,
            source_kind=event_input.source_kind,
            source_turn_id=event_input.source_turn_id,
            source_ref=event_input.source_ref,
            source_quote=event_input.source_quote,
            causal_event_ids=event_input.causal_event_ids,
            consent_scope=event_input.consent_scope,
            idempotency_key=event_input.idempotency_key,
            occurred_at=occurred_at,
        )
        await self._materializer.apply(record)
        # 延迟导入避免循环：derivations 在模块级引用本模块的 MemoryLedger。
        from app.services.ai.memory.derivations import enqueue_memory_event

        await enqueue_memory_event(self._db, record)
        return record

    async def append_many(
        self, event_inputs: Iterable[MemoryEventInput]
    ) -> list[MemoryEventRecord]:
        """Append several events in order within one transaction."""

        return [await self.append(event_input) for event_input in event_inputs]

    async def find_by_idempotency(
        self, owner_user_id: int, idempotency_key: str
    ) -> MemoryEventRecord | None:
        """按 (owner, idempotency_key) 回查已入账事件（重放前置检查用）。"""

        row = (
            await self._db.execute(
                text(self._SQL_IDEMPOTENCY_LOOKUP),
                {
                    "owner_user_id": owner_user_id,
                    "idempotency_key": idempotency_key,
                },
            )
        ).mappings().first()
        return _record_from_row(row) if row is not None else None

    async def list_events(
        self,
        owner_user_id: int,
        *,
        after_seq: int = 0,
        limit: int = 200,
    ) -> list[MemoryEventRecord]:
        rows = (
            await self._db.execute(
                text(self._SQL_LIST_EVENTS),
                {"owner_user_id": owner_user_id, "after_seq": after_seq, "limit": limit},
            )
        ).mappings().all()
        return [_record_from_row(row) for row in rows]

    # ------------------------------------------------------------------

    async def _lock_owner_sequence(self, owner_user_id: int) -> int:
        # 先幂等 ensure 再 SELECT FOR UPDATE（顺序不可反转）：REPEATABLE READ
        # 下"先查后插"会让并发首写各自持有间隙锁后互相等待（实测 1213 死锁）；
        # ensure-first 让后到事务阻塞在 duplicate-key 检查上，按行锁天然串行。
        await self._db.execute(text(self._SQL_ENSURE_OWNER), {"owner_user_id": owner_user_id})
        row = (
            await self._db.execute(text(self._SQL_LOCK_OWNER), {"owner_user_id": owner_user_id})
        ).mappings().first()
        if row is None:  # pragma: no cover - ensure 之后行必存在
            raise MemoryMaterializationError(
                f"owner sequence row missing after ensure for owner {owner_user_id}"
            )
        return int(row["next_seq"])

    async def lock_owner(self, owner_user_id: int) -> None:
        """获取该 owner 的序列行锁（事务内最外层，统一锁序防死锁）。

        所有 MemoryService 变更动作必须在触碰 suppression/claim 等行之前先拿
        这把锁：append 路径在锁内物化视图行，若 confirm/correct 先锁 claim 行
        再进 append 锁 owner，会与 propose 形成 AB-BA 死锁窗口。
        """

        await self._lock_owner_sequence(owner_user_id)

    @staticmethod
    def _input_fingerprint(event_input: MemoryEventInput) -> str:
        return _fingerprint(
            subject=event_input.subject.value,
            namespace=event_input.namespace.value,
            node_type=event_input.node_type.value,
            event_type=event_input.event_type.value,
            payload=event_input.payload,
            source_kind=event_input.source_kind.value,
            source_turn_id=event_input.source_turn_id,
            source_ref=event_input.source_ref,
            source_quote=event_input.source_quote,
            causal_event_ids=event_input.causal_event_ids,
            consent_scope=event_input.consent_scope,
        )

    @staticmethod
    def _row_fingerprint(row: Any) -> str:
        payload = row["payload_json"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        causal = row["causal_event_ids_json"]
        if isinstance(causal, str):
            causal = json.loads(causal)
        return _fingerprint(
            subject=str(row["subject"]),
            namespace=str(row["namespace"]),
            node_type=str(row["node_type"]),
            event_type=str(row["event_type"]),
            payload=payload,
            source_kind=str(row["source_kind"]),
            source_turn_id=row["source_turn_id"],
            source_ref=row["source_ref"],
            source_quote=row["source_quote"],
            causal_event_ids=causal or (),
            consent_scope=str(row["consent_scope"]),
        )

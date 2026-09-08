"""Memory Kernel Core v1 materializer: events -> current views.

Applies ledger events onto ``ai_memory_observation`` / ``ai_memory_claim`` /
``ai_memory_insight`` / ``ai_memory_state`` / ``ai_memory_suppression``.
Views are the *current* resolved state; every historical change stays in the
append-only event ledger, so view rows may be updated in place while the
event stream remains immutable.  ``rebuild_owner`` replays the whole event
stream for one owner and is idempotent thanks to deterministic node ids
(``obs_/clm_/ins_/st_/sup_{event_id}``) and first-wins canonical inserts.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.ai_memory import (
    MEMORY_EVENT_TRANSITIONS,
    MemoryEventRecord,
    MemoryEventType,
    MemoryNodeType,
    MemoryObservationPayload,
)
from app.services.ai.memory.policy import MemoryPolicy

__all__ = ["MemoryMaterializer", "MemoryMaterializationError"]


class MemoryMaterializationError(Exception):
    """Raised when an event cannot be applied to the current views."""


def _dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _safe_status_set(statuses: frozenset[str]) -> str:
    for status in statuses:
        if not status.replace("_", "").isalpha():
            raise MemoryMaterializationError(f"unsafe status literal: {status!r}")
    return ", ".join(f"'{status}'" for status in sorted(statuses))


class MemoryMaterializer:
    """Applies ledger records to the five views (inside the caller's transaction)."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # SQL（子串路由友好：假会话与真实 DB 走同一语句形状）
    # ------------------------------------------------------------------

    _SQL_CLAIM_READ = (
        "SELECT claim_id, status FROM ai_memory_claim "
        "WHERE owner_user_id = :owner_user_id AND subject = :subject "
        "AND namespace = :namespace AND canonical_key = :canonical_key FOR UPDATE"
    )
    _SQL_CLAIM_INSERT = (
        "INSERT INTO ai_memory_claim (claim_id, owner_user_id, subject, namespace, "
        "canonical_key, dimension, value_json, confidence, stability, importance, "
        "constraint_type, importance_confirmed, fact_kind, status, source_kind, "
        "last_event_id, last_event_seq) VALUES (:claim_id, :owner_user_id, :subject, "
        ":namespace, :canonical_key, :dimension, :value_json, :confidence, :stability, "
        ":importance, :constraint_type, :importance_confirmed, :fact_kind, :status, "
        ":source_kind, :last_event_id, :last_event_seq)"
    )
    _SQL_CLAIM_UPDATE = (
        "UPDATE ai_memory_claim SET status = :status, value_json = :value_json, "
        "confidence = :confidence, stability = :stability, importance = :importance, "
        "constraint_type = :constraint_type, importance_confirmed = :importance_confirmed, "
        "source_kind = :source_kind, last_event_id = :last_event_id, "
        "last_event_seq = :last_event_seq WHERE claim_id = :claim_id"
    )
    _SQL_OBSERVATION_GUARD = (
        "SELECT observation_id FROM ai_memory_observation WHERE observation_id = :observation_id"
    )
    _SQL_OBSERVATION_INSERT = (
        "INSERT INTO ai_memory_observation (observation_id, owner_user_id, subject, "
        "namespace, canonical_key, dimension, value_json, confidence, fact_kind, status, "
        "source_kind, source_quote, last_event_id, last_event_seq) VALUES "
        "(:observation_id, :owner_user_id, :subject, :namespace, :canonical_key, "
        ":dimension, :value_json, :confidence, :fact_kind, :status, :source_kind, "
        ":source_quote, :last_event_id, :last_event_seq)"
    )
    _SQL_OBSERVATION_CASCADE = (
        "UPDATE ai_memory_observation SET status = :status, last_event_id = :last_event_id, "
        "last_event_seq = :last_event_seq WHERE owner_user_id = :owner_user_id AND "
        "subject = :subject AND namespace = :namespace AND canonical_key = :canonical_key "
        "AND status IN ('proposed', 'active')"
    )
    _SQL_SUPPRESSION_READ = (
        "SELECT suppression_id, status FROM ai_memory_suppression "
        "WHERE owner_user_id = :owner_user_id AND subject = :subject "
        "AND namespace = :namespace AND canonical_key = :canonical_key FOR UPDATE"
    )
    _SQL_SUPPRESSION_INSERT = (
        "INSERT INTO ai_memory_suppression (suppression_id, owner_user_id, subject, "
        "namespace, canonical_key, reason, status, last_event_id, last_event_seq) VALUES "
        "(:suppression_id, :owner_user_id, :subject, :namespace, :canonical_key, :reason, "
        ":status, :last_event_id, :last_event_seq)"
    )
    _SQL_SUPPRESSION_UPDATE = (
        "UPDATE ai_memory_suppression SET status = :status, last_event_id = :last_event_id, "
        "last_event_seq = :last_event_seq WHERE suppression_id = :suppression_id"
    )
    _SQL_SUPPRESSION_REACTIVATE = (
        "UPDATE ai_memory_suppression SET status = :status, reason = :reason, "
        "last_event_id = :last_event_id, last_event_seq = :last_event_seq "
        "WHERE suppression_id = :suppression_id"
    )
    _SQL_STATE_READ = (
        "SELECT state_id, status FROM ai_memory_state WHERE state_id = :state_id "
        "AND owner_user_id = :owner_user_id FOR UPDATE"
    )
    _SQL_STATE_INSERT = (
        "INSERT INTO ai_memory_state (state_id, owner_user_id, subject, namespace, "
        "canonical_key, value_json, valid_until, confidence, status, last_event_id, "
        "last_event_seq) VALUES (:state_id, :owner_user_id, :subject, :namespace, "
        ":canonical_key, :value_json, :valid_until, :confidence, :status, "
        ":last_event_id, :last_event_seq)"
    )
    _SQL_STATE_UPDATE = (
        "UPDATE ai_memory_state SET status = :status, last_event_id = :last_event_id, "
        "last_event_seq = :last_event_seq WHERE state_id = :state_id "
        "AND owner_user_id = :owner_user_id"
    )
    _SQL_INSIGHT_READ = (
        "SELECT insight_id, status FROM ai_memory_insight WHERE insight_id = :insight_id "
        "AND owner_user_id = :owner_user_id FOR UPDATE"
    )
    _SQL_INSIGHT_INSERT = (
        "INSERT INTO ai_memory_insight (insight_id, owner_user_id, subject, namespace, "
        "summary, claim_ids_json, confidence, status, last_event_id, last_event_seq) VALUES "
        "(:insight_id, :owner_user_id, :subject, :namespace, :summary, :claim_ids_json, "
        ":confidence, :status, :last_event_id, :last_event_seq)"
    )
    _SQL_INSIGHT_UPDATE = (
        "UPDATE ai_memory_insight SET status = :status, last_event_id = :last_event_id, "
        "last_event_seq = :last_event_seq WHERE insight_id = :insight_id "
        "AND owner_user_id = :owner_user_id"
    )

    # ------------------------------------------------------------------
    # apply / rebuild
    # ------------------------------------------------------------------

    async def apply(self, record: MemoryEventRecord) -> None:
        node_type = record.node_type
        if node_type == MemoryNodeType.OBSERVATION:
            await self._apply_observation(record)
        elif node_type == MemoryNodeType.CLAIM:
            await self._apply_claim(record)
        elif node_type == MemoryNodeType.INSIGHT:
            await self._apply_insight(record)
        elif node_type == MemoryNodeType.STATE:
            await self._apply_state(record)
        elif node_type == MemoryNodeType.SUPPRESSION:
            await self._apply_suppression(record)
        else:  # pragma: no cover - enum is frozen
            raise MemoryMaterializationError(f"unknown node_type: {node_type!r}")

    async def rebuild_owner(self, owner_user_id: int) -> int:
        """Replay every event of one owner onto the views; returns applied count."""

        from app.services.ai.memory.ledger import MemoryLedger

        ledger = MemoryLedger(self._db, materializer=self)
        applied = 0
        after_seq = 0
        while True:
            records = await ledger.list_events(owner_user_id, after_seq=after_seq, limit=200)
            if not records:
                break
            for record in records:
                await self.apply(record)
                applied += 1
            after_seq = records[-1].server_seq
            if len(records) < 200:
                break
        return applied

    # ------------------------------------------------------------------
    # observation
    # ------------------------------------------------------------------

    async def _apply_observation(self, record: MemoryEventRecord) -> None:
        event_type = record.event_type
        if event_type == MemoryEventType.OBSERVATION_PROPOSED:
            payload = MemoryObservationPayload.model_validate(record.payload)
            observation_id = f"obs_{record.event_id}"
            guard = await self._db.execute(
                text(self._SQL_OBSERVATION_GUARD), {"observation_id": observation_id}
            )
            if guard.scalar_one_or_none() is not None:
                return
            await self._db.execute(
                text(self._SQL_OBSERVATION_INSERT),
                {
                    "observation_id": observation_id,
                    "owner_user_id": record.owner_user_id,
                    "subject": record.subject.value,
                    "namespace": record.namespace.value,
                    "canonical_key": payload.canonical_key,
                    "dimension": payload.dimension,
                    "value_json": _dumps(payload.value),
                    "confidence": payload.confidence,
                    "fact_kind": payload.fact_kind,
                    "status": MemoryPolicy.observation_initial_status(record.source_kind),
                    "source_kind": record.source_kind.value,
                    "source_quote": record.source_quote,
                    "last_event_id": record.event_id,
                    "last_event_seq": record.server_seq,
                },
            )
            return
        transition = MEMORY_EVENT_TRANSITIONS[event_type.value]
        # observation 生命周期事件按 canonical key 级联（视图行无唯一键）。
        await self._db.execute(
            text(self._SQL_OBSERVATION_CASCADE),
            {
                "status": transition.to_status,
                "owner_user_id": record.owner_user_id,
                "subject": record.subject.value,
                "namespace": record.namespace.value,
                "canonical_key": record.payload["canonical_key"],
                "last_event_id": record.event_id,
                "last_event_seq": record.server_seq,
            },
        )

    # ------------------------------------------------------------------
    # claim
    # ------------------------------------------------------------------

    async def _read_claim(self, record: MemoryEventRecord, canonical_key: str) -> dict[str, Any] | None:
        row = await self._db.execute(
            text(self._SQL_CLAIM_READ),
            {
                "owner_user_id": record.owner_user_id,
                "subject": record.subject.value,
                "namespace": record.namespace.value,
                "canonical_key": canonical_key,
            },
        )
        return row.mappings().first()

    async def _apply_claim(self, record: MemoryEventRecord) -> None:
        event_type = record.event_type
        payload = record.payload
        canonical_key = payload["canonical_key"]

        if event_type == MemoryEventType.CLAIM_PROPOSED:
            existing = await self._read_claim(record, canonical_key)
            if existing is not None:
                return  # 先到先得：同一 canonical key 只保留第一条 proposed Claim。
            try:
                await self._db.execute(
                    text(self._SQL_CLAIM_INSERT),
                    {
                        "claim_id": f"clm_{record.event_id}",
                        "owner_user_id": record.owner_user_id,
                        "subject": record.subject.value,
                        "namespace": record.namespace.value,
                        "canonical_key": canonical_key,
                        "dimension": payload.get("dimension"),
                        "value_json": _dumps(payload.get("value")),
                        "confidence": payload.get("confidence"),
                        "stability": payload["stability"],
                        "importance": payload.get("importance", 0.5),
                        "constraint_type": payload.get("constraint_type"),
                        "importance_confirmed": 1 if payload.get("importance_confirmed") else 0,
                        "fact_kind": payload["fact_kind"],
                        "status": "proposed",
                        "source_kind": record.source_kind.value,
                        "last_event_id": record.event_id,
                        "last_event_seq": record.server_seq,
                    },
                )
            except IntegrityError:
                # 并发同一 canonical key 的败者：事件保留在账本，视图让位
                # 先到者（first-wins），重放/重建时同样命中此分支。
                if await self._read_claim(record, canonical_key) is not None:
                    return
                raise
            return

        transition = MEMORY_EVENT_TRANSITIONS[event_type.value]
        existing = await self._read_claim(record, canonical_key)
        if existing is None:
            raise MemoryMaterializationError(
                f"claim transition {event_type.value!r} targets missing canonical key "
                f"{canonical_key!r} for owner {record.owner_user_id}"
            )
        current_status = str(existing["status"])
        if current_status == transition.to_status:
            return  # 幂等重放。
        if current_status not in transition.from_statuses:
            raise MemoryMaterializationError(
                f"invalid claim transition {event_type.value!r} from {current_status!r} "
                f"for owner {record.owner_user_id} key {canonical_key!r}"
            )
        await self._db.execute(
            text(self._SQL_CLAIM_UPDATE),
            {
                "claim_id": str(existing["claim_id"]),
                "status": transition.to_status,
                "value_json": _dumps(payload.get("value")),
                "confidence": payload.get("confidence"),
                "stability": payload["stability"],
                "importance": payload.get("importance", 0.5),
                "constraint_type": payload.get("constraint_type"),
                "importance_confirmed": 1 if payload.get("importance_confirmed") else 0,
                "source_kind": record.source_kind.value,
                "last_event_id": record.event_id,
                "last_event_seq": record.server_seq,
            },
        )
        if event_type == MemoryEventType.CLAIM_USER_CORRECTED:
            # 纠正级联：同 key 的证据行标记 user_corrected（旧值留在事件账本）。
            await self._db.execute(
                text(self._SQL_OBSERVATION_CASCADE),
                {
                    "status": "user_corrected",
                    "owner_user_id": record.owner_user_id,
                    "subject": record.subject.value,
                    "namespace": record.namespace.value,
                    "canonical_key": canonical_key,
                    "last_event_id": record.event_id,
                    "last_event_seq": record.server_seq,
                },
            )

    # ------------------------------------------------------------------
    # insight
    # ------------------------------------------------------------------

    async def _apply_insight(self, record: MemoryEventRecord) -> None:
        event_type = record.event_type
        payload = record.payload
        if event_type == MemoryEventType.INSIGHT_PROPOSED:
            insight_id = f"ins_{record.event_id}"
            await self._db.execute(
                text(self._SQL_INSIGHT_INSERT),
                {
                    "insight_id": insight_id,
                    "owner_user_id": record.owner_user_id,
                    "subject": record.subject.value,
                    "namespace": record.namespace.value,
                    "summary": payload["summary"],
                    "claim_ids_json": json.dumps(list(payload["claim_ids"]), ensure_ascii=False),
                    "confidence": payload.get("confidence", 0.5),
                    "status": "proposed",
                    "last_event_id": record.event_id,
                    "last_event_seq": record.server_seq,
                },
            )
            return
        insight_id = payload.get("insight_id")
        if not insight_id:
            raise MemoryMaterializationError(
                f"{event_type.value} requires insight_id in payload for owner {record.owner_user_id}"
            )
        row = (
            await self._db.execute(
                text(self._SQL_INSIGHT_READ),
                {"insight_id": str(insight_id), "owner_user_id": record.owner_user_id},
            )
        ).mappings().first()
        if row is None:
            raise MemoryMaterializationError(
                f"insight transition {event_type.value!r} targets missing insight {insight_id!r}"
            )
        transition = MEMORY_EVENT_TRANSITIONS[event_type.value]
        current_status = str(row["status"])
        if current_status == transition.to_status:
            return
        if current_status not in transition.from_statuses:
            raise MemoryMaterializationError(
                f"invalid insight transition {event_type.value!r} from {current_status!r} "
                f"for insight {insight_id!r}"
            )
        await self._db.execute(
            text(self._SQL_INSIGHT_UPDATE),
            {
                "insight_id": str(insight_id),
                "owner_user_id": record.owner_user_id,
                "status": transition.to_status,
                "last_event_id": record.event_id,
                "last_event_seq": record.server_seq,
            },
        )

    # ------------------------------------------------------------------
    # state
    # ------------------------------------------------------------------

    async def _apply_state(self, record: MemoryEventRecord) -> None:
        event_type = record.event_type
        payload = record.payload
        if event_type == MemoryEventType.STATE_ACTIVATED:
            state_id = f"st_{record.event_id}"
            await self._db.execute(
                text(self._SQL_STATE_INSERT),
                {
                    "state_id": state_id,
                    "owner_user_id": record.owner_user_id,
                    "subject": record.subject.value,
                    "namespace": record.namespace.value,
                    "canonical_key": payload["canonical_key"],
                    "value_json": _dumps(payload.get("value")),
                    "valid_until": payload["valid_until"],
                    "confidence": payload.get("confidence"),
                    "status": "active",
                    "last_event_id": record.event_id,
                    "last_event_seq": record.server_seq,
                },
            )
            return
        state_id = payload.get("state_id")
        if not state_id:
            raise MemoryMaterializationError(
                f"{event_type.value} requires state_id in payload for owner {record.owner_user_id}"
            )
        row = (
            await self._db.execute(
                text(self._SQL_STATE_READ),
                {"state_id": str(state_id), "owner_user_id": record.owner_user_id},
            )
        ).mappings().first()
        if row is None:
            raise MemoryMaterializationError(
                f"state transition {event_type.value!r} targets missing state {state_id!r}"
            )
        transition = MEMORY_EVENT_TRANSITIONS[event_type.value]
        current_status = str(row["status"])
        if current_status == transition.to_status:
            return
        if current_status not in transition.from_statuses:
            raise MemoryMaterializationError(
                f"invalid state transition {event_type.value!r} from {current_status!r} "
                f"for state {state_id!r}"
            )
        await self._db.execute(
            text(self._SQL_STATE_UPDATE),
            {
                "state_id": str(state_id),
                "owner_user_id": record.owner_user_id,
                "status": transition.to_status,
                "last_event_id": record.event_id,
                "last_event_seq": record.server_seq,
            },
        )

    # ------------------------------------------------------------------
    # suppression
    # ------------------------------------------------------------------

    async def _apply_suppression(self, record: MemoryEventRecord) -> None:
        event_type = record.event_type
        payload = record.payload
        canonical_key = payload["canonical_key"]
        row = (
            await self._db.execute(
                text(self._SQL_SUPPRESSION_READ),
                {
                    "owner_user_id": record.owner_user_id,
                    "subject": record.subject.value,
                    "namespace": record.namespace.value,
                    "canonical_key": canonical_key,
                },
            )
        ).mappings().first()

        if event_type == MemoryEventType.SUPPRESSION_ACTIVATED:
            if row is None:
                try:
                    await self._db.execute(
                        text(self._SQL_SUPPRESSION_INSERT),
                        {
                            "suppression_id": f"sup_{record.event_id}",
                            "owner_user_id": record.owner_user_id,
                            "subject": record.subject.value,
                            "namespace": record.namespace.value,
                            "canonical_key": canonical_key,
                            "reason": payload.get("reason"),
                            "status": "active",
                            "last_event_id": record.event_id,
                            "last_event_seq": record.server_seq,
                        },
                    )
                except IntegrityError:
                    # 并发同 key 墓碑的败者：回读命中即让位（事件仍在账本）。
                    row = (
                        await self._db.execute(
                            text(self._SQL_SUPPRESSION_READ),
                            {
                                "owner_user_id": record.owner_user_id,
                                "subject": record.subject.value,
                                "namespace": record.namespace.value,
                                "canonical_key": canonical_key,
                            },
                        )
                    ).mappings().first()
                    if row is not None:
                        return
                    raise
                return
            if str(row["status"]) == "active":
                return  # 已有活动墓碑：幂等。
            await self._db.execute(
                text(self._SQL_SUPPRESSION_REACTIVATE),
                {
                    "suppression_id": str(row["suppression_id"]),
                    "status": "active",
                    "reason": payload.get("reason"),
                    "last_event_id": record.event_id,
                    "last_event_seq": record.server_seq,
                },
            )
            return

        # suppression_lifted
        if row is None:
            raise MemoryMaterializationError(
                f"suppression_lifted targets missing canonical key {canonical_key!r} "
                f"for owner {record.owner_user_id}"
            )
        if str(row["status"]) == "lifted":
            return  # 幂等重放。
        await self._db.execute(
            text(self._SQL_SUPPRESSION_UPDATE),
            {
                "suppression_id": str(row["suppression_id"]),
                "status": "lifted",
                "last_event_id": record.event_id,
                "last_event_seq": record.server_seq,
            },
        )

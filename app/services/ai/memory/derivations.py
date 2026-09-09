"""Memory Kernel Core v1 derivations: outbox fan-out, insight cascade, state TTL.

Fan-out model:

- :func:`enqueue_memory_event` is called by the Ledger after every fresh
  append; ``derivation_outbox`` is keyed by the memory ``event_id``, so one
  event can only ever be enqueued once (replays return before enqueueing).
- The payload in the outbox is strictly minimal (node/event/subject) — no
  candidate text, quotes or transcript.  Consumers re-read the event from the
  append-only ledger by ``event_id``.
- Consumption reuses the existing cleanup consumer: handlers are registered
  per ``memory_{node_type}`` event type at import time, so the standard
  ``ai_worker --consumers`` loop picks them up.  Receipt idempotency comes
  from ``derivation_consumer_receipt``.
- :func:`handle_memory_derivation` implements the only cross-node rule this
  phase: a terminating claim event (corrected/superseded/contradicted/expired)
  invalidates every proposed/confirmed Insight derived from that claim.
  Insights never mutate Claims — they are read-only derivations.
- :func:`expire_memory_states` enforces the mandatory State TTL.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.ai_memory import (
    MemoryEventInput,
    MemoryEventRecord,
    MemoryEventType,
    MemoryNodeType,
    MemorySourceKind,
)
from app.services.ai.memory.ledger import MemoryLedger, _record_from_row
from app.services.derivation_outbox import DerivationEvent, register_cleanup_handler

__all__ = [
    "MEMORY_AGGREGATE_TYPE",
    "MEMORY_EVENT_TYPE_BY_NODE",
    "enqueue_memory_event",
    "expire_memory_states",
    "handle_memory_derivation",
    "invalidate_insights_for_claim",
]

MEMORY_AGGREGATE_TYPE = "ai_memory"
MEMORY_EVENT_TYPE_BY_NODE: dict[str, str] = {
    node.value: f"memory_{node.value}" for node in MemoryNodeType
}

# Claim 终态事件：命中即失效依赖它的 Insight（Insight 状态机只读）。
_INSIGHT_TERMINATING_CLAIM_EVENTS = frozenset(
    {
        MemoryEventType.CLAIM_USER_CORRECTED,
        MemoryEventType.CLAIM_SUPERSEDED,
        MemoryEventType.CLAIM_CONTRADICTED,
        MemoryEventType.CLAIM_EXPIRED,
    }
)

# 触发投影 invalidation/rebuild 的事件：确认（内容可能变多）、纠正/替代/
# 矛盾/过期（内容变化）、删除墓碑（事实需从投影消失）。
_PROJECTION_TRIGGERING_EVENTS = frozenset(
    {
        MemoryEventType.CLAIM_CONFIRMED,
        *_INSIGHT_TERMINATING_CLAIM_EVENTS,
        MemoryEventType.SUPPRESSION_ACTIVATED,
    }
)

_SQL_ENQUEUE = (
    "INSERT INTO derivation_outbox "
    "(event_id, aggregate_type, aggregate_id, event_type, changed_fields, "
    "source_revision_json, privacy_revision, payload_minimal, priority, published_at) "
    "VALUES (:event_id, 'ai_memory', :aggregate_id, :event_type, '[]', '{}', 0, "
    ":payload_minimal, 50, UTC_TIMESTAMP()) "
    "ON DUPLICATE KEY UPDATE event_id = event_id"
)
_SQL_LOAD_EVENT = (
    "SELECT event_id, owner_user_id, server_seq, subject, namespace, node_type, "
    "event_type, payload_json, source_kind, source_turn_id, source_ref, source_quote, "
    "causal_event_ids_json, consent_scope, idempotency_key, occurred_at "
    "FROM ai_memory_event WHERE event_id = :event_id"
)
_SQL_INSIGHT_SCAN = (
    "SELECT insight_id, summary, claim_ids_json, confidence, last_event_seq "
    "FROM ai_memory_insight WHERE owner_user_id = :owner_user_id "
    "AND subject = :subject AND status IN ('proposed', 'confirmed')"
)
_SQL_STATE_DUE = (
    "SELECT state_id, owner_user_id, subject, namespace, canonical_key, value_json, "
    "valid_until, confidence FROM ai_memory_state "
    "WHERE status = 'active' AND valid_until < :now{owner_clause} LIMIT :limit"
)


async def enqueue_memory_event(db: AsyncSession, record: MemoryEventRecord) -> None:
    """以 event_id 为主键幂等入队；重复入队被 ON DUPLICATE KEY 吸收。"""

    await db.execute(
        text(_SQL_ENQUEUE),
        {
            "event_id": record.event_id,
            "aggregate_id": record.owner_user_id,
            "event_type": MEMORY_EVENT_TYPE_BY_NODE[record.node_type.value],
            "payload_minimal": json.dumps(
                {
                    "node_type": record.node_type.value,
                    "event_type": record.event_type.value,
                    "subject": record.subject.value,
                },
                ensure_ascii=False,
            ),
        },
    )


async def handle_memory_derivation(db: AsyncSession, event: DerivationEvent) -> str:
    """cleanup 消费者 handler：回读账本事件并执行跨节点派生规则。"""

    row = (
        await db.execute(text(_SQL_LOAD_EVENT), {"event_id": event.event_id})
    ).mappings().first()
    if row is None:
        return "noop"
    record = _record_from_row(row)
    triggered_projection_rebuild = False
    if (
        record.node_type
        in (MemoryNodeType.CLAIM, MemoryNodeType.SUPPRESSION)
        and record.event_type in _PROJECTION_TRIGGERING_EVENTS
    ):
        touched = await _rebuild_projections_for_owner(db, record, event.event_id)
        triggered_projection_rebuild = touched > 0
    if (
        record.node_type == MemoryNodeType.CLAIM
        and record.event_type in _INSIGHT_TERMINATING_CLAIM_EVENTS
    ):
        invalidated = await invalidate_insights_for_claim(db, record)
        return "processed" if invalidated or triggered_projection_rebuild else "noop"
    return "processed" if triggered_projection_rebuild else "noop"


async def _rebuild_projections_for_owner(
    db: AsyncSession, record: MemoryEventRecord, trigger_event_id: str
) -> int:
    """claim 确认/纠正/删除后重建 owner 的全部已授权投影维度。

    builder 只收集 confirmed Claim 并排除活动墓碑命中的事实：删除记忆
    （suppress）后重建会产出不含该事实的新版本。失败向上抛给消费者，
    由 bounded retry 重试。
    """

    from app.services.ai.memory.projections import MemoryProjectionService

    return await MemoryProjectionService(db).rebuild_dimensions_for_owner(
        record.owner_user_id,
        trigger_ref=f"memory-event:{trigger_event_id}",
    )


async def _noop_projection_handler(db: AsyncSession, event: DerivationEvent) -> str:
    """memory_projection outbox 行是给未来下游消费者的幂等通知；本期 noop。"""

    return "noop"


async def invalidate_insights_for_claim(
    db: AsyncSession, record: MemoryEventRecord
) -> int:
    """把派生自某 Claim 的全部未定 Insight 置为 invalidated（事件驱动）。"""

    from app.services.ai.memory.service import MemoryService

    canonical_key = record.payload.get("canonical_key")
    if not canonical_key:
        return 0
    # 锁序统一：先 owner 序列锁再 claim 行锁（与 MemoryService 变更路径一致）。
    ledger = MemoryLedger(db)
    await ledger.lock_owner(record.owner_user_id)
    claim_row = await MemoryService(db).read_claim_by_canonical(
        owner_user_id=record.owner_user_id,
        subject=record.subject.value,
        namespace=record.namespace.value,
        canonical_key=str(canonical_key),
    )
    if claim_row is None:
        return 0
    claim_id = str(claim_row["claim_id"])
    rows = (
        await db.execute(
            text(_SQL_INSIGHT_SCAN),
            {"owner_user_id": record.owner_user_id, "subject": record.subject.value},
        )
    ).mappings().all()
    ledger = MemoryLedger(db)
    invalidated = 0
    for row in rows:
        claim_ids = tuple(str(item) for item in json.loads(row["claim_ids_json"]))
        if claim_id not in claim_ids:
            continue
        await ledger.append(
            MemoryEventInput(
                owner_user_id=record.owner_user_id,
                subject=record.subject,
                namespace=record.namespace,
                node_type=MemoryNodeType.INSIGHT,
                event_type=MemoryEventType.INSIGHT_INVALIDATED,
                payload={
                    "summary": str(row["summary"]),
                    "claim_ids": claim_ids,
                    "confidence": float(row["confidence"] or 0.0),
                    "insight_id": str(row["insight_id"]),
                },
                source_kind=MemorySourceKind.INFERRED,
                causal_event_ids=(record.event_id,),
                idempotency_key=(
                    f"insight-invalidate:{row['insight_id']}:{int(row['last_event_seq'])}"
                ),
            )
        )
        invalidated += 1
    return invalidated


async def expire_memory_states(
    db: AsyncSession,
    *,
    now: datetime,
    owner_user_id: int | None = None,
    limit: int = 200,
) -> int:
    """TTL 到期的 State 只能转 expired；幂等键绑定 state_id。"""

    owner_clause = " AND owner_user_id = :owner_user_id" if owner_user_id is not None else ""
    rows = (
        await db.execute(
            text(_SQL_STATE_DUE.format(owner_clause=owner_clause)),
            {
                "now": now,
                "limit": limit,
                **({"owner_user_id": owner_user_id} if owner_user_id is not None else {}),
            },
        )
    ).mappings().all()
    ledger = MemoryLedger(db)
    for row in rows:
        await ledger.append(
            MemoryEventInput(
                owner_user_id=int(row["owner_user_id"]),
                subject=str(row["subject"]),  # type: ignore[arg-type]
                namespace=str(row["namespace"]),  # type: ignore[arg-type]
                node_type=MemoryNodeType.STATE,
                event_type=MemoryEventType.STATE_EXPIRED,
                payload={
                    "canonical_key": str(row["canonical_key"]),
                    "value": json.loads(row["value_json"])
                    if isinstance(row["value_json"], str)
                    else row["value_json"],
                    "valid_until": row["valid_until"],
                    "confidence": float(row["confidence"])
                    if row["confidence"] is not None
                    else None,
                    "state_id": str(row["state_id"]),
                },
                source_kind=MemorySourceKind.INFERRED,
                idempotency_key=f"state-expiry:{row['state_id']}",
            )
        )
    return len(rows)


# 注册进既有 cleanup 消费者分发表：ai_worker --consumers 循环无需改动即可
# 消费记忆事件（收据幂等由 derivation_consumer_receipt 保证）。
for _node_type in MemoryNodeType:
    register_cleanup_handler(MEMORY_EVENT_TYPE_BY_NODE[_node_type.value], handle_memory_derivation)
# 投影重建通知行（memory_projection）：本期只登记 noop 消费者，防止
# 未知事件类型在消费者循环里报错；真实下游消费者在后续计划接入。
register_cleanup_handler("memory_projection", _noop_projection_handler)

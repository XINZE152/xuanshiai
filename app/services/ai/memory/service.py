"""Memory Kernel Core v1 unified MemoryService.

The only entry point for Shadow Write / API / backfill.  Every mutating
action goes through the Ledger as an event (the service never writes view
tables directly — reads use plain SELECTs).  Guarantees:

- ``propose`` writes to the current subject only, never crosses subjects and
  is blocked by active tombstones;
- ``confirm_claim`` requires ``expected_revision`` (the claim row's
  ``last_event_seq``) and is the only path that can set
  ``importance_confirmed=True`` — there is no parameter for it, so callers
  (AI included) cannot inject it;
- ``correct_claim`` never overwrites history: the claim row moves to
  ``user_corrected`` with a causal link back to the previous event, and the
  old value stays queryable in the ledger;
- ``suppress`` / ``lift_suppression`` are owner-scoped (foreign owners get
  NotFound) and idempotent via derived idempotency keys.
"""

from __future__ import annotations

import json
import unicodedata
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.ai_memory import (
    MemoryEventInput,
    MemoryEventRecord,
    MemoryEventType,
    MemoryNodeType,
    MemorySourceKind,
)
from app.services.ai.memory.ledger import MemoryLedger
from app.services.ai.memory.policy import MemoryPolicy

__all__ = [
    "MemoryClaimNotFound",
    "MemoryClaimStateDenied",
    "MemoryRevisionConflict",
    "MemoryService",
    "normalize_idempotency_key",
]


class MemoryClaimNotFound(Exception):
    """Claim/tombstone does not exist for this owner (owner-scoped 404)."""


class MemoryRevisionConflict(Exception):
    """expected_revision does not match the row's last_event_seq (409)."""


class MemoryClaimStateDenied(Exception):
    """The claim's current status does not allow this action."""


def normalize_idempotency_key(idempotency_key: str | None, *, max_length: int = 128) -> str:
    """Normalize the shared AI Memory idempotency-key contract."""

    key = idempotency_key.strip() if idempotency_key is not None else ""
    if (
        not key
        or len(key) > max_length
        or any(ch.isspace() or unicodedata.category(ch) == "Cc" for ch in key)
    ):
        raise ValueError(
            f"Idempotency-Key must be 1-{max_length} characters and contain "
            "no whitespace or control characters"
        )
    return key


def _loads(value: Any) -> Any:
    if value is None or not isinstance(value, str):
        return value
    return json.loads(value)


class MemoryService:
    """统一记忆服务：propose / confirm / correct / suppress / lift。"""

    def __init__(self, db: AsyncSession, *, ledger: MemoryLedger | None = None) -> None:
        self._db = db
        self._ledger = ledger or MemoryLedger(db)

    @staticmethod
    def _validate_idempotency_key(idempotency_key: str, *, max_length: int = 128) -> str:
        return normalize_idempotency_key(idempotency_key, max_length=max_length)

    @classmethod
    def _validate_propose_idempotency_key(cls, idempotency_key: str) -> str:
        """Validate and normalize propose key before deriving the ``:claim`` key.

        The public key contract is 1–128 characters.  Propose emits a second
        event using ``:claim``; reject keys longer than 122 rather than
        truncating (which could create collisions).
        """

        return cls._validate_idempotency_key(idempotency_key, max_length=122)

    _SQL_CLAIM_BY_ID = (
        "SELECT claim_id, owner_user_id, subject, namespace, canonical_key, dimension, "
        "value_json, confidence, stability, importance, constraint_type, "
        "importance_confirmed, fact_kind, status, source_kind, last_event_id, "
        "last_event_seq FROM ai_memory_claim WHERE claim_id = :claim_id "
        "AND owner_user_id = :owner_user_id FOR UPDATE"
    )
    _SQL_CLAIM_BY_CANONICAL = (
        "SELECT claim_id, owner_user_id, subject, namespace, canonical_key, dimension, "
        "value_json, confidence, stability, importance, constraint_type, "
        "importance_confirmed, fact_kind, status, source_kind, last_event_id, "
        "last_event_seq FROM ai_memory_claim WHERE owner_user_id = :owner_user_id "
        "AND subject = :subject AND namespace = :namespace "
        "AND canonical_key = :canonical_key FOR UPDATE"
    )
    _SQL_PROJECTION_GRANT_BY_ID = (
        "SELECT grant_id, owner_user_id, function_key, purpose, data_category, status "
        "FROM ai_memory_projection_grant WHERE grant_id = :grant_id FOR UPDATE"
    )
    _SQL_SUPPRESSION_READ = (
        "SELECT suppression_id, owner_user_id, subject, namespace, canonical_key, "
        "reason, status, last_event_id, last_event_seq FROM ai_memory_suppression "
        "WHERE owner_user_id = :owner_user_id AND subject = :subject "
        "AND namespace = :namespace AND canonical_key = :canonical_key FOR UPDATE"
    )

    # ------------------------------------------------------------------
    # 读（视图只读；写入全部走 Ledger）
    # ------------------------------------------------------------------

    _SQL_MEMORY_EVENT_PROVENANCE = (
        "SELECT source_quote, source_ref FROM ai_memory_event WHERE event_id = :event_id"
    )
    _SQL_SUPPRESSION_BY_ID = (
        "SELECT suppression_id, owner_user_id, subject, namespace, canonical_key, "
        "status, last_event_id, last_event_seq FROM ai_memory_suppression "
        "WHERE suppression_id = :suppression_id AND owner_user_id = :owner_user_id"
    )

    async def list_memory_items(
        self,
        owner_user_id: int,
        *,
        subject: str,
        statuses: frozenset[str],
        after_seq: int = 0,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """当前用户的记忆条目（Claim 视图 + 最小来源引用）。

        响应字段恒为 content/value/status/confidence/stability/importance/
        source_quote/source_ref 及标识字段——完整 transcript 不进入响应。
        """

        safe_statuses = sorted(statuses)
        for status_value in safe_statuses:
            if not status_value.replace("_", "").isalpha():
                raise ValueError(f"unsafe status literal: {status_value!r}")
        status_list = ", ".join(f"'{value}'" for value in safe_statuses)
        # 活动墓碑的 Claim 不进列表（删除对用户可见生效）；解除墓碑后自然恢复。
        sql = (
            "SELECT claim_id, subject, namespace, canonical_key, dimension, value_json, "
            "confidence, stability, importance, status, last_event_id, last_event_seq "
            "FROM ai_memory_claim WHERE owner_user_id = :owner_user_id "
            "AND subject = :subject AND status IN ("
            + status_list
            + ") AND last_event_seq > :after_seq "
            "AND NOT EXISTS (SELECT 1 FROM ai_memory_suppression s WHERE "
            "s.owner_user_id = ai_memory_claim.owner_user_id "
            "AND s.subject = ai_memory_claim.subject "
            "AND s.namespace = ai_memory_claim.namespace "
            "AND s.canonical_key = ai_memory_claim.canonical_key "
            "AND s.status = 'active') "
            "ORDER BY last_event_seq ASC LIMIT :limit"
        )
        rows = (
            await self._db.execute(
                text(sql),
                {
                    "owner_user_id": owner_user_id,
                    "subject": subject,
                    "after_seq": after_seq,
                    "limit": limit,
                },
            )
        ).mappings().all()
        items: list[dict[str, Any]] = []
        last_seq = after_seq
        for row in rows:
            value = _loads(row["value_json"])
            provenance = (
                await self._db.execute(
                    text(self._SQL_MEMORY_EVENT_PROVENANCE),
                    {"event_id": str(row["last_event_id"])},
                )
            ).mappings().first()
            last_seq = int(row["last_event_seq"])
            items.append(
                {
                    "claim_id": str(row["claim_id"]),
                    "subject": str(row["subject"]),
                    "node_type": "claim",
                    "content": str(value) if value is not None else None,
                    "value": value,
                    "status": str(row["status"]),
                    "confidence": float(row["confidence"]) if row["confidence"] is not None else None,
                    "stability": float(row["stability"]) if row["stability"] is not None else None,
                    "importance": float(row["importance"]) if row["importance"] is not None else None,
                    "source_quote": str(provenance["source_quote"]) if provenance and provenance["source_quote"] else None,
                    "source_ref": str(provenance["source_ref"]) if provenance and provenance["source_ref"] else None,
                    "canonical_key": str(row["canonical_key"]),
                    # 写操作的 expected_revision 必须来自服务端最新事件序号；
                    # 前端不得猜测或复用另一条 Claim 的版本。
                    "revision": int(row["last_event_seq"]),
                }
            )
        return items, last_seq

    async def list_memory_view_items(
        self,
        owner_user_id: int,
        *,
        statuses: frozenset[str],
        after_seq: int = 0,
        limit: int = 20,
        subject: str | None = None,
    ) -> list[dict[str, Any]]:
        """Memory 管理页视图（跨主体）：最小字段 + updated_at，owner 隔离。

        与 :meth:`list_memory_items` 同一投影边界（无 transcript、无完整
        quote），另带 ``updated_at``；活动墓碑命中的事实不出现。
        """

        safe_statuses = sorted(statuses)
        for status_value in safe_statuses:
            if not status_value.replace("_", "").isalpha():
                raise ValueError(f"unsafe status literal: {status_value!r}")
        status_list = ", ".join(f"'{value}'" for value in safe_statuses)
        subject_clause = "AND subject = :subject" if subject is not None else ""
        sql = (
            "SELECT claim_id, subject, namespace, canonical_key, dimension, value_json, "
            "confidence, stability, importance, status, last_event_id, last_event_seq, "
            "updated_at "
            "FROM ai_memory_claim WHERE owner_user_id = :owner_user_id "
            "AND status IN ("
            + status_list
            + ") AND last_event_seq > :after_seq "
            + subject_clause
            + " AND NOT EXISTS (SELECT 1 FROM ai_memory_suppression s WHERE "
            "s.owner_user_id = ai_memory_claim.owner_user_id "
            "AND s.subject = ai_memory_claim.subject "
            "AND s.namespace = ai_memory_claim.namespace "
            "AND s.canonical_key = ai_memory_claim.canonical_key "
            "AND s.status = 'active') "
            "ORDER BY last_event_seq ASC LIMIT :limit"
        )
        params: dict[str, Any] = {
            "owner_user_id": owner_user_id,
            "after_seq": after_seq,
            "limit": limit,
        }
        if subject is not None:
            params["subject"] = subject
        rows = (await self._db.execute(text(sql), params)).mappings().all()
        items: list[dict[str, Any]] = []
        last_seq = after_seq
        for row in rows:
            value = _loads(row["value_json"])
            provenance = (
                await self._db.execute(
                    text(self._SQL_MEMORY_EVENT_PROVENANCE),
                    {"event_id": str(row["last_event_id"])},
                )
            ).mappings().first()
            last_seq = int(row["last_event_seq"])
            items.append(
                {
                    "claim_id": str(row["claim_id"]),
                    "subject": str(row["subject"]),
                    "node_type": "claim",
                    "content": str(value) if value is not None else None,
                    "value": value,
                    "status": str(row["status"]),
                    "confidence": float(row["confidence"]) if row["confidence"] is not None else None,
                    "stability": float(row["stability"]) if row["stability"] is not None else None,
                    "importance": float(row["importance"]) if row["importance"] is not None else None,
                    "source_quote": str(provenance["source_quote"]) if provenance and provenance["source_quote"] else None,
                    "source_ref": str(provenance["source_ref"]) if provenance and provenance["source_ref"] else None,
                    "canonical_key": str(row["canonical_key"]),
                    "updated_at": row["updated_at"].isoformat() if hasattr(row["updated_at"], "isoformat") else str(row["updated_at"]),
                    "revision": int(row["last_event_seq"]),
                }
            )
        return items, last_seq

    async def list_projection_grants(self, owner_user_id: int) -> list[dict[str, Any]]:
        """owner 的全部投影授权（active + revoked 历史概览，最小字段）。"""

        rows = (
            await self._db.execute(
                text(
                    "SELECT grant_id, function_key, purpose, data_category, status, "
                    "policy_revision, granted_at, revoked_at FROM "
                    "ai_memory_projection_grant WHERE owner_user_id = :owner_user_id "
                    "ORDER BY function_key, purpose, data_category"
                ),
                {"owner_user_id": owner_user_id},
            )
        ).mappings().all()
        grants: list[dict[str, Any]] = []
        for row in rows:
            grants.append(
                {
                    "grant_id": str(row["grant_id"]),
                    "function_key": str(row["function_key"]),
                    "purpose": str(row["purpose"]),
                    "data_category": str(row["data_category"]),
                    "status": str(row["status"]),
                    "policy_revision": str(row["policy_revision"]),
                    "granted_at": row["granted_at"].isoformat() if hasattr(row["granted_at"], "isoformat") else str(row["granted_at"]),
                    "revoked_at": row["revoked_at"].isoformat() if row["revoked_at"] is not None and hasattr(row["revoked_at"], "isoformat") else (str(row["revoked_at"]) if row["revoked_at"] is not None else None),
                }
            )
        return grants

    async def revoke_projection_grant(
        self, owner_user_id: int, grant_id: str, *, idempotency_key: str
    ) -> dict[str, Any]:
        """撤销 owner 的单个投影授权并立即失效对应投影（owner-scoped 404）。

        grant_id 形如 ``prj-grant:{owner}:{function_key}:{purpose}:{category}``，
        归属校验同时校验 id 内嵌 owner 与行 owner，双保险不泄露他人授权。

        Grant 的状态机本身天然幂等：key 作为统一输入门禁验证并进入服务层，
        但 grant 存储没有独立操作账本，因此不持久化或比较该 key。
        """

        self._validate_idempotency_key(idempotency_key)

        row = (
            await self._db.execute(
                text(self._SQL_PROJECTION_GRANT_BY_ID),
                {"grant_id": grant_id},
            )
        ).mappings().first()
        if row is None or int(row["owner_user_id"]) != int(owner_user_id):
            raise MemoryClaimNotFound("projection grant not found")
        # 归属以行内 owner_user_id 为准（grant_id 可能是内嵌 owner 的可读格式，
        # 也可能是不透明哈希格式），不做 id 前缀推断。
        if str(row["status"]) != "active":
            return {"status": "already_revoked", **{
                key: str(row[key]) for key in ("function_key", "purpose", "data_category")
            }}
        from app.services.ai.memory.projections import MemoryProjectionService

        invalidated = await MemoryProjectionService(self._db).revoke(
            owner_user_id=owner_user_id,
            function_key=str(row["function_key"]),
            purpose=str(row["purpose"]),
            data_category=str(row["data_category"]),
        )
        return {
            "status": "revoked",
            "function_key": str(row["function_key"]),
            "purpose": str(row["purpose"]),
            "data_category": str(row["data_category"]),
            "invalidated_projections": invalidated,
        }

    async def find_claim_by_field_identity(
        self,
        *,
        owner_user_id: int,
        subject: str,
        identity: str,
    ) -> dict[str, Any] | None:
        """按 Shadow Write 的同一 identity 规则反查 Claim（dimension 无关）。

        ``canonical_key`` 以 ``sha256(identity)[:16]`` 收尾，因此用
        ``LIKE '%:<digest>'`` 精确匹配，不依赖 dimension 取值。
        """

        import hashlib as _hashlib

        digest = _hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        row = await self._db.execute(
            text(
                "SELECT claim_id, owner_user_id, subject, namespace, canonical_key, "
                "dimension, value_json, confidence, stability, importance, "
                "constraint_type, importance_confirmed, fact_kind, status, "
                "last_event_id, last_event_seq FROM ai_memory_claim "
                "WHERE owner_user_id = :owner_user_id AND subject = :subject "
                "AND canonical_key LIKE CONCAT('%:', :digest)"
            ),
            {"owner_user_id": owner_user_id, "subject": subject, "digest": digest},
        )
        return row.mappings().first()

    async def read_suppression_by_id(
        self, owner_user_id: int, suppression_id: str
    ) -> dict[str, Any] | None:
        row = await self._db.execute(
            text(self._SQL_SUPPRESSION_BY_ID),
            {"suppression_id": suppression_id, "owner_user_id": owner_user_id},
        )
        return row.mappings().first()

    async def _replay_if_same_target(
        self,
        owner_user_id: int,
        idempotency_key: str,
        claim_id: str,
        event_type: str,
    ) -> MemoryEventRecord | None:
        """同一 Idempotency-Key 且指向同一 Claim 的历史动作 → 回放原事件。

        key 已被用于其它 Claim 时返回 None（走正常校验路径并最终 409）。
        """

        if not idempotency_key:
            return None
        prior = await self._ledger.find_by_idempotency(owner_user_id, idempotency_key)
        if prior is None:
            return None
        if prior.event_type.value != event_type:
            return None
        if prior.source_ref != f"memory:claim:{claim_id}":
            return None
        return prior

    async def read_claim_by_canonical(
        self,
        *,
        owner_user_id: int,
        subject: str,
        namespace: str = "moxiang",
        canonical_key: str,
    ) -> dict[str, Any] | None:
        row = await self._db.execute(
            text(self._SQL_CLAIM_BY_CANONICAL),
            {
                "owner_user_id": owner_user_id,
                "subject": subject,
                "namespace": namespace,
                "canonical_key": canonical_key,
            },
        )
        return row.mappings().first()

    async def _read_claim_by_id(self, owner_user_id: int, claim_id: str) -> dict[str, Any]:
        row = (
            await self._db.execute(
                text(self._SQL_CLAIM_BY_ID),
                {"claim_id": claim_id, "owner_user_id": owner_user_id},
            )
        ).mappings().first()
        if row is None:
            raise MemoryClaimNotFound(
                f"memory claim {claim_id!r} not found for owner {owner_user_id}"
            )
        return row

    async def read_suppression(
        self, *, owner_user_id: int, subject: str, namespace: str, canonical_key: str
    ) -> dict[str, Any] | None:
        row = await self._db.execute(
            text(self._SQL_SUPPRESSION_READ),
            {
                "owner_user_id": owner_user_id,
                "subject": subject,
                "namespace": namespace,
                "canonical_key": canonical_key,
            },
        )
        return row.mappings().first()

    # ------------------------------------------------------------------
    # propose（Shadow Write 入口）
    # ------------------------------------------------------------------

    async def propose(
        self,
        *,
        owner_user_id: int,
        subject: str,
        canonical_key: str,
        dimension: str | None,
        value: Any,
        confidence: float,
        source_kind: str,
        fact_kind: str,
        source_quote: str | None = None,
        source_turn_id: str | None = None,
        source_ref: str | None = None,
        namespace: str = "moxiang",
        idempotency_key: str,
    ) -> list[MemoryEventRecord]:
        """沉淀一条观察证据；同一事实第一条会同时生成 proposed Claim。

        ``source_kind`` 必须由调用方从抽取的 ``assertion_mode`` 显式映射
        （explicit→user_explicit / inferred→inferred），不得从 confidence 猜测。
        """

        idempotency_key = self._validate_propose_idempotency_key(idempotency_key)
        # 统一锁序：先拿 owner 序列行锁再触碰 suppression/claim 行，
        # 与 append 的物化路径构成同一顺序，消除 AB-BA 死锁窗口。
        await self._ledger.lock_owner(owner_user_id)
        MemoryPolicy.assert_subject_write(subject, fact_kind=fact_kind)
        tombstone = await self.read_suppression(
            owner_user_id=owner_user_id,
            subject=subject,
            namespace=namespace,
            canonical_key=canonical_key,
        )
        MemoryPolicy.assert_not_suppressed(
            str(tombstone["status"]) if tombstone is not None else None
        )

        observation_event = MemoryEventInput(
            owner_user_id=owner_user_id,
            subject=subject,  # type: ignore[arg-type]
            namespace=namespace,  # type: ignore[arg-type]
            node_type=MemoryNodeType.OBSERVATION,
            event_type=MemoryEventType.OBSERVATION_PROPOSED,
            payload={
                "canonical_key": canonical_key,
                "dimension": dimension,
                "value": value,
                "confidence": confidence,
                "fact_kind": fact_kind,
            },
            source_kind=source_kind,  # type: ignore[arg-type]
            source_turn_id=source_turn_id,
            source_ref=source_ref,
            source_quote=source_quote,
            idempotency_key=idempotency_key,
        )
        existing_claim = await self.read_claim_by_canonical(
            owner_user_id=owner_user_id,
            subject=subject,
            namespace=namespace,
            canonical_key=canonical_key,
        )
        if existing_claim is not None:
            return [await self._ledger.append(observation_event)]

        claim_event = MemoryEventInput(
            owner_user_id=owner_user_id,
            subject=subject,  # type: ignore[arg-type]
            namespace=namespace,  # type: ignore[arg-type]
            node_type=MemoryNodeType.CLAIM,
            event_type=MemoryEventType.CLAIM_PROPOSED,
            payload={
                "canonical_key": canonical_key,
                "dimension": dimension,
                "value": value,
                "confidence": confidence,
                "stability": MemoryPolicy.derive_stability(source_kind, confidence),
                "importance": 0.5,
                "constraint_type": None,
                "importance_confirmed": False,
                "fact_kind": fact_kind,
            },
            source_kind=source_kind,  # type: ignore[arg-type]
            source_turn_id=source_turn_id,
            source_ref=source_ref,
            source_quote=source_quote,
            idempotency_key=f"{idempotency_key}:claim",
        )
        return await self._ledger.append_many([observation_event, claim_event])

    # ------------------------------------------------------------------
    # confirm / correct
    # ------------------------------------------------------------------

    async def confirm_claim(
        self,
        *,
        owner_user_id: int,
        claim_id: str,
        expected_revision: int,
        importance: float,
        constraint_type: str | None = None,
        idempotency_key: str | None = None,
    ) -> MemoryEventRecord:
        """用户确认：只有本动作能把 Claim 升级为 confirmed 并设定重要度/硬约束。"""

        key = self._validate_idempotency_key(
            idempotency_key or f"claim-confirm:{claim_id}:{expected_revision}"
        )
        # 锁序同 propose：先 owner 序列锁，再 claim 行锁。
        await self._ledger.lock_owner(owner_user_id)
        row = await self._read_claim_by_id(owner_user_id, claim_id)
        # 先查幂等回放：同一 Idempotency-Key 的重试在乐观锁推进后仍可回放原事件。
        replay = await self._replay_if_same_target(
            owner_user_id, key, claim_id, "claim_confirmed"
        )
        if replay is not None:
            return replay
        if int(row["last_event_seq"]) != expected_revision:
            raise MemoryRevisionConflict(
                f"claim {claim_id!r} revision {row['last_event_seq']} != expected {expected_revision}"
            )
        if str(row["status"]) != "proposed":
            raise MemoryClaimStateDenied(
                f"claim {claim_id!r} status {row['status']!r} is not confirmable"
            )
        stability = max(
            float(row["stability"]),
            MemoryPolicy.derive_stability(MemorySourceKind.USER_CONFIRMED, 1.0),
        )
        event = MemoryEventInput(
            owner_user_id=owner_user_id,
            subject=str(row["subject"]),  # type: ignore[arg-type]
            namespace=str(row["namespace"]),  # type: ignore[arg-type]
            node_type=MemoryNodeType.CLAIM,
            event_type=MemoryEventType.CLAIM_CONFIRMED,
            payload={
                "canonical_key": str(row["canonical_key"]),
                "dimension": row["dimension"],
                "value": _loads(row["value_json"]),
                "confidence": float(row["confidence"]) if row["confidence"] is not None else None,
                "stability": stability,
                "importance": float(importance),
                "constraint_type": constraint_type,
                "importance_confirmed": True,
                "fact_kind": str(row["fact_kind"]),
            },
            source_kind=MemorySourceKind.USER_CONFIRMED,
            source_ref=f"memory:claim:{claim_id}",
            idempotency_key=key,
        )
        return await self._ledger.append(event)

    async def correct_claim(
        self,
        *,
        owner_user_id: int,
        claim_id: str,
        expected_revision: int,
        value: Any,
        importance: float | None = None,
        constraint_type: str | None = None,
        source_quote: str | None = None,
        idempotency_key: str | None = None,
    ) -> MemoryEventRecord:
        """用户纠正：生成 user_corrected 事件，保留旧 Claim 行与因果链。"""

        key = self._validate_idempotency_key(
            idempotency_key or f"claim-correct:{claim_id}:{expected_revision}"
        )
        await self._ledger.lock_owner(owner_user_id)
        row = await self._read_claim_by_id(owner_user_id, claim_id)
        replay = await self._replay_if_same_target(
            owner_user_id, key, claim_id, "claim_user_corrected"
        )
        if replay is not None:
            return replay
        if int(row["last_event_seq"]) != expected_revision:
            raise MemoryRevisionConflict(
                f"claim {claim_id!r} revision {row['last_event_seq']} != expected {expected_revision}"
            )
        if str(row["status"]) not in ("proposed", "confirmed"):
            raise MemoryClaimStateDenied(
                f"claim {claim_id!r} status {row['status']!r} is not correctable"
            )
        event = MemoryEventInput(
            owner_user_id=owner_user_id,
            subject=str(row["subject"]),  # type: ignore[arg-type]
            namespace=str(row["namespace"]),  # type: ignore[arg-type]
            node_type=MemoryNodeType.CLAIM,
            event_type=MemoryEventType.CLAIM_USER_CORRECTED,
            payload={
                "canonical_key": str(row["canonical_key"]),
                "dimension": row["dimension"],
                "value": value,
                "confidence": float(row["confidence"]) if row["confidence"] is not None else None,
                "stability": MemoryPolicy.derive_stability(MemorySourceKind.USER_EXPLICIT, 1.0),
                "importance": float(importance) if importance is not None else float(row["importance"]),
                "constraint_type": constraint_type
                if constraint_type is not None
                else row["constraint_type"],
                "importance_confirmed": bool(row["importance_confirmed"]),
                "fact_kind": str(row["fact_kind"]),
            },
            source_kind=MemorySourceKind.USER_EXPLICIT,
            source_ref=f"memory:claim:{claim_id}",
            source_quote=source_quote,
            causal_event_ids=(str(row["last_event_id"]),),
            idempotency_key=key,
        )
        return await self._ledger.append(event)

    # ------------------------------------------------------------------
    # insight（派生只读：Insight 永不改变长期 Claim）
    # ------------------------------------------------------------------

    async def propose_insight(
        self,
        *,
        owner_user_id: int,
        subject: str,
        summary: str,
        claim_ids: tuple[str, ...],
        confidence: float = 0.5,
        namespace: str = "moxiang",
        idempotency_key: str,
    ) -> list[MemoryEventRecord]:
        """沉淀一条从 Claim 派生的 Insight（只存摘要与 Claim ids，不复制原文）。"""

        idempotency_key = self._validate_idempotency_key(idempotency_key)
        event = MemoryEventInput(
            owner_user_id=owner_user_id,
            subject=subject,  # type: ignore[arg-type]
            namespace=namespace,  # type: ignore[arg-type]
            node_type=MemoryNodeType.INSIGHT,
            event_type=MemoryEventType.INSIGHT_PROPOSED,
            payload={
                "summary": summary,
                "claim_ids": tuple(claim_ids),
                "confidence": confidence,
            },
            source_kind=MemorySourceKind.INFERRED,
            idempotency_key=idempotency_key,
        )
        return [await self._ledger.append(event)]

    # ------------------------------------------------------------------
    # suppress / lift
    # ------------------------------------------------------------------

    async def suppress(
        self,
        *,
        owner_user_id: int,
        subject: str,
        canonical_key: str,
        idempotency_key: str,
        reason: str | None = None,
        namespace: str = "moxiang",
    ) -> MemoryEventRecord:
        """用户删除墓碑：阻止同 canonical key 自动重抽取。

        ``idempotency_key`` 必填（API 层取 ``Idempotency-Key`` 头）：同一用户
        意图重试用同一 key 回放；lift 之后再次删除是新的用户意图，用新 key。
        """

        idempotency_key = self._validate_idempotency_key(idempotency_key)
        event = MemoryEventInput(
            owner_user_id=owner_user_id,
            subject=subject,  # type: ignore[arg-type]
            namespace=namespace,  # type: ignore[arg-type]
            node_type=MemoryNodeType.SUPPRESSION,
            event_type=MemoryEventType.SUPPRESSION_ACTIVATED,
            payload={"canonical_key": canonical_key, "reason": reason},
            source_kind=MemorySourceKind.USER_EXPLICIT,
            idempotency_key=idempotency_key,
        )
        return await self._ledger.append(event)

    async def lift_suppression(
        self,
        *,
        owner_user_id: int,
        subject: str,
        canonical_key: str,
        idempotency_key: str,
        namespace: str = "moxiang",
    ) -> MemoryEventRecord:
        """解除墓碑：只能由墓碑属主发起（他人读取即 NotFound）。"""

        idempotency_key = self._validate_idempotency_key(idempotency_key)
        await self._ledger.lock_owner(owner_user_id)
        tombstone = await self.read_suppression(
            owner_user_id=owner_user_id,
            subject=subject,
            namespace=namespace,
            canonical_key=canonical_key,
        )
        if tombstone is None:
            raise MemoryClaimNotFound(
                f"suppression for {canonical_key!r} not found for owner {owner_user_id}"
            )
        event = MemoryEventInput(
            owner_user_id=owner_user_id,
            subject=subject,  # type: ignore[arg-type]
            namespace=namespace,  # type: ignore[arg-type]
            node_type=MemoryNodeType.SUPPRESSION,
            event_type=MemoryEventType.SUPPRESSION_LIFTED,
            payload={"canonical_key": canonical_key},
            source_kind=MemorySourceKind.USER_EXPLICIT,
            idempotency_key=idempotency_key,
        )
        return await self._ledger.append(event)

    async def suppress_claim(
        self,
        *,
        owner_user_id: int,
        claim_id: str,
        reason: str | None = None,
        idempotency_key: str,
    ) -> MemoryEventRecord:
        """按 Claim 删除：对该事实的 canonical key 建墓碑（同 key 不再自动重抽取）。"""

        await self._ledger.lock_owner(owner_user_id)
        row = await self._read_claim_by_id(owner_user_id, claim_id)
        return await self.suppress(
            owner_user_id=owner_user_id,
            subject=str(row["subject"]),
            canonical_key=str(row["canonical_key"]),
            reason=reason,
            namespace=str(row["namespace"]),
            idempotency_key=idempotency_key,
        )

    async def lift_suppression_by_id(
        self,
        *,
        owner_user_id: int,
        suppression_id: str,
        idempotency_key: str,
    ) -> MemoryEventRecord:
        """按墓碑 ID 解除；他人（owner 不符）读取即 NotFound。"""

        tombstone = await self.read_suppression_by_id(owner_user_id, suppression_id)
        if tombstone is None:
            raise MemoryClaimNotFound(
                f"suppression {suppression_id!r} not found for owner {owner_user_id}"
            )
        return await self.lift_suppression(
            owner_user_id=owner_user_id,
            subject=str(tombstone["subject"]),
            canonical_key=str(tombstone["canonical_key"]),
            namespace=str(tombstone["namespace"]),
            idempotency_key=idempotency_key,
        )

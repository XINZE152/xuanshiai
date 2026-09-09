"""Memory Kernel Core v1 policy: frozen rules with no database access.

This module is the single enforcement point for:

- source priority (user_explicit > user_confirmed > user_feedback > inferred >
  behavior; conflicts are never silently last-write-wins),
- canonical key derivation (subject + dimension + identity digest),
- stability derivation per source kind,
- subject-write guards (personal ↔ about_user, ideal_partner ↔
  partner_preference; third-party subjects are unrepresentable),
- suppression guards (active tombstones block automatic re-extraction).

Ledger / MemoryService / Shadow Write all call into this module instead of
re-implementing the rules.
"""

from __future__ import annotations

import hashlib

from app.schemas.ai_memory import (
    MEMORY_SOURCE_RANK,
    MEMORY_SUBJECTS,
    MemorySourceKind,
)

__all__ = ["MemoryPolicy", "MemoryPolicyDenied"]


class MemoryPolicyDenied(Exception):
    """Raised when a write violates the frozen memory policy.

    Subject/policy errors must be searchable: the message always carries the
    offending subject / fact_kind / canonical key, never user quote text.
    """


# stability 推导基数：显式来源天然更稳；inferred/behavior 随 confidence 浮动。
_STABILITY_BASE: dict[str, float] = {
    MemorySourceKind.USER_EXPLICIT.value: 0.90,
    MemorySourceKind.USER_CONFIRMED.value: 0.85,
    MemorySourceKind.USER_FEEDBACK.value: 0.65,
    MemorySourceKind.INFERRED.value: 0.35,
    MemorySourceKind.BEHAVIOR.value: 0.25,
}
_STABILITY_CONFIDENCE_FACTOR: dict[str, float] = {
    MemorySourceKind.INFERRED.value: 0.25,
    MemorySourceKind.BEHAVIOR.value: 0.25,
}


class MemoryPolicy:
    """Frozen policy rules (pure functions; safe to call inside transactions)."""

    @staticmethod
    def source_priority(source_kind: MemorySourceKind | str) -> int:
        key = source_kind.value if isinstance(source_kind, MemorySourceKind) else str(source_kind)
        try:
            return MEMORY_SOURCE_RANK[key]
        except KeyError as exc:
            raise MemoryPolicyDenied(f"unknown memory source_kind: {source_kind!r}") from exc

    @staticmethod
    def resolve_conflict(existing_source_kind: str, incoming_source_kind: str) -> str:
        """Decide who wins a canonical-key conflict: ``replace`` or ``keep_existing``.

        同优先级保持先到先得（不是 LWW）；低优先级永远不能覆盖已有事实；
        只有更高优先级的来源可以接管。
        """

        existing_rank = MemoryPolicy.source_priority(existing_source_kind)
        incoming_rank = MemoryPolicy.source_priority(incoming_source_kind)
        if incoming_rank > existing_rank:
            return "replace"
        return "keep_existing"

    @staticmethod
    def canonical_key(subject: str, dimension: str | None, identity: str) -> str:
        """Deterministic canonical key: ``{subject}:{dimension}:{identity-digest}``."""

        if subject not in MEMORY_SUBJECTS:
            raise MemoryPolicyDenied(
                f"canonical_key requires subject in {sorted(MEMORY_SUBJECTS)}, got {subject!r}"
            )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        return f"{subject}:{dimension or 'general'}:{digest}"

    @staticmethod
    def candidate_identity(
        field_kind: str, field_key: str | None, category: str | None, content_hash: str | None
    ) -> str:
        """候选 → identity 的唯一规则（Shadow Write 与旧动作反查共用，防漂移）。

        structured 用白名单 field_key；entry 用 ``{category}:{content_hash}``。
        """

        if field_kind == "structured" and field_key:
            return field_key
        return f"{category or 'entry'}:{content_hash or ''}"

    @staticmethod
    def identity_digest(identity: str) -> str:
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def derive_stability(source_kind: MemorySourceKind | str, confidence: float) -> float:
        """Map (source_kind, confidence) onto a bounded stability score."""

        key = source_kind.value if isinstance(source_kind, MemorySourceKind) else str(source_kind)
        try:
            base = _STABILITY_BASE[key]
        except KeyError as exc:
            raise MemoryPolicyDenied(f"unknown memory source_kind: {source_kind!r}") from exc
        factor = _STABILITY_CONFIDENCE_FACTOR.get(key)
        value = base + factor * confidence if factor is not None else base
        return round(min(1.0, max(0.0, value)), 4)

    @staticmethod
    def observation_initial_status(source_kind: MemorySourceKind | str) -> str:
        """显式来源的证据立即 active；推断/行为证据先 proposed 等待确认链。"""

        key = source_kind.value if isinstance(source_kind, MemorySourceKind) else str(source_kind)
        if key in (MemorySourceKind.USER_EXPLICIT.value, MemorySourceKind.USER_CONFIRMED.value):
            return "active"
        return "proposed"

    @staticmethod
    def assert_subject_write(subject: str, *, fact_kind: str | None) -> None:
        """Lock subject ↔ fact_kind pairwise; third-party subjects are rejected."""

        if subject not in MEMORY_SUBJECTS:
            raise MemoryPolicyDenied(
                f"memory subject must be one of {sorted(MEMORY_SUBJECTS)}, got {subject!r}"
            )
        if fact_kind is None:
            return
        expected = "about_user" if subject == "personal" else "partner_preference"
        if fact_kind != expected:
            raise MemoryPolicyDenied(
                f"subject {subject!r} requires fact_kind={expected!r}, got {fact_kind!r}"
            )

    @staticmethod
    def assert_not_suppressed(suppression_status: str | None) -> None:
        """Active tombstones block automatic re-extraction of the same key."""

        if suppression_status == "active":
            raise MemoryPolicyDenied(
                "canonical key is suppressed by an active tombstone; "
                "automatic re-extraction is blocked until the user lifts it"
            )

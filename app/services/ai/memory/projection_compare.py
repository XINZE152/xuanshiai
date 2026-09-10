"""Canonical diff between legacy and memory projections (Phase 2 Task 5).

The diff exists for shadow-mode observability only.  It compares exactly the
canonical fields the plan freezes — subject, field key, value type, status,
source kind, claim id and version — and never entry values, quotes or any
user text.  Every field of :class:`ProjectionDiff` is safe to log.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["ProjectionDiff", "canonical_diff", "canonical_document"]


@dataclass(frozen=True)
class ProjectionDiff:
    """Shadow 双读的 canonical 差异（所有字段均可安全写入日志）。"""

    legacy_entry_count: int = 0
    memory_entry_count: int = 0
    subject_match: bool = True
    status_match: bool = True
    field_keys_only_legacy: tuple[str, ...] = ()
    field_keys_only_memory: tuple[str, ...] = ()
    claim_id_mismatches: tuple[str, ...] = field(default_factory=tuple)
    value_type_mismatches: tuple[str, ...] = field(default_factory=tuple)
    source_kind_mismatches: tuple[str, ...] = field(default_factory=tuple)

    @property
    def diff_types(self) -> tuple[str, ...]:
        types: list[str] = []
        if not self.subject_match:
            types.append("subject_mismatch")
        if not self.status_match:
            types.append("status_mismatch")
        if self.field_keys_only_legacy:
            types.append("field_key_only_legacy")
        if self.field_keys_only_memory:
            types.append("field_key_only_memory")
        if self.claim_id_mismatches:
            types.append("claim_id_mismatch")
        if self.value_type_mismatches:
            types.append("value_type_mismatch")
        if self.source_kind_mismatches:
            types.append("source_kind_mismatch")
        return tuple(types)

    @property
    def is_identical(self) -> bool:
        return not self.diff_types


def canonical_document(
    *,
    subject: str | None = None,
    status: str | None = None,
    entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """归一化两侧投影为可比较形状（剥离一切敏感原文）。

    entry 只保留 field_key / value_type / source_kind / claim_id；
    value、quote、transcript 在进入 diff 之前就被丢弃。
    """

    normalized_entries = [
        {
            "field_key": entry.get("field_key"),
            "value_type": entry.get("value_type"),
            "source_kind": entry.get("source_kind"),
            "claim_id": entry.get("claim_id"),
        }
        for entry in (entries or [])
    ]
    normalized_entries.sort(key=lambda item: str(item["field_key"]))
    return {
        "subject": subject,
        "status": status,
        "entries": normalized_entries,
    }


def _entry_index(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(entry["field_key"]): entry for entry in doc["entries"]}


def canonical_diff(
    legacy: dict[str, Any] | None, memory: dict[str, Any] | None
) -> ProjectionDiff:
    """比较两侧 canonical 文档；单侧缺失按空文档处理。"""

    legacy_doc = legacy if legacy is not None else canonical_document()
    memory_doc = memory if memory is not None else canonical_document()

    legacy_index = _entry_index(legacy_doc)
    memory_index = _entry_index(memory_doc)
    legacy_keys = set(legacy_index)
    memory_keys = set(memory_index)

    claim_id_mismatches: list[str] = []
    value_type_mismatches: list[str] = []
    source_kind_mismatches: list[str] = []
    for key in sorted(legacy_keys & memory_keys):
        legacy_entry = legacy_index[key]
        memory_entry = memory_index[key]
        if (
            legacy_entry.get("value_type") is not None
            and memory_entry.get("value_type") is not None
            and legacy_entry["value_type"] != memory_entry["value_type"]
        ):
            value_type_mismatches.append(key)
        if (
            legacy_entry.get("source_kind") is not None
            and memory_entry.get("source_kind") is not None
            and legacy_entry["source_kind"] != memory_entry["source_kind"]
        ):
            source_kind_mismatches.append(key)
        if (
            legacy_entry.get("claim_id") is not None
            and memory_entry.get("claim_id") is not None
            and legacy_entry["claim_id"] != memory_entry["claim_id"]
        ):
            claim_id_mismatches.append(key)

    return ProjectionDiff(
        legacy_entry_count=len(legacy_index),
        memory_entry_count=len(memory_index),
        subject_match=legacy_doc["subject"] == memory_doc["subject"],
        status_match=legacy_doc["status"] == memory_doc["status"],
        field_keys_only_legacy=tuple(sorted(legacy_keys - memory_keys)),
        field_keys_only_memory=tuple(sorted(memory_keys - legacy_keys)),
        claim_id_mismatches=tuple(claim_id_mismatches),
        value_type_mismatches=tuple(value_type_mismatches),
        source_kind_mismatches=tuple(source_kind_mismatches),
    )

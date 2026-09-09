"""Memory Projection policy: frozen rules with no database access.

Single enforcement point for (plan §2 / §5):

- function key gating — ``search`` / ``compatibility`` / ``recommend`` /
  ``counselor_context`` / ``persona_context`` are the enabled keys (Phase 3
  enabled the two consumer keys); unknown keys still fail closed
  (:class:`ProjectionPolicyDenied`);
- subject ↔ data_category interlock (ideal_partner is never a third-party
  profile);
- only confirmed Claims may enter the long-term projection — inferred
  Observations, unconfirmed Insights, State and Suppression are rejected;
- the exact 10-field entry allowlist and the canonical input hash.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

from app.schemas.ai_memory_projection import (
    MEMORY_PROJECTION_ENTRY_ALLOWLIST,
    PROJECTION_FUNCTIONS,
    PROJECTION_FUTURE_FUNCTIONS,
)

__all__ = [
    "MEMORY_PROJECTION_ENTRY_ALLOWLIST",
    "ProjectionFeatureNotEnabled",
    "ProjectionPolicy",
    "ProjectionPolicyDenied",
]


class ProjectionPolicyDenied(Exception):
    """Raised when a projection read/build violates the frozen policy.

    Messages carry ids / enum values only — never user text, field values or
    quotes.
    """


class ProjectionFeatureNotEnabled(ProjectionPolicyDenied):
    """已登记但未启用的 function key（Phase 3 后集合为空，保留兼容语义）。"""


# data_category -> 允许的 subject（None = 派生类别，两个主体均可）。
_SUBJECT_BY_DATA_CATEGORY: dict[str, str | None] = {
    "personal_profile": "personal",
    "public_profile_summary": "personal",
    "ideal_partner_preference": "ideal_partner",
    "compatibility_features": None,
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


class ProjectionPolicy:
    """Frozen projection rules (pure functions; safe inside transactions)."""

    @staticmethod
    def assert_function_enabled(function_key: str) -> None:
        """future key 与未知 key 一律 fail closed。"""

        if function_key in PROJECTION_FUNCTIONS:
            return
        if function_key in PROJECTION_FUTURE_FUNCTIONS:
            raise ProjectionFeatureNotEnabled(
                f"projection function_key {function_key!r} is registered as a "
                "future consumer and is not enabled yet"
            )
        raise ProjectionPolicyDenied(f"unknown projection function_key: {function_key!r}")

    @staticmethod
    def assert_subject_category(data_category: str, subject: str) -> None:
        """data_category 与 claim 主体互锁；ideal_partner 非第三方档案。"""

        if data_category not in _SUBJECT_BY_DATA_CATEGORY:
            raise ProjectionPolicyDenied(
                f"unknown projection data_category: {data_category!r}"
            )
        expected = _SUBJECT_BY_DATA_CATEGORY[data_category]
        if expected is not None and subject != expected:
            raise ProjectionPolicyDenied(
                f"data_category {data_category!r} requires subject {expected!r}, "
                f"got {subject!r}"
            )

    @staticmethod
    def assert_confirmed_claim(node_type: str, status: str) -> None:
        """只有 confirmed Claim 进入长期 Projection（fail closed）。"""

        if node_type == "claim" and status == "confirmed":
            return
        raise ProjectionPolicyDenied(
            f"projection entries may only come from confirmed claims, got "
            f"node_type={node_type!r} status={status!r}"
        )

    @staticmethod
    def allowed_fields() -> tuple[str, ...]:
        """Entry 字段 allowlist（冻结序，便于稳定输出）。"""

        return tuple(sorted(MEMORY_PROJECTION_ENTRY_ALLOWLIST))

    @staticmethod
    def assert_entry_fields(entry: Mapping[str, Any]) -> None:
        keys = set(entry)
        if keys != set(MEMORY_PROJECTION_ENTRY_ALLOWLIST):
            extra = sorted(keys - set(MEMORY_PROJECTION_ENTRY_ALLOWLIST))
            missing = sorted(set(MEMORY_PROJECTION_ENTRY_ALLOWLIST) - keys)
            raise ProjectionPolicyDenied(
                f"projection entry field allowlist violation: extra={extra} "
                f"missing={missing}"
            )

    @staticmethod
    def projection_input_hash(entries: Iterable[Mapping[str, Any]]) -> str:
        """构建输入的规范指纹：与版本/证据字段无关，内容变化即变。

        参与 hash 的只有下游可见的最小事实字段（field_key/value/value_type/
        source_kind/claim_id/stability/importance/constraint_type），按
        (field_key, claim_id) 排序后做 canonical JSON，再 sha256。构建期
        entry 可能还没有 projection_version/evidence_ref，因此这里只要求
        8 个内容字段齐备，多余键忽略。
        """

        content_keys = (
            "field_key",
            "value",
            "value_type",
            "source_kind",
            "claim_id",
            "stability",
            "importance",
            "constraint_type",
        )
        normalized = []
        for entry in entries:
            missing = [key for key in content_keys if key not in entry]
            if missing:
                raise ProjectionPolicyDenied(
                    f"projection entry missing hash input fields: {sorted(missing)}"
                )
            normalized.append({key: entry[key] for key in content_keys})
        normalized.sort(key=lambda item: (item["field_key"], item["claim_id"]))
        return hashlib.sha256(
            _canonical_json(normalized).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def assert_readable(
        projection: Mapping[str, Any],
        *,
        owner_user_id: int,
        function_key: str,
        purpose: str,
        data_category: str,
        policy_revision: str,
    ) -> None:
        """读取面前置校验：owner、维度、状态、策略版本与 entry allowlist。

        ``projection`` 是 ai_memory_projection 行 + 已解析 entries 的映射；
        任何不匹配都抛 :class:`ProjectionPolicyDenied`（服务层转稳定业务
        错误，不泄露资源存在性）。
        """

        if int(projection["owner_user_id"]) != int(owner_user_id):
            raise ProjectionPolicyDenied("projection owner mismatch")
        if str(projection["function_key"]) != function_key:
            raise ProjectionPolicyDenied("projection function_key mismatch")
        if str(projection["purpose"]) != purpose:
            raise ProjectionPolicyDenied("projection purpose mismatch")
        if str(projection["data_category"]) != data_category:
            raise ProjectionPolicyDenied("projection data_category mismatch")
        if str(projection["status"]) != "active":
            raise ProjectionPolicyDenied(
                f"projection status {str(projection['status'])!r} is not readable"
            )
        if str(projection["policy_revision"]) != policy_revision:
            raise ProjectionPolicyDenied(
                "projection policy_revision does not match the current policy"
            )
        for entry in projection.get("entries") or ():
            ProjectionPolicy.assert_entry_fields(entry)


# ---------------------------------------------------------------------------
# Consumer registry（Phase 2 Task 8 登记；Phase 3 启用消费 key）
# ---------------------------------------------------------------------------

# 两个 consumer key 已在 Phase 3 启用（PROJECTION_FUNCTIONS）；本注册表继续
# 作为其最小输入与隐私门槛的边界记录，消费者适配层以此为契约。

FUTURE_CONSUMER_REGISTRY: dict[str, dict[str, Any]] = {
    "counselor_context": {
        "label": "AI 军师会话上下文",
        "allowed_data_categories": ("personal_profile", "ideal_partner_preference"),
        "allowed_subjects": ("personal", "ideal_partner"),
        "min_claim_status": "confirmed",
        "forbidden_inputs": ("raw_event", "transcript", "source_quote"),
        "privacy_gate": (
            "profile_text_extract 授权快照 + 对应维度 grant + policy revision 一致"
        ),
    },
    "persona_context": {
        "label": "AI 分身公开画像",
        "allowed_data_categories": ("public_profile_summary",),
        "allowed_subjects": ("personal",),
        "min_claim_status": "confirmed",
        "forbidden_inputs": ("raw_event", "transcript", "source_quote"),
        "privacy_gate": (
            "只允许当前可见且已授权的 public_profile_summary；"
            "不得读取本人或他人的任何非公开维度"
        ),
    },
}


def future_consumer_spec(function_key: str) -> dict[str, Any]:
    """返回 consumer 的最小输入与隐私门槛（只读副本）。

    Phase 2 登记、Phase 3 启用：凡在注册表内的消费 key 均可查其边界契约；
    未登记 key（含 enabled 下游 key 与未知 key）一律拒绝。
    """

    if function_key not in FUTURE_CONSUMER_REGISTRY:
        raise ProjectionPolicyDenied(
            f"{function_key!r} is not a registered consumer"
        )
    spec = FUTURE_CONSUMER_REGISTRY.get(function_key)
    if spec is None:  # pragma: no cover - 上一行已保证非 None
        raise ProjectionPolicyDenied(
            f"future consumer {function_key!r} has no registered spec"
        )
    return dict(spec)


# 挂载为静态方法（类本体定义在上方；模块级函数便于单独引用）。
ProjectionPolicy.future_consumer_spec = staticmethod(future_consumer_spec)

"""Memory Projection Phase 2 冻结契约 schema（计划 §2）。

下游（Search / Compatibility / Recommend）只能读取经 subject、状态、来源、
授权和字段 allowlist 过滤的 Projection——本模块是「打印契约」层：

- function_key 固定 search / compatibility / recommend / counselor_context /
  persona_context；后两个为 Phase 3 启用的 AI 军师 / AI 分身消费 key
  （此前仅登记 future，启用记录见 docs/ai-memory-phase3-rollout-20260906.md）。
- purpose 固定 candidate_filter / candidate_rank / explanation /
  session_context；data_category 固定四值，未知枚举在 API 层返回 422。
- Entry 只允许 10 字段 allowlist；value 必须与 value_type 匹配；
  source_quote / transcript / 未授权字段在 schema 层不可表示
  （``extra="forbid"``）。
- subject ↔ data_category 互锁：personal_profile / public_profile_summary
  只能来自 personal 主体，ideal_partner_preference 只能来自 ideal_partner
  主体（ideal_partner 永远是当前用户自己的偏好，不是第三方档案）。
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.ai_memory import MemorySourceKind, MemorySubject

# ---------------------------------------------------------------------------
# 冻结词汇表
# ---------------------------------------------------------------------------

PROJECTION_FUNCTIONS: frozenset[str] = frozenset(
    {"search", "compatibility", "recommend", "counselor_context", "persona_context"}
)
# Phase 3 起 counselor_context / persona_context 已启用；集合保留为空，
# 未知 key 仍由 ProjectionPolicy.assert_function_enabled fail closed。
PROJECTION_FUTURE_FUNCTIONS: frozenset[str] = frozenset()
PROJECTION_PURPOSES: frozenset[str] = frozenset(
    {"candidate_filter", "candidate_rank", "explanation", "session_context"}
)
PROJECTION_DATA_CATEGORIES: frozenset[str] = frozenset(
    {
        "personal_profile",
        "ideal_partner_preference",
        "compatibility_features",
        "public_profile_summary",
    }
)

# Entry 字段 allowlist（计划 §2 冻结，禁止完整 quote / transcript / 置信度
# 等内部字段进入下游读取面）。
MEMORY_PROJECTION_ENTRY_ALLOWLIST: frozenset[str] = frozenset(
    {
        "field_key",
        "value",
        "value_type",
        "source_kind",
        "claim_id",
        "stability",
        "importance",
        "constraint_type",
        "projection_version",
        "evidence_ref",
    }
)

ProjectionValueType = Literal["string", "number", "boolean", "string_list"]

_SOURCE_KIND_VALUES: frozenset[str] = frozenset(
    source.value for source in MemorySourceKind
)
_FIELD_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class ProjectionFunctionKey(str, Enum):
    SEARCH = "search"
    COMPATIBILITY = "compatibility"
    RECOMMEND = "recommend"
    COUNSELOR_CONTEXT = "counselor_context"
    PERSONA_CONTEXT = "persona_context"


class ProjectionPurpose(str, Enum):
    CANDIDATE_FILTER = "candidate_filter"
    CANDIDATE_RANK = "candidate_rank"
    EXPLANATION = "explanation"
    SESSION_CONTEXT = "session_context"


class ProjectionDataCategory(str, Enum):
    PERSONAL_PROFILE = "personal_profile"
    IDEAL_PARTNER_PREFERENCE = "ideal_partner_preference"
    COMPATIBILITY_FEATURES = "compatibility_features"
    PUBLIC_PROFILE_SUMMARY = "public_profile_summary"


# ---------------------------------------------------------------------------
# Entry / Document / Grant
# ---------------------------------------------------------------------------


class ProjectionEntry(BaseModel):
    """投影条目：下游可见的最小事实单元（10 字段 allowlist）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field_key: str = Field(..., pattern=r"^[a-z][a-z0-9_]{0,63}$")
    value: Any = None
    value_type: ProjectionValueType
    source_kind: str
    claim_id: str = Field(..., min_length=1, max_length=64)
    stability: float = Field(..., ge=0.0, le=1.0)
    importance: float = Field(..., ge=0.0, le=1.0)
    constraint_type: str | None = Field(default=None, max_length=32)
    projection_version: int = Field(..., ge=1)
    evidence_ref: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _validate(self) -> "ProjectionEntry":
        if self.source_kind not in _SOURCE_KIND_VALUES:
            raise ValueError(
                f"entry source_kind must be one of {sorted(_SOURCE_KIND_VALUES)}, "
                f"got {self.source_kind!r}"
            )
        if self.value_type == "string" and not isinstance(self.value, str):
            raise ValueError("value_type=string requires a str value")
        if self.value_type == "number" and (
            isinstance(self.value, bool) or not isinstance(self.value, (int, float))
        ):
            raise ValueError("value_type=number requires an int/float value")
        if self.value_type == "boolean" and not isinstance(self.value, bool):
            raise ValueError("value_type=boolean requires a bool value")
        if self.value_type == "string_list":
            if not isinstance(self.value, list) or not all(
                isinstance(item, str) for item in self.value
            ):
                raise ValueError("value_type=string_list requires a list[str] value")
        return self


class ProjectionDocument(BaseModel):
    """一次构建产出的完整投影文档（存入 ai_memory_projection.payload）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: MemorySubject
    function_key: ProjectionFunctionKey
    purpose: ProjectionPurpose
    data_category: ProjectionDataCategory
    policy_revision: str = Field(..., min_length=1, max_length=64)
    consent_snapshot_id: str = Field(..., min_length=1, max_length=128)
    projection_version: int = Field(..., ge=1)
    projection_input_hash: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    entries: tuple[ProjectionEntry, ...] = ()

    @model_validator(mode="after")
    def _validate(self) -> "ProjectionDocument":
        expected_subject = _SUBJECT_BY_DATA_CATEGORY.get(self.data_category.value)
        if (
            expected_subject is not None
            and self.subject.value != expected_subject
        ):
            raise ValueError(
                f"data_category {self.data_category.value!r} requires subject "
                f"{expected_subject!r}, got {self.subject.value!r}"
            )
        for entry in self.entries:
            if entry.projection_version != self.projection_version:
                raise ValueError(
                    "entry projection_version must match the document version "
                    f"({entry.projection_version} != {self.projection_version})"
                )
        return self


class ProjectionGrantRequest(BaseModel):
    """grant 入参：必须绑定授权快照 id 与策略版本（缺一不可）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    owner_user_id: int = Field(..., ge=1)
    function_key: ProjectionFunctionKey
    purpose: ProjectionPurpose
    data_category: ProjectionDataCategory
    consent_snapshot_id: str = Field(..., min_length=1, max_length=128)
    policy_revision: str = Field(..., min_length=1, max_length=64)


_SUBJECT_BY_DATA_CATEGORY: dict[str, str] = {
    "personal_profile": "personal",
    "public_profile_summary": "personal",
    "ideal_partner_preference": "ideal_partner",
    # compatibility_features 是派生匹配特征集：两个主体都可作为输入。
    "compatibility_features": None,
}

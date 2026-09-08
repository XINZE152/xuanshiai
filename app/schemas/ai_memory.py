"""Memory Kernel Core v1 冻结契约 schema（架构文档 ai-memory-kernel-core-v1）。

本模块是记忆内核的"打印契约"层：事件信封、五种节点 payload、状态机与
来源优先级全部在此冻结，供 Ledger / Policy / Materializer / Service / API
共同引用。核心不变量：

- ``ai_memory_event`` 是 append-only 事实源；Observation / Claim / Insight /
  State / Suppression 只是由事件物化出的当前视图。
- subject 只接受 ``personal`` 与 ``ideal_partner``；ideal_partner 永远是当前
  用户自己的偏好，不是第三方资料（payload 级用 ``fact_kind`` 双向锁死）。
- 来源优先级固定 user_explicit > user_confirmed > user_feedback > inferred >
  behavior；冲突不得静默 LWW（由 Policy 在物化时执行）。
- State 必须携带 ``valid_until``（TTL），只影响会话上下文。
- 只保存最小 ``source_quote``（≤512 字符）/``source_ref``；完整 transcript
  不得进入事件 payload（payload 模型全部 ``extra="forbid"``）。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.db.ai_schema import PROFILE_DIMENSION_SET
from app.schemas.ai_common import AiConsentScope

# ---------------------------------------------------------------------------
# 冻结词汇表
# ---------------------------------------------------------------------------

MEMORY_SUBJECTS: frozenset[str] = frozenset({"personal", "ideal_partner"})
# 本期只开放墨相师 namespace；AI 军师 / AI 分身 namespace 为明确延期项。
MEMORY_NAMESPACES: frozenset[str] = frozenset({"moxiang"})

MAX_SOURCE_QUOTE_LENGTH = 512


class MemorySubject(str, Enum):
    """记忆主体：只允许用户本人画像与理想伴侣偏好两个主体。"""

    PERSONAL = "personal"
    IDEAL_PARTNER = "ideal_partner"


class MemoryNamespace(str, Enum):
    """记忆命名空间：本期仅墨相师（延期项：advisor / avatar）。"""

    MOXIANG = "moxiang"


class MemoryNodeType(str, Enum):
    """五种物化视图对应的节点类型。"""

    OBSERVATION = "observation"
    CLAIM = "claim"
    INSIGHT = "insight"
    STATE = "state"
    SUPPRESSION = "suppression"


class MemorySourceKind(str, Enum):
    """来源类型；优先级见 ``MEMORY_SOURCE_RANK``（固定，不可静默 LWW）。"""

    USER_EXPLICIT = "user_explicit"
    USER_CONFIRMED = "user_confirmed"
    USER_FEEDBACK = "user_feedback"
    INFERRED = "inferred"
    BEHAVIOR = "behavior"


# 优先级数值越大越高；同名 canonical key 冲突时 Policy 依据该表裁决。
MEMORY_SOURCE_RANK: dict[str, int] = {
    MemorySourceKind.USER_EXPLICIT.value: 5,
    MemorySourceKind.USER_CONFIRMED.value: 4,
    MemorySourceKind.USER_FEEDBACK.value: 3,
    MemorySourceKind.INFERRED.value: 2,
    MemorySourceKind.BEHAVIOR.value: 1,
}


class MemoryObservationStatus(str, Enum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    CONTRADICTED = "contradicted"
    USER_CORRECTED = "user_corrected"
    EXPIRED = "expired"


class MemoryClaimStatus(str, Enum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    SUPERSEDED = "superseded"
    CONTRADICTED = "contradicted"
    USER_CORRECTED = "user_corrected"
    EXPIRED = "expired"


class MemoryInsightStatus(str, Enum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    INVALIDATED = "invalidated"
    SUPERSEDED = "superseded"


class MemoryStateStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    USER_DELETED = "user_deleted"


class MemorySuppressionStatus(str, Enum):
    ACTIVE = "active"
    LIFTED = "lifted"


# 每种节点类型的初始状态（计划 §2 冻结契约表）。
MEMORY_NODE_INITIAL_STATUS: dict[str, str] = {
    MemoryNodeType.OBSERVATION.value: "proposed",
    MemoryNodeType.CLAIM.value: "proposed",
    MemoryNodeType.INSIGHT.value: "proposed",
    MemoryNodeType.STATE.value: "active",
    MemoryNodeType.SUPPRESSION.value: "active",
}


class MemoryEventType(str, Enum):
    """事件类型全集；每个值在 ``MEMORY_EVENT_TRANSITIONS`` 有且仅有一条规则。"""

    OBSERVATION_PROPOSED = "observation_proposed"
    OBSERVATION_ACTIVATED = "observation_activated"
    OBSERVATION_SUPERSEDED = "observation_superseded"
    OBSERVATION_CONTRADICTED = "observation_contradicted"
    OBSERVATION_USER_CORRECTED = "observation_user_corrected"
    OBSERVATION_EXPIRED = "observation_expired"

    CLAIM_PROPOSED = "claim_proposed"
    CLAIM_CONFIRMED = "claim_confirmed"
    CLAIM_SUPERSEDED = "claim_superseded"
    CLAIM_CONTRADICTED = "claim_contradicted"
    CLAIM_USER_CORRECTED = "claim_user_corrected"
    CLAIM_EXPIRED = "claim_expired"

    INSIGHT_PROPOSED = "insight_proposed"
    INSIGHT_CONFIRMED = "insight_confirmed"
    INSIGHT_INVALIDATED = "insight_invalidated"
    INSIGHT_SUPERSEDED = "insight_superseded"

    STATE_ACTIVATED = "state_activated"
    STATE_EXPIRED = "state_expired"
    STATE_USER_DELETED = "state_user_deleted"

    SUPPRESSION_ACTIVATED = "suppression_activated"
    SUPPRESSION_LIFTED = "suppression_lifted"


class MemoryTransition(NamedTuple):
    """一条事件类型的转移规则。

    ``from_statuses`` 为空 frozenset 表示"创建"语义：目标节点必须尚不存在。
    """

    node_type: MemoryNodeType
    from_statuses: frozenset[str]
    to_status: str


MEMORY_EVENT_TRANSITIONS: dict[str, MemoryTransition] = {
    MemoryEventType.OBSERVATION_PROPOSED.value: MemoryTransition(
        MemoryNodeType.OBSERVATION, frozenset(), "proposed"
    ),
    MemoryEventType.OBSERVATION_ACTIVATED.value: MemoryTransition(
        MemoryNodeType.OBSERVATION, frozenset({"proposed"}), "active"
    ),
    MemoryEventType.OBSERVATION_SUPERSEDED.value: MemoryTransition(
        MemoryNodeType.OBSERVATION, frozenset({"proposed", "active"}), "superseded"
    ),
    MemoryEventType.OBSERVATION_CONTRADICTED.value: MemoryTransition(
        MemoryNodeType.OBSERVATION, frozenset({"proposed", "active"}), "contradicted"
    ),
    MemoryEventType.OBSERVATION_USER_CORRECTED.value: MemoryTransition(
        MemoryNodeType.OBSERVATION, frozenset({"proposed", "active"}), "user_corrected"
    ),
    MemoryEventType.OBSERVATION_EXPIRED.value: MemoryTransition(
        MemoryNodeType.OBSERVATION, frozenset({"proposed", "active"}), "expired"
    ),
    MemoryEventType.CLAIM_PROPOSED.value: MemoryTransition(
        MemoryNodeType.CLAIM, frozenset(), "proposed"
    ),
    MemoryEventType.CLAIM_CONFIRMED.value: MemoryTransition(
        MemoryNodeType.CLAIM, frozenset({"proposed"}), "confirmed"
    ),
    MemoryEventType.CLAIM_SUPERSEDED.value: MemoryTransition(
        MemoryNodeType.CLAIM, frozenset({"proposed", "confirmed"}), "superseded"
    ),
    MemoryEventType.CLAIM_CONTRADICTED.value: MemoryTransition(
        MemoryNodeType.CLAIM, frozenset({"proposed", "confirmed"}), "contradicted"
    ),
    MemoryEventType.CLAIM_USER_CORRECTED.value: MemoryTransition(
        MemoryNodeType.CLAIM, frozenset({"proposed", "confirmed"}), "user_corrected"
    ),
    MemoryEventType.CLAIM_EXPIRED.value: MemoryTransition(
        MemoryNodeType.CLAIM, frozenset({"proposed", "confirmed"}), "expired"
    ),
    MemoryEventType.INSIGHT_PROPOSED.value: MemoryTransition(
        MemoryNodeType.INSIGHT, frozenset(), "proposed"
    ),
    MemoryEventType.INSIGHT_CONFIRMED.value: MemoryTransition(
        MemoryNodeType.INSIGHT, frozenset({"proposed"}), "confirmed"
    ),
    MemoryEventType.INSIGHT_INVALIDATED.value: MemoryTransition(
        MemoryNodeType.INSIGHT, frozenset({"proposed", "confirmed"}), "invalidated"
    ),
    MemoryEventType.INSIGHT_SUPERSEDED.value: MemoryTransition(
        MemoryNodeType.INSIGHT, frozenset({"proposed", "confirmed"}), "superseded"
    ),
    MemoryEventType.STATE_ACTIVATED.value: MemoryTransition(
        MemoryNodeType.STATE, frozenset(), "active"
    ),
    MemoryEventType.STATE_EXPIRED.value: MemoryTransition(
        MemoryNodeType.STATE, frozenset({"active"}), "expired"
    ),
    MemoryEventType.STATE_USER_DELETED.value: MemoryTransition(
        MemoryNodeType.STATE, frozenset({"active"}), "user_deleted"
    ),
    MemoryEventType.SUPPRESSION_ACTIVATED.value: MemoryTransition(
        MemoryNodeType.SUPPRESSION, frozenset(), "active"
    ),
    MemoryEventType.SUPPRESSION_LIFTED.value: MemoryTransition(
        MemoryNodeType.SUPPRESSION, frozenset({"active"}), "lifted"
    ),
}

# fact_kind 词汇表：personal 主体只能写 about_user 事实，ideal_partner 主体
# 只能写 partner_preference 偏好。双向锁死，防止第三方评价或自我事实串主体。
FACT_KIND_ABOUT_USER = "about_user"
FACT_KIND_PARTNER_PREFERENCE = "partner_preference"
MemoryFactKind = Literal["about_user", "partner_preference"]

_MEMORY_CONSTRAINT_TYPE_MAX_LENGTH = 32


# ---------------------------------------------------------------------------
# 节点 payload 模型（append-only 事件 payload 的最小形状）
# ---------------------------------------------------------------------------


class _MemoryPayloadBase(BaseModel):
    """payload 公共约束：禁未知字段（transcript 等不得进入事件）。"""

    model_config = ConfigDict(extra="forbid")


class MemoryObservationPayload(_MemoryPayloadBase):
    """一次有来源的观察/证据；含 confidence 与最小引用。"""

    canonical_key: str = Field(..., min_length=1, max_length=160)
    dimension: str | None = Field(default=None, max_length=64)
    value: Any = None
    confidence: float = Field(..., ge=0.0, le=1.0)
    fact_kind: MemoryFactKind
    note: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _validate_dimension(self) -> "MemoryObservationPayload":
        if self.dimension is not None and self.dimension not in PROFILE_DIMENSION_SET:
            raise ValueError(
                f"dimension must be one of {sorted(PROFILE_DIMENSION_SET)}, got {self.dimension!r}"
            )
        return self


class MemoryClaimPayload(_MemoryPayloadBase):
    """同一事实的规范化归并；只有用户确认后 status 才能成为 confirmed。"""

    canonical_key: str = Field(..., min_length=1, max_length=160)
    dimension: str | None = Field(default=None, max_length=64)
    value: Any = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    stability: float = Field(..., ge=0.0, le=1.0)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    constraint_type: str | None = Field(default=None, max_length=_MEMORY_CONSTRAINT_TYPE_MAX_LENGTH)
    # 只有用户确认动作能把 importance_confirmed 置 True；AI 推荐值不得设置。
    importance_confirmed: bool = False
    fact_kind: MemoryFactKind

    @model_validator(mode="after")
    def _validate_dimension(self) -> "MemoryClaimPayload":
        if self.dimension is not None and self.dimension not in PROFILE_DIMENSION_SET:
            raise ValueError(
                f"dimension must be one of {sorted(PROFILE_DIMENSION_SET)}, got {self.dimension!r}"
            )
        return self


class MemoryInsightPayload(_MemoryPayloadBase):
    """从 Claim 派生的洞察；只存摘要与 Claim ids，不复制 transcript。

    ``insight_id`` 仅在生命周期事件（confirmed/invalidated/superseded）上出现，
    用于定位目标 insight 行；proposed 事件留空（由 event_id 派生）。
    """

    summary: str = Field(..., min_length=1, max_length=200)
    claim_ids: tuple[str, ...] = Field(..., min_length=1)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    insight_id: str | None = Field(default=None, max_length=64)


class MemoryStatePayload(_MemoryPayloadBase):
    """会话上下文状态；必须携带 TTL（valid_until），不得进入长期画像。

    ``state_id`` 仅在生命周期事件（expired/user_deleted）上出现，用于定位目标
    state 行；activated 事件留空（由 event_id 派生）。
    """

    canonical_key: str = Field(..., min_length=1, max_length=160)
    value: Any = None
    valid_until: datetime
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    state_id: str | None = Field(default=None, max_length=64)


class MemorySuppressionPayload(_MemoryPayloadBase):
    """用户删除墓碑；阻止同 canonical key 自动重抽取。"""

    canonical_key: str = Field(..., min_length=1, max_length=160)
    reason: str | None = Field(default=None, max_length=200)


_PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    MemoryNodeType.OBSERVATION.value: MemoryObservationPayload,
    MemoryNodeType.CLAIM.value: MemoryClaimPayload,
    MemoryNodeType.INSIGHT.value: MemoryInsightPayload,
    MemoryNodeType.STATE.value: MemoryStatePayload,
    MemoryNodeType.SUPPRESSION.value: MemorySuppressionPayload,
}


def payload_model_for(node_type: MemoryNodeType | str) -> type[BaseModel]:
    """返回节点类型对应的 payload 模型；未知节点类型抛 ValueError。"""

    key = node_type.value if isinstance(node_type, MemoryNodeType) else str(node_type)
    try:
        return _PAYLOAD_MODELS[key]
    except KeyError as exc:
        raise ValueError(f"unknown memory node_type: {node_type!r}") from exc


# ---------------------------------------------------------------------------
# 事件信封
# ---------------------------------------------------------------------------


def _validate_event_consistency(
    subject: MemorySubject | str,
    node_type: MemoryNodeType | str,
    event_type: MemoryEventType | str,
    payload: dict[str, Any],
) -> None:
    """共享一致性校验：转移规则、payload 形状、fact_kind 与 subject 互锁。"""

    subject_value = subject.value if isinstance(subject, MemorySubject) else str(subject)
    node_value = node_type.value if isinstance(node_type, MemoryNodeType) else str(node_type)
    event_value = event_type.value if isinstance(event_type, MemoryEventType) else str(event_type)

    transition = MEMORY_EVENT_TRANSITIONS.get(event_value)
    if transition is None:
        raise ValueError(f"unknown memory event_type: {event_value!r}")
    if transition.node_type.value != node_value:
        raise ValueError(
            f"event_type {event_value!r} belongs to node_type {transition.node_type.value!r}, "
            f"got {node_value!r}"
        )

    payload_model = payload_model_for(node_value)
    try:
        payload_model.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"payload does not match {payload_model.__name__}: {exc}") from exc

    if node_value in (MemoryNodeType.OBSERVATION.value, MemoryNodeType.CLAIM.value):
        expected_fact_kind = (
            FACT_KIND_ABOUT_USER
            if subject_value == MemorySubject.PERSONAL.value
            else FACT_KIND_PARTNER_PREFERENCE
        )
        actual_fact_kind = payload.get("fact_kind")
        if actual_fact_kind != expected_fact_kind:
            raise ValueError(
                f"subject {subject_value!r} requires fact_kind={expected_fact_kind!r}, "
                f"got {actual_fact_kind!r}"
            )


class MemoryEventInput(BaseModel):
    """调用方提交给 Ledger 的事件；event_id / server_seq / occurred_at 由服务端分配。"""

    model_config = ConfigDict(extra="forbid")

    owner_user_id: int = Field(..., ge=1)
    subject: MemorySubject
    namespace: MemoryNamespace = MemoryNamespace.MOXIANG
    node_type: MemoryNodeType
    event_type: MemoryEventType
    payload: dict[str, Any]
    source_kind: MemorySourceKind
    source_turn_id: str | None = Field(default=None, max_length=64)
    source_ref: str | None = Field(default=None, max_length=256)
    source_quote: str | None = Field(default=None, max_length=MAX_SOURCE_QUOTE_LENGTH)
    causal_event_ids: tuple[str, ...] = ()
    consent_scope: AiConsentScope = "profile_text_extract"
    idempotency_key: str = Field(..., min_length=1, max_length=128)

    @model_validator(mode="after")
    def _validate(self) -> "MemoryEventInput":
        _validate_event_consistency(
            self.subject, self.node_type, self.event_type, self.payload
        )
        return self

    def typed_payload(self) -> Any:
        """按节点类型返回强类型 payload 实例（已通过信封级校验）。"""

        return payload_model_for(self.node_type).model_validate(self.payload)


class MemoryEventRecord(BaseModel):
    """账本中一条已落库事件（append-only，创建后不可变）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(..., min_length=1, max_length=64)
    server_seq: int = Field(..., ge=1)
    owner_user_id: int = Field(..., ge=1)
    subject: MemorySubject
    namespace: MemoryNamespace = MemoryNamespace.MOXIANG
    node_type: MemoryNodeType
    event_type: MemoryEventType
    payload: dict[str, Any]
    source_kind: MemorySourceKind
    source_turn_id: str | None = Field(default=None, max_length=64)
    source_ref: str | None = Field(default=None, max_length=256)
    source_quote: str | None = Field(default=None, max_length=MAX_SOURCE_QUOTE_LENGTH)
    causal_event_ids: tuple[str, ...] = ()
    consent_scope: AiConsentScope = "profile_text_extract"
    idempotency_key: str = Field(..., min_length=1, max_length=128)
    occurred_at: datetime

    @model_validator(mode="after")
    def _validate(self) -> "MemoryEventRecord":
        _validate_event_consistency(
            self.subject, self.node_type, self.event_type, self.payload
        )
        return self

    def typed_payload(self) -> Any:
        return payload_model_for(self.node_type).model_validate(self.payload)


class MemoryIdempotencyConflict(Exception):
    """相同 owner + idempotency_key 但 payload 摘要不同时的稳定冲突。"""

    def __init__(
        self,
        *,
        owner_user_id: int,
        idempotency_key: str,
        existing_event_id: str | None = None,
    ) -> None:
        self.owner_user_id = owner_user_id
        self.idempotency_key = idempotency_key
        self.existing_event_id = existing_event_id
        super().__init__(
            f"memory idempotency conflict: owner_user_id={owner_user_id} "
            f"idempotency_key={idempotency_key} existing_event_id={existing_event_id}"
        )

"""Memory Kernel Core v1 schema contract tests (Task 1).

Written before the implementation exists: they fail with ImportError until
``app.schemas.ai_memory`` provides the frozen enums, event schemas and the
idempotency-conflict error.  The cases mirror the frozen contract in the
execution plan §2:

- subject only accepts ``personal`` / ``ideal_partner`` (third-party rejected);
- ``source_quote`` is capped at 512 characters;
- ``source_kind`` only accepts the frozen five-value priority list;
- confidence / stability are bounded to [0, 1];
- State payloads must carry ``valid_until`` (TTL);
- ideal_partner events may never carry personal-fact payloads.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.schemas.ai_memory import (
    MAX_SOURCE_QUOTE_LENGTH,
    MEMORY_EVENT_TRANSITIONS,
    MEMORY_NAMESPACES,
    MEMORY_SOURCE_RANK,
    MEMORY_SUBJECTS,
    MemoryClaimPayload,
    MemoryEventInput,
    MemoryEventRecord,
    MemoryEventType,
    MemoryIdempotencyConflict,
    MemoryNodeType,
    MemoryObservationPayload,
    MemorySourceKind,
    MemoryStatePayload,
    MemorySubject,
    payload_model_for,
)

OBSERVATION_PAYLOAD = {
    "canonical_key": "personal:lifestyle:coffee",
    "value": "每天喝咖啡",
    "confidence": 0.82,
    "fact_kind": "about_user",
}

CLAIM_PAYLOAD = {
    "canonical_key": "personal:lifestyle:coffee",
    "value": "每天喝咖啡",
    "stability": 0.7,
    "importance": 0.4,
    "fact_kind": "about_user",
}


def _base_event(**overrides):
    fields = {
        "owner_user_id": 42,
        "subject": "personal",
        "node_type": "observation",
        "event_type": "observation_proposed",
        "payload": dict(OBSERVATION_PAYLOAD),
        "source_kind": "user_explicit",
        "source_quote": "我每天早上都要喝一杯咖啡",
        "idempotency_key": "seed-observation-001",
    }
    fields.update(overrides)
    return MemoryEventInput(**fields)


# ---------------------------------------------------------------------------
# subject 隔离
# ---------------------------------------------------------------------------


def test_unknown_subject_rejected() -> None:
    with pytest.raises(ValidationError):
        _base_event(subject="spouse")


def test_third_party_subject_rejected() -> None:
    with pytest.raises(ValidationError):
        _base_event(subject="third_party_profile")


def test_subject_vocabulary_is_frozen() -> None:
    assert MEMORY_SUBJECTS == frozenset({"personal", "ideal_partner"})
    assert {s.value for s in MemorySubject} == {"personal", "ideal_partner"}


def test_ideal_partner_cannot_write_personal_facts() -> None:
    with pytest.raises(ValidationError):
        _base_event(
            subject="ideal_partner",
            payload={**OBSERVATION_PAYLOAD, "fact_kind": "about_user"},
        )
    with pytest.raises(ValidationError):
        _base_event(
            subject="ideal_partner",
            node_type="claim",
            event_type="claim_proposed",
            payload={**CLAIM_PAYLOAD, "fact_kind": "about_user"},
        )


def test_personal_cannot_write_partner_preferences() -> None:
    with pytest.raises(ValidationError):
        _base_event(
            subject="personal",
            payload={**OBSERVATION_PAYLOAD, "fact_kind": "partner_preference"},
        )


def test_ideal_partner_preference_event_accepted() -> None:
    event = _base_event(
        subject="ideal_partner",
        payload={
            "canonical_key": "ideal_partner:personality:stable",
            "value": "希望对方情绪稳定",
            "confidence": 0.9,
            "fact_kind": "partner_preference",
        },
        source_quote="我希望未来伴侣情绪稳定一点",
    )
    assert event.subject == MemorySubject.IDEAL_PARTNER


# ---------------------------------------------------------------------------
# 隐私最小化与来源约束
# ---------------------------------------------------------------------------


def test_source_quote_at_512_ok_and_513_rejected() -> None:
    quote_512 = "喝" * 512
    assert MAX_SOURCE_QUOTE_LENGTH == 512
    _base_event(source_quote=quote_512)
    with pytest.raises(ValidationError):
        _base_event(source_quote="喝" * 513)


def test_invalid_source_kind_rejected() -> None:
    with pytest.raises(ValidationError):
        _base_event(source_kind="rumor")


def test_source_priority_order_is_frozen() -> None:
    order = [
        MemorySourceKind.USER_EXPLICIT,
        MemorySourceKind.USER_CONFIRMED,
        MemorySourceKind.USER_FEEDBACK,
        MemorySourceKind.INFERRED,
        MemorySourceKind.BEHAVIOR,
    ]
    ranks = [MEMORY_SOURCE_RANK[k.value] for k in order]
    assert ranks == sorted(ranks, reverse=True)
    assert len(set(ranks)) == len(ranks)
    assert ranks[0] > ranks[-1]


# ---------------------------------------------------------------------------
# 数值边界
# ---------------------------------------------------------------------------


def test_confidence_out_of_bounds_rejected() -> None:
    with pytest.raises(ValidationError):
        MemoryObservationPayload(**{**OBSERVATION_PAYLOAD, "confidence": 1.5})
    with pytest.raises(ValidationError):
        MemoryObservationPayload(**{**OBSERVATION_PAYLOAD, "confidence": -0.1})


def test_stability_out_of_bounds_rejected() -> None:
    with pytest.raises(ValidationError):
        MemoryClaimPayload(**{**CLAIM_PAYLOAD, "stability": 1.2})
    with pytest.raises(ValidationError):
        MemoryClaimPayload(**{**CLAIM_PAYLOAD, "stability": -0.01})


def test_owner_user_id_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        _base_event(owner_user_id=0)
    with pytest.raises(ValidationError):
        _base_event(owner_user_id=-7)


# ---------------------------------------------------------------------------
# State TTL
# ---------------------------------------------------------------------------


def test_state_requires_valid_until() -> None:
    with pytest.raises(ValidationError):
        MemoryStatePayload(canonical_key="session:context:mood", value="今晚想聊轻松的")
    future = datetime.now(UTC) + timedelta(minutes=30)
    payload = MemoryStatePayload(
        canonical_key="session:context:mood", value="今晚想聊轻松的", valid_until=future
    )
    assert payload.valid_until == future


def test_state_event_without_valid_until_rejected() -> None:
    with pytest.raises(ValidationError):
        _base_event(
            node_type="state",
            event_type="state_activated",
            payload={"canonical_key": "session:context:mood", "value": "轻松话题"},
        )


# ---------------------------------------------------------------------------
# 事件类型 / 节点类型一致性与状态机
# ---------------------------------------------------------------------------


def test_event_type_must_match_node_type() -> None:
    with pytest.raises(ValidationError):
        _base_event(node_type="observation", event_type="claim_confirmed")


def test_transition_table_covers_every_event_type() -> None:
    for event_type in MemoryEventType:
        assert event_type.value in MEMORY_EVENT_TRANSITIONS
        node_type, from_statuses, to_status = MEMORY_EVENT_TRANSITIONS[event_type.value]
        assert isinstance(node_type, MemoryNodeType)
        assert isinstance(from_statuses, frozenset)
        assert isinstance(to_status, str)


def test_claim_confirm_transition_only_from_proposed() -> None:
    node_type, from_statuses, to_status = MEMORY_EVENT_TRANSITIONS["claim_confirmed"]
    assert node_type == MemoryNodeType.CLAIM
    assert from_statuses == frozenset({"proposed"})
    assert to_status == "confirmed"


# ---------------------------------------------------------------------------
# payload 模型注册与 namespace
# ---------------------------------------------------------------------------


def test_payload_model_registry_covers_all_node_types() -> None:
    for node_type in MemoryNodeType:
        assert payload_model_for(node_type) is not None


def test_namespace_vocabulary_is_frozen_this_phase() -> None:
    assert MEMORY_NAMESPACES == frozenset({"moxiang"})


def test_extra_payload_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        _base_event(payload={**OBSERVATION_PAYLOAD, "transcript": "完整聊天记录……"})


# ---------------------------------------------------------------------------
# 记录冻结与幂等冲突
# ---------------------------------------------------------------------------


def _record_event() -> MemoryEventRecord:
    return MemoryEventRecord(
        event_id="evt-0001",
        server_seq=7,
        owner_user_id=42,
        subject="personal",
        namespace="moxiang",
        node_type="observation",
        event_type="observation_proposed",
        payload=OBSERVATION_PAYLOAD,
        source_kind="user_explicit",
        source_quote="我每天早上都要喝一杯咖啡",
        causal_event_ids=(),
        consent_scope="profile_text_extract",
        idempotency_key="seed-observation-001",
        occurred_at=datetime.now(UTC),
    )


def test_event_record_is_frozen() -> None:
    record = _record_event()
    with pytest.raises(ValidationError):
        record.source_quote = "篡改后的引用"


def test_idempotency_conflict_error_shape() -> None:
    error = MemoryIdempotencyConflict(
        owner_user_id=42,
        idempotency_key="seed-observation-001",
        existing_event_id="evt-0000",
    )
    assert error.owner_user_id == 42
    assert error.idempotency_key == "seed-observation-001"
    assert error.existing_event_id == "evt-0000"
    assert "seed-observation-001" in str(error)

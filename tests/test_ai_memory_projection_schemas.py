"""Memory Projection Phase 2 frozen contract tests (Task 1).

The projection vocabulary (function × purpose × data_category), the 10-field
entry allowlist and the subject↔category interlock are frozen by the plan
§2.  Raw quotes / transcripts / unauthorized fields must be unrepresentable
(``extra="forbid"``), and grants must bind a consent snapshot id plus policy
revision.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.ai_memory_projection import (
    MEMORY_PROJECTION_ENTRY_ALLOWLIST,
    PROJECTION_DATA_CATEGORIES,
    PROJECTION_FUTURE_FUNCTIONS,
    PROJECTION_FUNCTIONS,
    PROJECTION_PURPOSES,
    ProjectionDataCategory,
    ProjectionDocument,
    ProjectionEntry,
    ProjectionFunctionKey,
    ProjectionGrantRequest,
    ProjectionPurpose,
)


def _entry_kwargs(**overrides):
    fields = {
        "field_key": "interest_tags",
        "value": ["hiking", "coffee"],
        "value_type": "string_list",
        "source_kind": "user_confirmed",
        "claim_id": "clm_abc123",
        "stability": 0.85,
        "importance": 0.5,
        "constraint_type": None,
        "projection_version": 1,
        "evidence_ref": "memory:claim:clm_abc123",
    }
    fields.update(overrides)
    return fields


def _document_kwargs(**overrides):
    fields = {
        "subject": "personal",
        "function_key": "search",
        "purpose": "candidate_filter",
        "data_category": "personal_profile",
        "policy_revision": "ai-policy-2026-08-07-v1",
        "consent_snapshot_id": "cs-123",
        "projection_version": 1,
        "projection_input_hash": "a" * 64,
        "entries": (ProjectionEntry(**_entry_kwargs()),),
    }
    fields.update(overrides)
    return fields


# ---------------------------------------------------------------------------
# 冻结词汇表
# ---------------------------------------------------------------------------


def test_vocabulary_frozen() -> None:
    assert PROJECTION_FUNCTIONS == frozenset(
        {"search", "compatibility", "recommend", "counselor_context", "persona_context"}
    )
    # Phase 3 起 future 集为空（consumer key 已启用），集合本身保留。
    assert PROJECTION_FUTURE_FUNCTIONS == frozenset()
    assert PROJECTION_PURPOSES == frozenset(
        {"candidate_filter", "candidate_rank", "explanation", "session_context"}
    )
    assert PROJECTION_DATA_CATEGORIES == frozenset(
        {
            "personal_profile",
            "ideal_partner_preference",
            "compatibility_features",
            "public_profile_summary",
        }
    )
    assert {k.value for k in ProjectionFunctionKey} == PROJECTION_FUNCTIONS
    assert {p.value for p in ProjectionPurpose} == PROJECTION_PURPOSES
    assert {c.value for c in ProjectionDataCategory} == PROJECTION_DATA_CATEGORIES


# ---------------------------------------------------------------------------
# 未知枚举拒绝（API 层映射 422）
# ---------------------------------------------------------------------------


def _grant_kwargs(**overrides):
    fields = {
        "owner_user_id": 42,
        "function_key": "search",
        "purpose": "candidate_filter",
        "data_category": "personal_profile",
        "consent_snapshot_id": "cs-1",
        "policy_revision": "ai-policy-2026-08-07-v1",
    }
    fields.update(overrides)
    return fields


def test_unknown_function_key_rejected() -> None:
    with pytest.raises(ValidationError):
        ProjectionGrantRequest(**_grant_kwargs(function_key="matchmaking"))


def test_unknown_purpose_rejected() -> None:
    with pytest.raises(ValidationError):
        ProjectionGrantRequest(**_grant_kwargs(purpose="ad_targeting"))


def test_unknown_data_category_rejected() -> None:
    with pytest.raises(ValidationError):
        ProjectionGrantRequest(**_grant_kwargs(data_category="third_party_profile"))


def test_grant_requires_consent_snapshot_and_policy_revision() -> None:
    with pytest.raises(ValidationError):
        ProjectionGrantRequest(**_grant_kwargs(consent_snapshot_id=""))
    with pytest.raises(ValidationError):
        ProjectionGrantRequest(**_grant_kwargs(policy_revision=""))


# ---------------------------------------------------------------------------
# Entry allowlist：禁止 raw quote / transcript / 未授权字段
# ---------------------------------------------------------------------------


def test_entry_allowlist_is_exactly_the_frozen_ten_fields() -> None:
    assert set(ProjectionEntry.model_fields) == MEMORY_PROJECTION_ENTRY_ALLOWLIST
    assert MEMORY_PROJECTION_ENTRY_ALLOWLIST == frozenset(
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


def test_entry_rejects_raw_quote_and_transcript() -> None:
    with pytest.raises(ValidationError):
        ProjectionEntry(**_entry_kwargs(source_quote="我每天早上都要喝一杯咖啡"))
    with pytest.raises(ValidationError):
        ProjectionEntry(**_entry_kwargs(transcript="整段对话原文"))


def test_entry_rejects_unauthorized_fields() -> None:
    with pytest.raises(ValidationError):
        ProjectionEntry(**_entry_kwargs(confidence=0.9))
    with pytest.raises(ValidationError):
        ProjectionEntry(**_entry_kwargs(note="内部备注"))


def test_entry_value_type_mismatch_rejected() -> None:
    with pytest.raises(ValidationError):
        ProjectionEntry(**_entry_kwargs(value_type="number", value="abc"))
    with pytest.raises(ValidationError):
        ProjectionEntry(**_entry_kwargs(value_type="boolean", value="yes"))
    with pytest.raises(ValidationError):
        ProjectionEntry(**_entry_kwargs(value_type="string_list", value=[1, 2]))
    ok = ProjectionEntry(**_entry_kwargs(value_type="number", value=175))
    assert ok.value == 175


def test_entry_field_key_is_safe_identifier() -> None:
    with pytest.raises(ValidationError):
        ProjectionEntry(**_entry_kwargs(field_key="DROP TABLE; --"))
    with pytest.raises(ValidationError):
        ProjectionEntry(**_entry_kwargs(field_key="我每天喝咖啡的原文"))


def test_entry_evidence_ref_bounded() -> None:
    with pytest.raises(ValidationError):
        ProjectionEntry(**_entry_kwargs(evidence_ref="x" * 129))


# ---------------------------------------------------------------------------
# 文档级：subject ↔ data_category 互锁
# ---------------------------------------------------------------------------


def test_document_rejects_subject_category_mismatch() -> None:
    with pytest.raises(ValidationError):
        ProjectionDocument(**_document_kwargs(subject="ideal_partner"))
    with pytest.raises(ValidationError):
        ProjectionDocument(
            **_document_kwargs(
                subject="personal",
                data_category="ideal_partner_preference",
            )
        )


def test_document_accepts_compatibility_features_from_both_subjects() -> None:
    doc = ProjectionDocument(
        **_document_kwargs(
            function_key="compatibility",
            purpose="candidate_rank",
            data_category="compatibility_features",
        )
    )
    assert doc.subject == "personal"
    doc2 = ProjectionDocument(
        **_document_kwargs(
            subject="ideal_partner",
            function_key="compatibility",
            purpose="candidate_rank",
            data_category="compatibility_features",
        )
    )
    assert doc2.subject == "ideal_partner"


def test_document_rejects_entry_version_mismatch() -> None:
    with pytest.raises(ValidationError):
        ProjectionDocument(
            **_document_kwargs(
                entries=(ProjectionEntry(**_entry_kwargs(projection_version=2)),)
            )
        )


def test_document_dumps_minimal_shape() -> None:
    doc = ProjectionDocument(**_document_kwargs())
    payload = doc.model_dump(mode="json")
    assert set(payload) == {
        "subject",
        "function_key",
        "purpose",
        "data_category",
        "policy_revision",
        "consent_snapshot_id",
        "projection_version",
        "projection_input_hash",
        "entries",
    }
    assert "source_quote" not in payload["entries"][0]

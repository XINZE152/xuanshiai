"""Memory Projection policy tests (Task 1).

ProjectionPolicy is the pure enforcement point: future function keys fail
closed, subject↔category interlock, only confirmed Claims may enter the
long-term projection, the entry allowlist is exact, and the input hash is a
stable canonical fingerprint that excludes version/evidence fields.
"""

from __future__ import annotations

import pytest

from app.services.ai.memory.projection_policy import (
    MEMORY_PROJECTION_ENTRY_ALLOWLIST,
    ProjectionFeatureNotEnabled,
    ProjectionPolicy,
    ProjectionPolicyDenied,
)


def _entry(**overrides):
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


def _content_entry(**overrides):
    """构建期的 entry：还没有 projection_version / evidence_ref。"""

    entry = _entry(**overrides)
    entry.pop("projection_version")
    entry.pop("evidence_ref")
    return entry


# ---------------------------------------------------------------------------
# function key：future key fail closed
# ---------------------------------------------------------------------------


def test_consumer_keys_enabled_phase3() -> None:
    """Phase 3 起 counselor/persona 消费 key 已启用；未知 key 仍 fail closed。"""

    for key in ("counselor_context", "persona_context"):
        assert ProjectionPolicy.assert_function_enabled(key) is None
    with pytest.raises(ProjectionPolicyDenied):
        ProjectionPolicy.assert_function_enabled("matchmaking")


def test_unknown_function_key_fail_closed() -> None:
    with pytest.raises(ProjectionPolicyDenied) as excinfo:
        ProjectionPolicy.assert_function_enabled("matchmaking")
    assert not isinstance(excinfo.value, ProjectionFeatureNotEnabled)


def test_active_function_keys_pass() -> None:
    for key in ("search", "compatibility", "recommend"):
        assert ProjectionPolicy.assert_function_enabled(key) is None


# ---------------------------------------------------------------------------
# subject ↔ data_category 互锁
# ---------------------------------------------------------------------------


def test_subject_mismatch_denied() -> None:
    with pytest.raises(ProjectionPolicyDenied):
        ProjectionPolicy.assert_subject_category("personal_profile", "ideal_partner")
    with pytest.raises(ProjectionPolicyDenied):
        ProjectionPolicy.assert_subject_category(
            "ideal_partner_preference", "personal"
        )
    with pytest.raises(ProjectionPolicyDenied):
        ProjectionPolicy.assert_subject_category("third_party_profile", "personal")


def test_subject_match_passes() -> None:
    ProjectionPolicy.assert_subject_category("personal_profile", "personal")
    ProjectionPolicy.assert_subject_category("public_profile_summary", "personal")
    ProjectionPolicy.assert_subject_category(
        "ideal_partner_preference", "ideal_partner"
    )
    # compatibility_features 是派生匹配特征集：两个主体都可作为输入。
    ProjectionPolicy.assert_subject_category("compatibility_features", "personal")
    ProjectionPolicy.assert_subject_category(
        "compatibility_features", "ideal_partner"
    )


def test_denied_message_carries_no_entry_value() -> None:
    with pytest.raises(ProjectionPolicyDenied) as excinfo:
        ProjectionPolicy.assert_subject_category(
            "ideal_partner_preference", "personal"
        )
    assert "我每天喝咖啡" not in str(excinfo.value)


# ---------------------------------------------------------------------------
# 只有 confirmed Claim 可入长期 Projection
# ---------------------------------------------------------------------------


def test_only_confirmed_claim_passes() -> None:
    ProjectionPolicy.assert_confirmed_claim("claim", "confirmed")


@pytest.mark.parametrize(
    "node_type,status",
    [
        ("claim", "proposed"),
        ("claim", "user_corrected"),
        ("claim", "superseded"),
        ("claim", "expired"),
        ("observation", "active"),
        ("observation", "proposed"),
        ("insight", "confirmed"),
        ("state", "active"),
        ("suppression", "active"),
    ],
)
def test_non_confirmed_or_non_claim_nodes_denied(node_type: str, status: str) -> None:
    with pytest.raises(ProjectionPolicyDenied):
        ProjectionPolicy.assert_confirmed_claim(node_type, status)


# ---------------------------------------------------------------------------
# allowlist 与 input hash
# ---------------------------------------------------------------------------


def test_allowlist_is_frozen_exact_ten() -> None:
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


def test_assert_entry_fields_rejects_extra_and_missing() -> None:
    ProjectionPolicy.assert_entry_fields(_entry())
    with pytest.raises(ProjectionPolicyDenied):
        ProjectionPolicy.assert_entry_fields(_entry(source_quote="原话"))
    with pytest.raises(ProjectionPolicyDenied):
        broken = _entry()
        del broken["stability"]
        ProjectionPolicy.assert_entry_fields(broken)


def test_input_hash_order_insensitive_and_content_sensitive() -> None:
    entries = [
        _content_entry(field_key="interest_tags"),
        _content_entry(field_key="age", value=30, value_type="number"),
    ]
    reordered = list(reversed(entries))
    h1 = ProjectionPolicy.projection_input_hash(entries)
    h2 = ProjectionPolicy.projection_input_hash(reordered)
    assert h1 == h2
    assert len(h1) == 64

    changed = [dict(entries[0]), dict(entries[1])]
    changed[1]["value"] = 31
    assert ProjectionPolicy.projection_input_hash(changed) != h1


def test_input_hash_excludes_version_and_evidence() -> None:
    base = [_entry()]
    with_version = [dict(base[0], projection_version=7, evidence_ref="memory:x")]
    assert ProjectionPolicy.projection_input_hash(base) == (
        ProjectionPolicy.projection_input_hash(with_version)
    )


# ---------------------------------------------------------------------------
# assert_readable：owner / 维度 / 状态 / 策略版本全量校验
# ---------------------------------------------------------------------------


def _projection_row(**overrides):
    fields = {
        "owner_user_id": 42,
        "function_key": "search",
        "purpose": "candidate_filter",
        "data_category": "personal_profile",
        "status": "active",
        "policy_revision": "ai-policy-2026-08-07-v1",
        "projection_version": 3,
        "entries": [_entry()],
    }
    fields.update(overrides)
    return fields


def test_assert_readable_passes_for_matching_active_row() -> None:
    ProjectionPolicy.assert_readable(
        _projection_row(),
        owner_user_id=42,
        function_key="search",
        purpose="candidate_filter",
        data_category="personal_profile",
        policy_revision="ai-policy-2026-08-07-v1",
    )


def test_assert_readable_rejects_mismatches() -> None:
    common = dict(
        owner_user_id=42,
        function_key="search",
        purpose="candidate_filter",
        data_category="personal_profile",
        policy_revision="ai-policy-2026-08-07-v1",
    )
    with pytest.raises(ProjectionPolicyDenied):
        ProjectionPolicy.assert_readable(
            _projection_row(), **{**common, "owner_user_id": 43}
        )
    with pytest.raises(ProjectionPolicyDenied):
        ProjectionPolicy.assert_readable(
            _projection_row(status="invalidated"), **common
        )
    with pytest.raises(ProjectionPolicyDenied):
        ProjectionPolicy.assert_readable(
            _projection_row(),
            **{**common, "policy_revision": "ai-policy-2027-01-01-v1"},
        )
    with pytest.raises(ProjectionPolicyDenied):
        ProjectionPolicy.assert_readable(
            _projection_row(function_key="recommend"), **common
        )
    with pytest.raises(ProjectionPolicyDenied):
        ProjectionPolicy.assert_readable(
            _projection_row(purpose="candidate_rank"), **common
        )
    with pytest.raises(ProjectionPolicyDenied):
        ProjectionPolicy.assert_readable(
            _projection_row(data_category="compatibility_features"), **common
        )


def test_assert_readable_rejects_entries_with_extra_fields() -> None:
    row = _projection_row(entries=[_entry(source_quote="原话")])
    with pytest.raises(ProjectionPolicyDenied):
        ProjectionPolicy.assert_readable(
            row,
            owner_user_id=42,
            function_key="search",
            purpose="candidate_filter",
            data_category="personal_profile",
            policy_revision="ai-policy-2026-08-07-v1",
        )


# ---------------------------------------------------------------------------
# Task 8：future consumer registry（只登记，不实现行为）
# ---------------------------------------------------------------------------


def test_future_consumer_registry_lists_both_keys() -> None:
    from app.services.ai.memory.projection_policy import FUTURE_CONSUMER_REGISTRY

    assert set(FUTURE_CONSUMER_REGISTRY) == {"counselor_context", "persona_context"}


def test_registry_persona_context_only_public_profile_summary() -> None:
    spec = ProjectionPolicy.future_consumer_spec("persona_context")
    assert spec["allowed_data_categories"] == ("public_profile_summary",)
    assert spec["allowed_subjects"] == ("personal",)
    assert "raw_event" in spec["forbidden_inputs"]
    assert "transcript" in spec["forbidden_inputs"]


def test_registry_counselor_context_allows_own_contexts() -> None:
    spec = ProjectionPolicy.future_consumer_spec("counselor_context")
    assert spec["allowed_data_categories"] == (
        "personal_profile",
        "ideal_partner_preference",
    )
    assert spec["min_claim_status"] == "confirmed"
    assert "source_quote" in spec["forbidden_inputs"]


def test_registry_rejects_enabled_and_unknown_keys() -> None:
    with pytest.raises(ProjectionPolicyDenied):
        ProjectionPolicy.future_consumer_spec("search")
    with pytest.raises(ProjectionPolicyDenied):
        ProjectionPolicy.future_consumer_spec("matchmaking")


@pytest.mark.asyncio
async def test_consumer_keys_enabled_end_to_end() -> None:
    """Phase 3 启用后：consumer key 的 grant/build/read 全链路可用且留痕。"""

    from tests.test_ai_memory_projections import (
        FakeProjectionSession,
        ProjectionStore,
        seed_claim,
    )
    from app.services.ai.memory.projections import (
        MemoryProjectionService,
        derive_consent_snapshot_id,
    )

    store = ProjectionStore()
    store.consents[42] = {
        "scope": "profile_text_extract",
        "version": "profile_text_extract-v3",
        "policy_revision": "ai-policy-2026-08-07-v1",
        "granted_at": "2026-09-06T08:00:00",
    }
    session = FakeProjectionSession(store)
    service = MemoryProjectionService(session, policy_revision="ai-policy-2026-08-07-v1")
    seed_claim(store, "c_ok")
    consent = store.consents[42]
    snapshot_id = derive_consent_snapshot_id(consent)

    await service.grant(
        owner_user_id=42,
        function_key="counselor_context",
        purpose="session_context",
        data_category="personal_profile",
        consent_snapshot_id=snapshot_id,
        policy_revision="ai-policy-2026-08-07-v1",
    )
    built = await service.build(
        owner_user_id=42,
        function_key="counselor_context",
        purpose="session_context",
        data_category="personal_profile",
    )
    assert built["status"] == "active"
    doc = await service.read_active(
        owner_user_id=42,
        function_key="counselor_context",
        purpose="session_context",
        data_category="personal_profile",
    )
    assert doc is not None and doc["projection_version"] >= 1

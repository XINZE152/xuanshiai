"""Memory Kernel Core v1 policy unit tests (Task 3).

MemoryPolicy is pure (no database): source priority, canonical key
derivation, stability derivation and the subject-write guard live here so
Ledger / Service / Shadow Write can all share one enforcement point.
"""

from __future__ import annotations

import pytest

from app.schemas.ai_memory import MEMORY_SOURCE_RANK, MemorySourceKind
from app.services.ai.memory.policy import MemoryPolicy, MemoryPolicyDenied


# ---------------------------------------------------------------------------
# 来源优先级（固定，不得静默 LWW）
# ---------------------------------------------------------------------------


def test_source_priority_matches_frozen_rank() -> None:
    assert MemoryPolicy.source_priority(MemorySourceKind.USER_EXPLICIT) == 5
    assert MemoryPolicy.source_priority(MemorySourceKind.BEHAVIOR) == 1
    order = [
        MemorySourceKind.USER_EXPLICIT,
        MemorySourceKind.USER_CONFIRMED,
        MemorySourceKind.USER_FEEDBACK,
        MemorySourceKind.INFERRED,
        MemorySourceKind.BEHAVIOR,
    ]
    priorities = [MemoryPolicy.source_priority(kind) for kind in order]
    assert priorities == sorted(priorities, reverse=True)
    assert MEMORY_SOURCE_RANK == {
        "user_explicit": 5,
        "user_confirmed": 4,
        "user_feedback": 3,
        "inferred": 2,
        "behavior": 1,
    }


def test_conflict_resolution_is_priority_based_not_lww() -> None:
    # 高优先级来源可以接管同 key 事实。
    assert (
        MemoryPolicy.resolve_conflict("user_feedback", "user_explicit") == "replace"
    )
    # 同优先级：先到先得（不静默覆盖）。
    assert MemoryPolicy.resolve_conflict("inferred", "inferred") == "keep_existing"
    # 低优先级来源不能覆盖已有事实。
    assert MemoryPolicy.resolve_conflict("user_confirmed", "inferred") == "keep_existing"


# ---------------------------------------------------------------------------
# canonical key
# ---------------------------------------------------------------------------


def test_canonical_key_is_deterministic_and_scoped() -> None:
    a = MemoryPolicy.canonical_key("personal", "lifestyle", "field_key:coffee")
    b = MemoryPolicy.canonical_key("personal", "lifestyle", "field_key:coffee")
    assert a == b
    assert a.startswith("personal:lifestyle:")
    assert MemoryPolicy.canonical_key("ideal_partner", "lifestyle", "field_key:coffee").startswith(
        "ideal_partner:lifestyle:"
    )
    # 不同主体 / 不同维度 / 不同身份得到不同 key。
    assert MemoryPolicy.canonical_key("personal", "lifestyle", "field_key:coffee") != (
        MemoryPolicy.canonical_key("ideal_partner", "lifestyle", "field_key:coffee")
    )
    assert MemoryPolicy.canonical_key("personal", "intimacy_pattern", "field_key:coffee") != (
        MemoryPolicy.canonical_key("personal", "lifestyle", "field_key:coffee")
    )
    assert MemoryPolicy.canonical_key("personal", "lifestyle", "category:pet:hash1") != (
        MemoryPolicy.canonical_key("personal", "lifestyle", "category:pet:hash2")
    )
    assert len(a) <= 160


def test_canonical_key_accepts_general_dimension() -> None:
    key = MemoryPolicy.canonical_key("personal", None, "field_key:age")
    assert key.startswith("personal:general:")


# ---------------------------------------------------------------------------
# stability 推导
# ---------------------------------------------------------------------------


def test_derive_stability_monotonic_by_source_priority() -> None:
    explicit = MemoryPolicy.derive_stability(MemorySourceKind.USER_EXPLICIT, 0.9)
    confirmed = MemoryPolicy.derive_stability(MemorySourceKind.USER_CONFIRMED, 0.9)
    feedback = MemoryPolicy.derive_stability(MemorySourceKind.USER_FEEDBACK, 0.9)
    inferred = MemoryPolicy.derive_stability(MemorySourceKind.INFERRED, 0.9)
    behavior = MemoryPolicy.derive_stability(MemorySourceKind.BEHAVIOR, 0.9)
    assert explicit > confirmed > feedback > inferred > behavior
    for value in (explicit, confirmed, feedback, inferred, behavior):
        assert 0.0 <= value <= 1.0


def test_derive_stability_bounded_and_confidence_sensitive() -> None:
    low = MemoryPolicy.derive_stability(MemorySourceKind.INFERRED, 0.1)
    high = MemoryPolicy.derive_stability(MemorySourceKind.INFERRED, 0.95)
    assert high > low
    assert MemoryPolicy.derive_stability(MemorySourceKind.INFERRED, 1.0) <= 1.0
    assert MemoryPolicy.derive_stability(MemorySourceKind.BEHAVIOR, 0.0) >= 0.0


# ---------------------------------------------------------------------------
# subject 写入守卫
# ---------------------------------------------------------------------------


def test_assert_subject_write_accepts_aligned_pairs() -> None:
    MemoryPolicy.assert_subject_write("personal", fact_kind="about_user")
    MemoryPolicy.assert_subject_write("ideal_partner", fact_kind="partner_preference")


def test_assert_subject_write_rejects_cross_subject_facts() -> None:
    with pytest.raises(MemoryPolicyDenied) as excinfo:
        MemoryPolicy.assert_subject_write("ideal_partner", fact_kind="about_user")
    assert "ideal_partner" in str(excinfo.value)
    with pytest.raises(MemoryPolicyDenied):
        MemoryPolicy.assert_subject_write("personal", fact_kind="partner_preference")


def test_assert_subject_write_rejects_unknown_subject() -> None:
    with pytest.raises(MemoryPolicyDenied):
        MemoryPolicy.assert_subject_write("third_party_profile", fact_kind="about_user")


def test_assert_subject_write_ignores_payloadless_nodes() -> None:
    # state / suppression / insight 不携带 fact_kind，不受主体互锁约束。
    MemoryPolicy.assert_subject_write("personal", fact_kind=None)
    MemoryPolicy.assert_subject_write("ideal_partner", fact_kind=None)


# ---------------------------------------------------------------------------
# Suppression 守卫
# ---------------------------------------------------------------------------


def test_active_suppression_blocks_rewrite() -> None:
    with pytest.raises(MemoryPolicyDenied) as excinfo:
        MemoryPolicy.assert_not_suppressed("active")
    assert "suppressed" in str(excinfo.value).lower() or "墓碑" in str(excinfo.value)


def test_lifted_or_missing_suppression_allows_rewrite() -> None:
    MemoryPolicy.assert_not_suppressed(None)
    MemoryPolicy.assert_not_suppressed("lifted")

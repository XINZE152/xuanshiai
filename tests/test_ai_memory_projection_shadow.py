"""Memory Projection shadow read & mode switch tests (Task 5).

- default mode is legacy (legacy read only, zero memory reads);
- invalid mode values fail fast;
- shadow dual-reads but always returns the legacy result, logging only
  hashes / counts / field keys / diff types (never values or quotes);
- memory mode returns the memory projection and falls back to legacy with a
  fallback log when the projection is missing;
- canonical diff compares only subject / field key / value type / status /
  source kind / claim id / version — never sensitive values.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.core.config import settings
from app.services.ai.features import (
    MEMORY_PROJECTION_READ_MODES,
    memory_dimension_for_kind,
    memory_projection_read_mode,
    read_projection_with_mode,
)
from app.services.ai.memory.projection_compare import (
    ProjectionDiff,
    canonical_diff,
    canonical_document,
)
from app.schemas.ai_common import ProjectionKind

from tests.test_ai_memory_projections import (
    OWNER_ID,
    POLICY_REVISION,
    FakeProjectionSession,
    ProjectionStore,
    seed_claim,
)

pytestmark = pytest.mark.asyncio


def set_mode(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    monkeypatch.setattr(settings, "ai_memory_projection_read_mode", mode)


async def seed_memory_projection(
    store: ProjectionStore,
    *,
    function_key: str = "search",
    purpose: str = "candidate_filter",
    data_category: str = "personal_profile",
) -> None:
    from app.services.ai.memory.projections import (
        MemoryProjectionService,
        derive_consent_snapshot_id,
    )

    store.consents[OWNER_ID] = {
        "scope": "profile_text_extract",
        "version": "profile_text_extract-v3",
        "policy_revision": POLICY_REVISION,
        "granted_at": "2026-09-05T08:00:00",
    }
    snapshot_id = derive_consent_snapshot_id(store.consents[OWNER_ID])
    store.grants[(OWNER_ID, function_key, purpose, data_category)] = {
        "grant_id": "g1",
        "owner_user_id": OWNER_ID,
        "function_key": function_key,
        "purpose": purpose,
        "data_category": data_category,
        "status": "active",
        "consent_snapshot_id": snapshot_id,
        "policy_revision": POLICY_REVISION,
        "granted_at": "2026-09-05T08:00:00",
        "revoked_at": None,
    }
    seed_claim(store, "c_ok")
    session = FakeProjectionSession(store)
    service = MemoryProjectionService(session, policy_revision=POLICY_REVISION)
    await service.build(
        owner_user_id=OWNER_ID,
        function_key=function_key,
        purpose=purpose,
        data_category=data_category,
    )


def legacy_loader(legacy: dict[str, Any] | None):
    async def _load(db) -> dict[str, Any] | None:
        return legacy

    return _load


LEGACY_PROJECTION = {
    "fields": {"height_cm": 175, "interest_tags": ["hiking"]},
    "source_hash": "legacy-hash",
}


# ---------------------------------------------------------------------------
# 三态开关
# ---------------------------------------------------------------------------


def test_read_modes_vocabulary() -> None:
    assert MEMORY_PROJECTION_READ_MODES == ("legacy", "shadow", "memory")
    assert settings.ai_memory_projection_read_mode in MEMORY_PROJECTION_READ_MODES


def test_invalid_mode_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ai_memory_projection_read_mode", "bogus")
    with pytest.raises(RuntimeError):
        memory_projection_read_mode()


def test_kind_mapping_covers_all_kinds() -> None:
    mapping = {
        kind: memory_dimension_for_kind(kind) for kind in ProjectionKind
    }
    assert set(mapping) == set(ProjectionKind)
    assert all(
        set(dimension) == {"function_key", "purpose", "data_category"}
        for dimension in mapping.values()
    )


async def test_legacy_mode_reads_only_legacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_mode(monkeypatch, "legacy")
    store = ProjectionStore()  # 无任何记忆投影数据
    session = FakeProjectionSession(store)
    calls = {"count": 0}

    async def loader(db):
        calls["count"] += 1
        return LEGACY_PROJECTION

    result = await read_projection_with_mode(
        session,
        user_id=OWNER_ID,
        projection_kind=ProjectionKind.PERSONAL_SEARCHABLE,
        legacy_loader=loader,
    )
    assert result == LEGACY_PROJECTION
    assert calls["count"] == 1
    assert session.calls == [], "legacy 模式不得读记忆投影表"


async def test_shadow_dual_reads_but_legacy_wins(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    set_mode(monkeypatch, "shadow")
    store = ProjectionStore()
    await seed_memory_projection(store)
    session = FakeProjectionSession(store)
    sensitive_value = "我每天早上都要喝一杯咖啡"

    with caplog.at_level("INFO", logger="app.services.ai.features"):
        result = await read_projection_with_mode(
            session,
            user_id=OWNER_ID,
            projection_kind=ProjectionKind.PERSONAL_SEARCHABLE,
            legacy_loader=legacy_loader(LEGACY_PROJECTION),
        )
    assert result == LEGACY_PROJECTION, "shadow 模式结果以旧链路为准"
    diff_lines = [
        record.getMessage()
        for record in caplog.records
        if "memory_projection_shadow_diff" in record.getMessage()
    ]
    assert diff_lines, "shadow 模式必须记录 canonical diff"
    message = diff_lines[0]
    assert "height_cm" in message and "interest_tags" in message
    assert sensitive_value not in message, "diff 日志不得携带字段原文"
    assert "175" not in message, "diff 日志不得携带字段值"


async def test_memory_mode_returns_memory_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_mode(monkeypatch, "memory")
    store = ProjectionStore()
    await seed_memory_projection(store)
    session = FakeProjectionSession(store)

    result = await read_projection_with_mode(
        session,
        user_id=OWNER_ID,
        projection_kind=ProjectionKind.PERSONAL_SEARCHABLE,
        legacy_loader=legacy_loader(LEGACY_PROJECTION),
    )
    assert result is not None
    assert "projection_id" in result, "memory 模式返回记忆投影文档"
    assert result["entries"][0]["claim_id"] == "c_ok"


async def test_memory_mode_falls_back_to_legacy_when_missing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    set_mode(monkeypatch, "memory")
    store = ProjectionStore()  # 无投影
    session = FakeProjectionSession(store)

    with caplog.at_level("INFO", logger="app.services.ai.features"):
        result = await read_projection_with_mode(
            session,
            user_id=OWNER_ID,
            projection_kind=ProjectionKind.PERSONAL_SEARCHABLE,
            legacy_loader=legacy_loader(LEGACY_PROJECTION),
        )
    assert result == LEGACY_PROJECTION, "缺投影时按固定策略回退 legacy"
    fallback_lines = [
        record.getMessage()
        for record in caplog.records
        if "memory_projection_fallback" in record.getMessage()
    ]
    assert fallback_lines, "回退必须计数日志"


# ---------------------------------------------------------------------------
# canonical diff
# ---------------------------------------------------------------------------


def test_canonical_diff_identical_documents() -> None:
    left = canonical_document(
        subject="personal",
        status="active",
        entries=[{"field_key": "height_cm", "value": 175, "value_type": "number"}],
    )
    right = canonical_document(
        subject="personal",
        status="active",
        entries=[{"field_key": "height_cm", "value": 999, "value_type": "number"}],
    )
    diff = canonical_diff(left, right)
    assert diff.is_identical, "value 不参与比较（禁止比较敏感原文）"
    assert diff.diff_types == ()


def test_canonical_diff_detects_all_frozen_dimensions() -> None:
    legacy = canonical_document(
        subject="personal",
        status="active",
        entries=[
            {"field_key": "age", "value_type": "number", "source_kind": "user_explicit", "claim_id": "clm_a"},
            {"field_key": "height_cm", "value_type": "number", "source_kind": "user_confirmed", "claim_id": "clm_b"},
        ],
    )
    memory = canonical_document(
        subject="ideal_partner",
        status="invalidated",
        entries=[
            {"field_key": "age", "value_type": "string", "source_kind": "inferred", "claim_id": "clm_z"},
            {"field_key": "city_code", "value_type": "string", "source_kind": "user_explicit", "claim_id": "clm_c"},
        ],
    )
    diff = canonical_diff(legacy, memory)
    assert not diff.subject_match
    assert not diff.status_match
    assert diff.field_keys_only_legacy == ("height_cm",)
    assert diff.field_keys_only_memory == ("city_code",)
    assert diff.claim_id_mismatches == ("age",)
    assert diff.value_type_mismatches == ("age",)
    assert diff.source_kind_mismatches == ("age",)
    assert set(diff.diff_types) == {
        "subject_mismatch",
        "status_mismatch",
        "field_key_only_legacy",
        "field_key_only_memory",
        "claim_id_mismatch",
        "value_type_mismatch",
        "source_kind_mismatch",
    }


def test_canonical_diff_handles_missing_side() -> None:
    diff = canonical_diff(None, canonical_document(subject="personal", status="active"))
    assert diff.legacy_entry_count == 0
    assert diff.memory_entry_count == 0
    assert diff.field_keys_only_memory == ()
    assert not diff.subject_match, "单侧缺失视为 subject 未知 → 不匹配"


def test_canonical_document_strips_values() -> None:
    doc = canonical_document(
        subject="personal",
        status="active",
        entries=[{"field_key": "interest_tags", "value": ["敏感原文"], "value_type": "string_list"}],
    )
    assert "value" not in json.dumps(doc, ensure_ascii=False) or "敏感原文" not in json.dumps(
        doc, ensure_ascii=False
    )
    assert all("value" not in entry or entry.get("value") is None for entry in doc["entries"])


def test_projection_diff_is_log_safe_shape() -> None:
    diff = ProjectionDiff()
    payload = diff.__dict__
    assert all(isinstance(key, str) for key in payload)
    banned = {"value", "values", "source_quote", "transcript", "entries"}
    assert not (banned & set(payload)), "diff 结构不得携带原文/条目负载字段"

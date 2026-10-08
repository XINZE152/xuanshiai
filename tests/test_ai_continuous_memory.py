"""continuous_v2 确认稿 → Memory 转发的无数据库行为测试。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from app.services.ai.continuous_memory import (
    ContinuousMemoryForwardError,
    forward_continuous_confirmation_to_memory,
)
from app.services.ai.candidates import bucket_for_dimension, compute_candidate_content_hash
from app.services.ai.memory.policy import MemoryPolicy
from app.services.ai.profile import ProfileDraft, ProfileDraftField

pytestmark = pytest.mark.asyncio


@dataclass
class _Record:
    event_id: str


class _FakeMemoryService:
    def __init__(self) -> None:
        self.claims: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._event_no = 0

    def _record(self) -> _Record:
        self._event_no += 1
        return _Record(f"event-{self._event_no}")

    async def read_claim_by_canonical(self, **kwargs: Any) -> dict[str, Any] | None:
        return self.claims.get(kwargs["canonical_key"])

    async def propose(self, **kwargs: Any) -> list[_Record]:
        self.calls.append(("propose", kwargs))
        row = {
            "claim_id": f"claim-{len(self.claims) + 1}",
            "status": "proposed",
            "last_event_seq": 2,
            "importance": 0.5,
            "constraint_type": None,
            "value_json": json.dumps(kwargs["value"], ensure_ascii=False),
        }
        self.claims[kwargs["canonical_key"]] = row
        return [self._record(), self._record()]

    async def confirm_claim(self, **kwargs: Any) -> _Record:
        self.calls.append(("confirm_claim", kwargs))
        claim = next(row for row in self.claims.values() if row["claim_id"] == kwargs["claim_id"])
        claim["status"] = "confirmed"
        claim["last_event_seq"] += 1
        if kwargs.get("reviewed_revision_ref") is not None:
            claim["value_json"] = json.dumps(kwargs["reviewed_value"], ensure_ascii=False)
        return self._record()

    async def correct_claim(self, **kwargs: Any) -> _Record:
        self.calls.append(("correct_claim", kwargs))
        claim = next(row for row in self.claims.values() if row["claim_id"] == kwargs["claim_id"])
        claim["status"] = "user_corrected"
        claim["value_json"] = json.dumps(kwargs["value"], ensure_ascii=False)
        claim["last_event_seq"] += 1
        return self._record()

    async def suppress_claim(self, **kwargs: Any) -> _Record:
        self.calls.append(("suppress_claim", kwargs))
        claim = next(row for row in self.claims.values() if row["claim_id"] == kwargs["claim_id"])
        claim["status"] = "suppressed"
        return self._record()


def _field(
    key: str,
    *,
    subject: str = "personal",
    value: Any = "hiking",
    status: str = "confirmed",
    kind: str = "structured",
    category: str | None = None,
    content: str | None = None,
    replaces_field_key: str | None = None,
    source_span: str | None = "用户确认的原始依据",
) -> ProfileDraftField:
    return ProfileDraftField(
        field_key=key,
        subject=subject,
        field_kind=kind,
        category=category,
        content=content,
        value=value,
        replaces_field_key=replaces_field_key,
        source_turn_ids=("turn-1",),
        source_span=source_span,
        confidence=0.82,
        consent_scope="profile_text_extract",
        confirmation_status=status,
    )

def _draft(*fields: ProfileDraftField, subject: str = "personal") -> ProfileDraft:
    return ProfileDraft(
        draft_id="draft-1",
        owner_user_id=42,
        subject=subject,
        schema_version="profile-continuous-v2",
        consent_snapshot={
            "scope": "profile_text_extract",
            "version": "v1",
            "policy_revision": "policy-v1",
            "granted_at": "2026-10-07T00:00:00",
        },
        fields=fields,
    )


CONSENT = {
    "scope": "profile_text_extract",
    "version": "v1",
    "policy_revision": "policy-v1",
    "granted_at": "2026-10-07T00:00:00",
}


async def test_confirmed_new_field_proposes_as_user_confirmed_then_confirms() -> None:
    service = _FakeMemoryService()
    field = _field("interest_tags", value=["hiking"])

    result = await forward_continuous_confirmation_to_memory(
        None,  # type: ignore[arg-type]
        _draft(field),
        (field,),
        revision_id=9,
        source_revision={"profile": 2, "preference": 0, "privacy": 3},
        consent_snapshot=CONSENT,
        idempotency_key="continuous-confirm:preview-1",
        memory_service=service,
    )

    assert result.proposed == 1
    assert result.confirmed == 1
    assert [name for name, _ in service.calls] == ["propose", "confirm_claim"]
    propose = service.calls[0][1]
    assert propose["source_kind"] == "user_confirmed"
    assert propose["fact_kind"] == "about_user"
    assert propose["source_ref"].startswith("continuous-v2:revision:9:personal:")
    assert len(propose["source_quote"]) <= 512


async def test_existing_change_uses_correction_and_deleted_uses_tombstone() -> None:
    changed = _field("interest_tags", value=["reading"])
    deleted = _field("city_code", value="4403", status="deleted")
    service = _FakeMemoryService()
    for field, value in ((changed, ["hiking"]), (deleted, "4403")):
        canonical = MemoryPolicy.canonical_key(
            field.subject,
            bucket_for_dimension(field.field_kind, field.field_key, field.category, field.content),
            MemoryPolicy.candidate_identity("structured", field.field_key, None, None),
        )
        service.claims[canonical] = {
            "claim_id": f"claim-{field.field_key}",
            "status": "confirmed",
            "last_event_seq": 7,
            "importance": 0.7,
            "constraint_type": "preference",
            "value_json": json.dumps(value),
        }

    result = await forward_continuous_confirmation_to_memory(
        None,  # type: ignore[arg-type]
        _draft(changed, deleted),
        (changed,),
        revision_no=3,
        source_revision={},
        consent_snapshot=CONSENT,
        idempotency_key="continuous-confirm:preview-2",
        memory_service=service,
    )

    assert result.corrected == 1
    assert result.suppressed == 1
    assert [name for name, _ in service.calls] == ["correct_claim", "confirm_claim", "suppress_claim"]
    assert service.claims[next(key for key in service.claims if service.claims[key]["claim_id"] == "claim-interest_tags")]["status"] == "confirmed"
    assert service.calls[0][1]["value"] == ["reading"]


@pytest.mark.parametrize("inherited", [True, False])
async def test_same_confirmed_value_skips_only_current_formal_baseline(inherited) -> None:
    field = _field("age", value=30)
    service = _FakeMemoryService()
    canonical = MemoryPolicy.canonical_key(
        "personal",
        bucket_for_dimension(field.field_kind, field.field_key, field.category, field.content),
        MemoryPolicy.candidate_identity("structured", "age", None, None),
    )
    service.claims[canonical] = {
        "claim_id": "claim-age",
        "status": "confirmed",
        "last_event_seq": 4,
        "importance": 0.5,
        "constraint_type": None,
        "value_json": "30",
    }

    result = await forward_continuous_confirmation_to_memory(
        None,  # type: ignore[arg-type]
        _draft(field),
        (field,),
        source_revision={},
        consent_snapshot=CONSENT,
        idempotency_key="continuous-confirm:preview-3",
        memory_service=service,
        previous_fields=(field,) if inherited else (),
    )

    if inherited:
        assert result.skipped == 1
        assert service.calls == []
    else:
        assert result.confirmed == 1
        assert [name for name, _ in service.calls] == ["correct_claim", "confirm_claim"]


async def test_unconfirmed_candidate_and_foreign_subject_are_rejected() -> None:
    candidate = _field("age", value=30, status="suggested")
    with pytest.raises(ContinuousMemoryForwardError):
        await forward_continuous_confirmation_to_memory(
            None,  # type: ignore[arg-type]
            _draft(candidate),
            (candidate,),
            source_revision={},
            consent_snapshot=CONSENT,
            idempotency_key="continuous-confirm:preview-4",
            memory_service=_FakeMemoryService(),
        )

    personal = _field("age", value=30)
    partner_draft = _draft(personal, subject="ideal_partner")
    with pytest.raises(ContinuousMemoryForwardError):
        await forward_continuous_confirmation_to_memory(
            None,  # type: ignore[arg-type]
            partner_draft,
            (personal,),
            source_revision={},
            consent_snapshot=CONSENT,
            idempotency_key="continuous-confirm:preview-5",
            memory_service=_FakeMemoryService(),
        )


async def test_ideal_partner_is_owner_preference_not_public_profile() -> None:
    field = _field("relationship_goal", subject="ideal_partner", value="愿意沟通")
    service = _FakeMemoryService()

    result = await forward_continuous_confirmation_to_memory(
        None,  # type: ignore[arg-type]
        _draft(field, subject="ideal_partner"),
        (field,),
        revision_id=11,
        source_revision={"preference": 2},
        consent_snapshot=CONSENT,
        idempotency_key="continuous-confirm:preview-6",
        memory_service=service,
    )
    assert result.subject == "ideal_partner"
    assert service.calls[0][1]["fact_kind"] == "partner_preference"
    assert all(call[1].get("data_category") != "public_profile_summary" for call in service.calls)
async def test_entry_replacement_suppresses_old_identity_before_confirming_new_entry() -> None:
    old = _field(
        "entry-old",
        kind="entry",
        category="personality",
        content="我习惯先倾听",
        value=None,
    )
    new = _field(
        "entry-new",
        kind="entry",
        category="personality",
        content="我习惯先倾听再沟通",
        value=None,
        replaces_field_key="entry-old",
    )
    service = _FakeMemoryService()
    old_content_hash = compute_candidate_content_hash(
        "personal", "entry", None, old.category, None, old.content
    )
    old_canonical = MemoryPolicy.canonical_key(
        "personal",
        bucket_for_dimension(old.field_kind, old.field_key, old.category, old.content),
        MemoryPolicy.candidate_identity("entry", None, old.category, old_content_hash),
    )
    service.claims[old_canonical] = {
        "claim_id": "claim-entry-old",
        "status": "confirmed",
        "last_event_seq": 3,
        "importance": 0.5,
        "constraint_type": None,
        "value_json": json.dumps(old.content, ensure_ascii=False),
    }

    result = await forward_continuous_confirmation_to_memory(
        None,  # type: ignore[arg-type]
        _draft(new),
        (new,),
        previous_fields=(old,),
        revision_id=12,
        source_revision={},
        consent_snapshot=CONSENT,
        idempotency_key="continuous-confirm:preview-entry-replace",
        memory_service=service,
    )

    assert result.suppressed == 1
    assert result.proposed == 1
    assert result.confirmed == 1
    assert [name for name, _ in service.calls] == [
        "suppress_claim",
        "propose",
        "confirm_claim",
    ]

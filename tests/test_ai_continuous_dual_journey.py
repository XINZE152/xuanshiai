from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.ai.base import ExtractedPatch, StructuredExtractRequest, StructuredExtractResult
from app.services.ai.candidates import (
    candidates_from_master_result,
    compute_candidate_content_hash,
)
from app.schemas.ai_profile import ProfileSubject
from app.services.ai.providers import MockAIProvider
from app.services.ai.prompts.profile_extract import build_profile_master_extract_prompt
from app.services.voice.master_orchestrator import MoxiangMasterOrchestrator


@pytest.mark.asyncio
async def test_mock_continuous_extract_is_one_call_with_two_subjects() -> None:
    provider = MockAIProvider()
    request = StructuredExtractRequest(
        subject="personal",
        subjects=("personal", "ideal_partner"),
        continuous_v2=True,
        session_kind="master",
        turn_texts=("我比较安静，希望对方愿意沟通。",),
        consent_version="consent-v1",
        policy_revision="policy-v1",
    )

    result = await provider.structured_extract(request)

    assert {item.subject.value for item in result.fields} == {"personal"}
    assert {item.subject.value for item in result.patches} == {"ideal_partner"}
    personal = candidates_from_master_result(
        subject="personal",
        result=result.model_copy(update={"patches": ()}),
        consent_version="consent-v1",
        policy_revision="policy-v1",
        source_turn_id="turn-1",
        owned_source_turn_ids=("turn-1",),
    )
    partner = candidates_from_master_result(
        subject="ideal_partner",
        result=result.model_copy(update={"fields": ()}),
        consent_version="consent-v1",
        policy_revision="policy-v1",
        source_turn_id="turn-1",
        owned_source_turn_ids=("turn-1",),
    )
    assert personal and all(item.subject == "personal" for item in personal)
    assert partner and all(item.subject == "ideal_partner" for item in partner)
    assert personal[0].source_turn_ids == ("turn-1",)
    assert partner[0].source_turn_ids == ("turn-1",)


def test_dual_prompt_requires_subject_and_conservative_ambiguity() -> None:
    prompt = build_profile_master_extract_prompt(
        "personal",
        ("我比较安静，希望对方愿意沟通。",),
        subjects=("personal", "ideal_partner"),
    )
    assert "只能输出一组 JSON" in prompt
    assert "每个 field/patch 还必须包含 subject" in prompt
    assert "具体第三人观察" in prompt
    assert "unknown_or_ambiguous" in prompt


def test_candidate_mapper_rejects_unowned_provider_source_id() -> None:
    from app.services.ai.base import ExtractedPatch, StructuredExtractResult
    from app.schemas.ai_profile import ProfileSubject

    result = StructuredExtractResult(
        patches=(
            ExtractedPatch(
                action="add",
                category="values",
                content="希望对方愿意沟通",
                subject=ProfileSubject.IDEAL_PARTNER,
                source_quote="我希望对方愿意沟通",
                source_turn_ids=("other-turn",),
            ),
        ),
    )
    with pytest.raises(ValueError, match="source id"):
        candidates_from_master_result(
            subject="ideal_partner",
            result=result,
            consent_version="consent-v1",
            policy_revision="policy-v1",
            source_turn_id="turn-1",
            owned_source_turn_ids=("turn-1",),
        )


def test_candidate_mapper_keeps_negative_or_uncertain_partner_uncommitted() -> None:
    from app.services.ai.base import ExtractedPatch, StructuredExtractResult
    from app.schemas.ai_profile import ProfileSubject

    result = StructuredExtractResult(
        patches=(
            ExtractedPatch(
                action="add",
                category="values",
                content="希望对方不要冷处理",
                subject=ProfileSubject.IDEAL_PARTNER,
                source_quote="我不确定是否希望对方不要冷处理",
            ),
        ),
    )
    assert candidates_from_master_result(
        subject="ideal_partner",
        result=result,
        consent_version="consent-v1",
        policy_revision="policy-v1",
        source_turn_id="turn-1",
        owned_source_turn_ids=("turn-1",),
    ) == ()


@pytest.mark.asyncio
async def test_master_orchestrator_only_injects_continuous_context_for_v2() -> None:
    ai_gateway = MagicMock()
    voice_gateway = MagicMock()
    orchestrator = MoxiangMasterOrchestrator(ai_gateway=ai_gateway, voice_gateway=voice_gateway)
    orchestrator.set_continuous_context("两主体正式稿与待核对候选")
    captured: list[dict[str, str]] = []

    async def stream_chat(context, messages, *, json_mode=False):
        captured.extend(messages)
        yield ("content", "收到")
        yield ("finish", "stop")

    ai_gateway.stream_chat = stream_chat
    with pytest.MonkeyPatch.context() as monkeypatch:
        settings = MagicMock(ai_provider="mock", ai_model_name="test", ai_retention_policy_version="policy-v1")
        monkeypatch.setattr("app.services.voice.master_orchestrator.settings", settings)
        async for _ in orchestrator.stream_reply("继续", flow_version="continuous_v2"):
            pass

    assert any("两主体正式稿与待核对候选" in item["content"] for item in captured)


def _patch_result(
    *,
    action: str,
    category: str,
    content: str,
    replaces_field_key: str | None = None,
    subject: ProfileSubject = ProfileSubject.PERSONAL,
) -> StructuredExtractResult:
    return StructuredExtractResult(
        patches=(
            ExtractedPatch(
                action=action,
                category=category,
                content=content,
                replaces_field_key=replaces_field_key,
                subject=subject,
                source_quote=content,
            ),
        ),
    )


def test_modify_patch_keeps_replacement_target_for_continuous_v2() -> None:
    """B3：continuous_v2 下 modify 必须把替换目标带进候选，成稿才能执行替换。"""
    result = _patch_result(
        action="modify",
        category="values",
        content="我更看重能一起复盘问题的相处方式",
        replaces_field_key="entry_values_seed01",
    )

    candidates = candidates_from_master_result(
        subject="personal",
        result=result,
        consent_version="consent-v1",
        policy_revision="policy-v1",
        source_turn_id="turn-1",
        owned_source_turn_ids=("turn-1",),
        replaceable_entry_keys={"entry_values_seed01"},
    )

    assert len(candidates) == 1
    assert candidates[0].field_kind == "entry"
    assert candidates[0].field_key == "entry_values_seed01"
    assert candidates[0].category == "values"


def test_modify_patch_with_unknown_target_is_dropped_without_failing_batch() -> None:
    """B3：目标不在正式稿时只丢弃该条，同轮其他候选照常保留（不整轮终态失败）。"""
    from app.services.ai.base import ExtractedEntry

    result = StructuredExtractResult(
        entries=(
            ExtractedEntry(
                category="interests",
                content="周末常去美术馆",
                subject=ProfileSubject.PERSONAL,
                source_quote="我周末常去美术馆",
            ),
        ),
        patches=(
            ExtractedPatch(
                action="modify",
                category="values",
                content="改成更看重沟通",
                replaces_field_key="entry_values_hallucinated",
                subject=ProfileSubject.PERSONAL,
                source_quote="之前那条改成更看重沟通",
            ),
        ),
    )

    candidates = candidates_from_master_result(
        subject="personal",
        result=result,
        consent_version="consent-v1",
        policy_revision="policy-v1",
        source_turn_id="turn-1",
        owned_source_turn_ids=("turn-1",),
        replaceable_entry_keys={"entry_values_seed01"},
    )

    assert len(candidates) == 1
    assert candidates[0].category == "interests"
    assert candidates[0].field_key is None


def test_legacy_mapper_keeps_entry_field_key_empty() -> None:
    """legacy / update 路径不传目标集合：entry 候选 field_key 与改动前一致为空。"""
    result = _patch_result(
        action="modify",
        category="values",
        content="改成更看重沟通",
        replaces_field_key="entry_values_seed01",
    )

    candidates = candidates_from_master_result(
        subject="personal",
        result=result,
        consent_version="consent-v1",
        policy_revision="policy-v1",
        source_turn_id="turn-1",
        owned_source_turn_ids=("turn-1",),
    )

    assert len(candidates) == 1
    assert candidates[0].field_key is None


def test_entry_content_hash_ignores_replacement_target() -> None:
    """替换目标只落在 field_key 列：hash 仍按 field_key=None 计算。

    这条不变量保证 Memory identity、``_assertion_mode_by_content_hash`` 与
    ``projections._claim_to_entry`` 的反解在 B3 之后继续一致。
    """
    content = "我更看重能一起复盘问题的相处方式"
    result = _patch_result(
        action="modify",
        category="values",
        content=content,
        replaces_field_key="entry_values_seed01",
    )

    with_target = candidates_from_master_result(
        subject="personal",
        result=result,
        consent_version="consent-v1",
        policy_revision="policy-v1",
        source_turn_id="turn-1",
        owned_source_turn_ids=("turn-1",),
        replaceable_entry_keys={"entry_values_seed01"},
    )
    without_target = candidates_from_master_result(
        subject="personal",
        result=result,
        consent_version="consent-v1",
        policy_revision="policy-v1",
        source_turn_id="turn-1",
        owned_source_turn_ids=("turn-1",),
    )

    assert with_target[0].content_hash == without_target[0].content_hash
    assert with_target[0].content_hash == compute_candidate_content_hash(
        "personal", "entry", None, "values", None, content
    )


def test_add_patch_never_carries_replacement_target() -> None:
    """add 分支不受替换语义影响：即使传入了目标集合也仍是新增。"""
    result = _patch_result(
        action="add",
        category="interests",
        content="最近开始学陶艺",
    )

    candidates = candidates_from_master_result(
        subject="personal",
        result=result,
        consent_version="consent-v1",
        policy_revision="policy-v1",
        source_turn_id="turn-1",
        owned_source_turn_ids=("turn-1",),
        replaceable_entry_keys={"entry_interests_seed01"},
    )

    assert len(candidates) == 1
    assert candidates[0].field_key is None


def test_replaced_entry_pairs_recompute_hash_from_published_rows() -> None:
    """失效旧候选所需的 hash 由发布行内容重算，可直接与候选 content_hash 比较。"""
    from app.services.ai.journey import _replaced_entry_pairs

    result = _patch_result(
        action="modify",
        category="values",
        content="改成更看重沟通",
        replaces_field_key="entry_values_seed01",
    )
    candidates = candidates_from_master_result(
        subject="personal",
        result=result,
        consent_version="consent-v1",
        policy_revision="policy-v1",
        source_turn_id="turn-1",
        owned_source_turn_ids=("turn-1",),
        replaceable_entry_keys={"entry_values_seed01"},
    )
    published = {
        "entry_values_seed01": {
            "field_key": "entry_values_seed01",
            "category": "values",
            "content": "欣赏踏实上进的人",
        }
    }

    pairs = _replaced_entry_pairs(candidates, published)

    assert pairs == (
        (
            "entry_values_seed01",
            compute_candidate_content_hash(
                "personal", "entry", None, "values", None, "欣赏踏实上进的人"
            ),
        ),
    )

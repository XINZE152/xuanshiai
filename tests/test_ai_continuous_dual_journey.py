from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.ai.base import StructuredExtractRequest
from app.services.ai.candidates import candidates_from_master_result
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

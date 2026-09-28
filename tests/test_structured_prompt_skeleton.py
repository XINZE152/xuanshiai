"""结构化任务 prompt 必须带统一骨架，且不混入对话角色名。"""

from __future__ import annotations

from app.services.ai.base import CompatibilityCompareRequest
from app.services.ai.prompts.compatibility_compare import build_compatibility_compare_prompt
from app.services.ai.prompts.profile_card_summarize import (
    PROFILE_CARD_PROMPT_VERSION,
    build_profile_card_summarize_prompt,
)
from app.services.ai.prompts.profile_extract import (
    build_profile_extract_prompt,
    build_profile_master_extract_prompt,
    build_profile_update_clarify_prompt,
)
from app.services.ai.prompts.profile_narrative import build_profile_narrative_prompt
from app.services.ai.prompts.search_parse import build_search_parse_prompt
from app.services.ai.prompts.structured_skeleton import STRUCTURED_PROMPT_SKELETON


def _prompts() -> tuple[str, ...]:
    return (
        build_profile_extract_prompt("personal", ("我住杭州。",)),
        build_profile_master_extract_prompt("personal", ("我住杭州。",)),
        build_profile_update_clarify_prompt("personal", ("最近开始健身。",)),
        build_profile_narrative_prompt("personal", (), (), ()),
        build_profile_card_summarize_prompt(personal_fields=(), narrative=None),
        build_search_parse_prompt("杭州，本科以上"),
        build_compatibility_compare_prompt(
            CompatibilityCompareRequest(viewer_personal="年龄 28", target_personal="年龄 30")
        ),
    )


def test_structured_builders_start_with_skeleton_and_stay_data_only() -> None:
    for prompt in _prompts():
        assert prompt.startswith(STRUCTURED_PROMPT_SKELETON + "\n\n")
        assert "json" in prompt.casefold()
        assert "知遇" not in prompt


def test_profile_card_prompt_version_bumped_for_skeleton() -> None:
    assert PROFILE_CARD_PROMPT_VERSION == "profile-card-summarize-v2"

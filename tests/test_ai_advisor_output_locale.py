"""AI 军师输出本地化：兜底知识、prompt、默认值必须中文（B-M-07）。"""

from __future__ import annotations

import re

from app.schemas.ai_advisor import AdvisorAdviceRequest
from app.services import ai_advisor as advisor


_CJK = re.compile(r"[\u4e00-\u9fa5]")


def test_fallback_knowledge_is_all_chinese() -> None:
    assert advisor._FALLBACK_KNOWLEDGE, "兜底知识表为空"
    for scenario, templates in advisor._FALLBACK_KNOWLEDGE.items():
        for template in templates:
            assert _CJK.search(template), f"场景 {scenario} 的模板未中文化：{template!r}"


def test_prompt_forces_chinese() -> None:
    request = AdvisorAdviceRequest(scenario="reply", incoming_message="在吗", tone="natural")
    prompt = advisor._build_prompt(request, "", [], "none")
    assert "Respond in Simplified Chinese only" in prompt


def test_normalize_result_defaults_are_chinese() -> None:
    request = AdvisorAdviceRequest(scenario="reply", incoming_message="在吗", tone="natural")
    result = advisor._normalize_result({}, request)
    assert _CJK.search(result["analysis"])
    assert _CJK.search(result["next_step"])
    # 无模型建议时回落中文兜底知识
    assert result["suggestions"]
    for item in result["suggestions"]:
        assert _CJK.search(item["content"])
        assert _CJK.search(item["reason"])


def test_normalize_result_keeps_english_model_output_but_reasons_are_chinese() -> None:
    """模型返回英文内容时不强行翻译（内容以 prompt 约束为主），但缺省 reason 必须中文。"""
    request = AdvisorAdviceRequest(scenario="reply", incoming_message="在吗", tone="natural")
    data = {
        "analysis": "English analysis from model",
        "suggestions": [{"content": "Hi there!", "style": "natural"}],
        "risk_level": "none",
        "next_step": None,
    }
    result = advisor._normalize_result(data, request)
    assert result["suggestions"][0]["content"] == "Hi there!"
    assert _CJK.search(result["suggestions"][0]["reason"])

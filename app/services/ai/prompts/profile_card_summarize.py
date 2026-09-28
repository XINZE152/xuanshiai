"""把已确认画像成稿改写成资料卡第一人称开放文本草稿。

与 ``profile_extract.py`` / ``profile_narrative.py`` 不同：这里不抽取事实字段、
不生成叙事展示层，只产出资料卡可拒绝草稿（自我介绍、关于我问答、兴趣候选）。

安全边界：
- 输入只含已确认字段 display 值 + 已确认叙事四维/insight/conclusion + 可选本人
  ideal_partner 叙事摘要；不含 turn 原文、用户 id、手机号。
- 不得编造职业/收入/身高/学历/住址；不确定则空字符串 + confidence 0。
- 人称必须是资料卡第一人称；禁止直接拷贝第二人称成稿。
- 输出仅限开放文本字段，不含事实栏。
"""

from __future__ import annotations

from typing import Any

from app.core.profile_tags import ALL_TAG_OPTIONS
from app.services.ai.prompts.structured_skeleton import wrap_structured_prompt

PROFILE_CARD_SCHEMA_VERSION = "profile-card-summarize-v1"
PROFILE_CARD_PROMPT_VERSION = "profile-card-summarize-v2"

_CONTACT_HINTS = ("微信", "微信号", "vx", "v信", "手机号", "电话", "加我")

_SYSTEM_HEADER = (
    "你是「宣誓爱」的资料卡文案助手。读者是用户本人，正在编辑自己的公开资料。"
    "任务是把用户已经确认过的画像成稿，改写成资料卡第一人称开放文本草稿。"
    "写作铁律：\n"
    "1. 全程使用第一人称「我」，禁止直接拷贝成稿里的第二人称「你」。\n"
    "2. 不编造职业、收入、身高、学历、住址、公司名或联系方式；输入没写到的事实必须空字符串，confidence 为 0。\n"
    "3. 不输出「优质」「高分」「命中注定」「最后机会」等评价或焦虑表述，不承诺关系结果。\n"
    "4. 关于理想伴侣只写「我希望遇到的人」，不得描述真实第三人。\n"
    "5. 兴趣标签只能从给定目录中挑选，禁止自造词。\n"
    "6. 不确定就空着。输出必须是 JSON，不要输出 JSON 之外的内容。"
)

_JSON_FORMAT_INSTRUCTION = (
    "请以 JSON 格式输出，结构如下：\n"
    "{\n"
    '  "self_intro": {"value": "第一人称自我介绍，最多500字，不确定则空字符串", "confidence": 0.0, "source_ref": "insight"},\n'
    '  "qa_1_partner": {"value": "理想中的另一半，第一人称，最多300字", "confidence": 0.0, "source_ref": "ideal_partner"},\n'
    '  "qa_3_love": {"value": "期待的爱情是什么样子，第一人称，最多300字", "confidence": 0.0, "source_ref": "relationship"},\n'
    '  "qa_2_sports_candidates": {"candidates": ["目录内标签"], "confidence": 0.0, "source_ref": "lifestyle"},\n'
    '  "interest_tag_candidates": {"candidates": ["目录内标签"], "confidence": 0.0, "source_ref": "interest_tags"}\n'
    "}\n"
    "每个 confidence 为 0 到 1 的小数。source_ref 只能是输入里出现过的字段或维度 key。"
)


def _fields_to_block(fields: tuple[dict[str, Any], ...]) -> str:
    if not fields:
        return "（无）"
    lines: list[str] = []
    for item in fields:
        key = str(item.get("field_key") or "").strip()
        value = str(item.get("display_value") or "").strip()
        if not key:
            continue
        lines.append(f"- {key}：{value or '（空）'}")
    return "\n".join(lines) if lines else "（无）"


def _narrative_to_block(narrative: dict[str, Any] | None) -> str:
    data = narrative or {}
    dimensions = data.get("dimensions") or []
    dim_lines: list[str] = []
    if isinstance(dimensions, list):
        for item in dimensions:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("key") or "").strip()
            summary = str(item.get("summary") or "").strip()
            if title or summary:
                dim_lines.append(f"- {title}：{summary}")
    insight = str(data.get("insight") or "").strip()
    conclusion = str(data.get("conclusion") or "").strip()
    parts = [
        f"洞察：{insight or '（空）'}",
        f"收束：{conclusion or '（空）'}",
        "四维：",
        "\n".join(dim_lines) if dim_lines else "（无）",
    ]
    return "\n".join(parts)


def build_profile_card_summarize_prompt(
    *,
    personal_fields: tuple[dict[str, Any], ...],
    narrative: dict[str, Any] | None,
    ideal_partner_summary: str | None = None,
) -> str:
    """构造资料卡草稿 prompt；输入不含用户标识或 turn 原文。"""
    catalog = "、".join(sorted(ALL_TAG_OPTIONS))
    partner = str(ideal_partner_summary or "").strip() or "（无）"
    return wrap_structured_prompt(
        f"{_SYSTEM_HEADER}\n\n"
        f"已确认的本人画像字段：\n{_fields_to_block(personal_fields)}\n\n"
        f"已确认的叙事成稿：\n{_narrative_to_block(narrative)}\n\n"
        f"本人「愿遇之相」叙事摘要（只可写成我希望遇到的人，不是真实第三人）：\n{partner}\n\n"
        f"兴趣标签目录（只能从此处挑选）：\n{catalog}\n\n"
        f"{_JSON_FORMAT_INSTRUCTION}"
    )


def looks_like_contact(text: str) -> bool:
    lowered = str(text or "")
    return any(token in lowered for token in _CONTACT_HINTS)

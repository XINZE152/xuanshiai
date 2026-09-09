"""实时语音对话回复生成的中文 prompt 构建器（voice reply）。

角色是"像朋友一样聊天的资料收集助手"：用户通过语音说了一句信息，
模型生成一句自然口语的回复——确认听到的信息，并自然地追问下一个
想了解的点。回复会经 TTS 播放，因此必须口语化、极简短。

安全边界：
- 输入含用户原始转写文本（仅本轮），输出为对话回复。
- 不编造用户未提供的信息；不输出清单/引号/markdown。
"""

from __future__ import annotations

import json
from typing import Any

from app.services.ai.prompts.moxiang_ip import build_moxiang_dialogue_messages

# 对话常用字段的中文语义（与画像抽取 field_key 对齐）。
_FIELD_LABELS = {
    "age": "年龄",
    "city_code": "所在城市",
    "marriage_status": "婚姻状况",
    "education_level": "学历",
    "height_cm": "身高",
    "income_band": "收入范围",
    "occupation_group": "职业",
    "interest_tags": "兴趣爱好",
    "lifestyle_tags": "生活方式",
    "relationship_goal": "择偶期望",
}

# 单轮回复长度上限（字符）：TTS 播放节奏约束，超长会导致用户等待感明显。
_REPLY_MAX_CHARS = 30
MOXIANG_VOICE_REPLY_PROMPT_VERSION = "moxiang-voice-reply-v2"

_VOICE_REPLY_TASK_RULES = (
    "当前任务是生成一条适合语音播放的极短画像对话回复。"
    "先用自然口语接住用户本轮真正表达的新信息或纠正，再视语境追问一个最值得了解的点；"
    "用户拒绝或只是在纠正时不要强行追问。不要机械重复「记下了」「好的」「明白了」。"
    f"回复不超过 {_REPLY_MAX_CHARS} 个汉字，最多一个问题，无引号、列表、表情或 Markdown。"
    '最终只输出 JSON：{"reply_text":"给用户的回复"}，不得输出分析或额外字段。'
)


def _field_label(field_key: str) -> str:
    return _FIELD_LABELS.get(field_key, field_key)


def _known_fields_summary(known_fields: tuple[dict[str, Any], ...]) -> str:
    """把已抽取字段摘要渲染成 prompt 片段；空时给占位文案。"""
    if not known_fields:
        return "无"
    parts = []
    for item in known_fields[:10]:
        key = str(item.get("field_key", ""))
        value = str(item.get("value", ""))
        if key:
            parts.append(f"{_field_label(key)}={value}")
    return "、".join(parts) if parts else "无"


def build_voice_reply_prompt(
    transcript: str,
    field_key: str,
    known_fields: tuple[dict[str, Any], ...],
) -> str:
    """构建一轮语音对话的低优先级用户数据。

    角色与输出规则由 :func:`build_voice_reply_messages` 放进 system 消息；
    本函数保留为流式调用和既有调用方共用的数据序列化入口。
    """
    current_focus = (
        f"{_field_label(field_key)}" if field_key else "自由聊"
    )
    return "VOICE_REPLY_INPUT=" + json.dumps(
        {
            "transcript": transcript,
            "known_fields": _known_fields_summary(known_fields),
            "current_focus": current_focus,
        },
        ensure_ascii=False,
    )


def build_voice_reply_messages(
    transcript: str,
    field_key: str,
    known_fields: tuple[dict[str, Any], ...],
) -> list[dict[str, str]]:
    """构造 system IP + user data 的语音回复 Provider 消息。"""

    return build_moxiang_dialogue_messages(
        user_message=build_voice_reply_prompt(transcript, field_key, known_fields),
        subject="personal",
        task_rules=_VOICE_REPLY_TASK_RULES,
    )

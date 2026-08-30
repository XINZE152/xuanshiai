"""OpenAI-compatible text generation with a deterministic test fallback."""

from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi import HTTPException

from app.core.config import settings


async def complete(messages: list[dict[str, str]], *, json_mode: bool = False) -> str:
    if not settings.ai_enabled:
        if settings.is_test_mode:
            return _mock_response(messages, json_mode=json_mode)
        raise HTTPException(503, detail="AI服务未启用")
    if not settings.ai_api_key:
        raise HTTPException(503, detail="AI服务未配置 API Key")
    url = settings.ai_base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {settings.ai_api_key.get_secret_value()}", "Content-Type": "application/json"}
    payload: dict[str, Any] = {"model": settings.ai_model, "messages": messages, "temperature": 0.4}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    try:
        async with httpx.AsyncClient(timeout=settings.ai_timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("AI返回内容为空")
        return content.strip()
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise HTTPException(503, detail="AI服务暂时不可用") from exc


def parse_json(content: str) -> dict[str, Any]:
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise HTTPException(503, detail="AI返回格式无效") from exc
    if not isinstance(value, dict):
        raise HTTPException(503, detail="AI返回格式无效")
    return value


def _prompt_value(prompt: str, label: str) -> str:
    prefix = f"{label}:"
    for line in prompt.splitlines():
        if line.strip().startswith(prefix):
            return line.split(":", 1)[1].strip()
    return ""


def _advisor_mock_advice(scenario: str, tone: str) -> dict[str, Any]:
    templates = {
        "opening": ("这是刚开始认识的阶段，适合用轻松自然的方式打招呼，不要一次提出太多问题。", "你好呀，看到你的资料觉得挺有意思，想和你简单认识一下。", "先表达友好和兴趣，再给对方轻松回应的空间。", "如果对方愿意回应，再围绕对方提到的兴趣自然展开。"),
        "reply": ("建议先回应对方消息中的具体内容，再补充一个轻量问题，避免连续追问。", "听起来这个经历挺有意思的，你当时最有印象的是哪一部分？", "先接住对方的话题，再用一个开放问题推动交流。", "观察对方是否愿意展开，如果回复仍然简短就适当放慢节奏。"),
        "topic_extension": ("当前适合围绕对方已经提到的内容延伸，不要突然跳到过于私人的问题。", "除了这个爱好，你平时还喜欢做哪些让自己放松的事情？", "沿着已有话题继续了解对方，问题范围轻松且容易回答。", "根据对方的回答选择一个具体细节继续聊，并适当分享自己的经历。"),
        "rescue": ("如果当前话题有些冷，可以换成轻松的日常话题，降低继续聊天的压力。", "那我们换个轻松的话题，你最近有没有吃到什么好吃的？", "主动切换话题，但不追问对方为什么回复简短。", "如果对方仍然没有展开，可以先暂停发送，给对方留出空间。"),
        "care": ("关心对方时尽量具体、克制，不要让对方感觉被要求必须回应。", "看你最近好像挺忙的，忙完记得好好休息，别一直让自己绷着。", "表达温和关心，同时尊重对方的时间和回复节奏。", "发送后不要连续补充多条消息，等对方方便时自然回应。"),
        "compliment": ("夸赞具体的性格、行为或感受，比泛泛夸外貌更自然，也更容易让对方舒服。", "你对待事情挺认真的，和你聊天能感觉到你很有自己的想法。", "夸赞具体特质，避免夸大或给对方造成负担。", "夸赞后可以回到轻松话题，不要要求对方马上回应或表态。"),
        "values": ("价值观沟通适合用开放问题慢慢了解，不要把一次回答当成确定的匹配结论。", "如果两个人产生分歧，你更倾向于先冷静一下，还是及时坐下来沟通？", "用具体生活场景了解沟通方式，避免直接评判对错。", "先认真听完对方的想法，也分享自己的看法，保持平等交流。"),
        "intimacy": ("关系推进要建立在双方互动舒适且有回应的基础上，表达欣赏即可，不要制造压力。", "和你聊天很放松，不知不觉就聊了这么久，感觉挺难得的。", "表达真实感受和欣赏，不要求对方立即承诺关系。", "根据对方反馈把握节奏，保持自然交流，不要连续强化暧昧表达。"),
        "closing": ("收尾时清楚表达要暂时结束聊天，同时留下自然继续交流的空间。", "今天聊得很开心，你先忙自己的事情，等有空我们再接着聊。", "礼貌结束当前对话，不让对方产生必须立即回复的压力。", "结束后不要反复追问，下一次可以从今天聊过的内容自然开始。"),
        "analyze": ("可以观察回复的具体程度、主动性和持续性，但不能仅凭一两条消息下确定结论。", "你可以先回应对方提到的具体内容，再看对方是否愿意继续展开。", "以实际沟通表现作为参考，不把对方的态度简单归因。", "如果对方持续回复简短，就降低频率并尊重对方的沟通边界。"),
    }
    analysis, content, reason, next_step = templates.get(scenario, templates["reply"])
    tone_variants = {
        "warm": {
            "opening": "你好呀，很高兴认识你。希望我们可以从轻松聊天开始，慢慢了解彼此。",
            "reply": "听起来这个经历挺有意思的，如果你愿意，可以再和我多说一点。",
            "topic_extension": "我有点好奇，除了这个爱好，你平时还喜欢做哪些让自己放松的事情？",
            "rescue": "别着急，我们换个轻松的话题聊聊，你最近有没有遇到什么开心的小事？",
            "care": "看你最近好像挺忙的，忙完记得好好休息，也别忘了照顾好自己。",
            "compliment": "我很欣赏你对待事情的认真和真诚，和你聊天感觉很舒服。",
            "values": "我想认真听听你的想法，如果两个人产生分歧，你更倾向于怎么沟通？",
            "intimacy": "和你聊天很放松，能慢慢了解你，我觉得是一件挺开心的事。",
            "closing": "今天和你聊天很开心，你先忙自己的事情，等有空我们再温柔地接着聊。",
            "analyze": "从沟通来看，可以先接住对方的情绪，再温和地观察对方是否愿意继续展开。",
        },
        "humorous": {
            "opening": "哈喽，终于碰到啦，先轻松认识一下，看看我们能不能聊到一起～",
            "reply": "这个话题成功勾起我的好奇心了，方便展开讲讲吗？我已经准备好认真听啦～",
            "topic_extension": "除了这个爱好，你还有没有什么隐藏技能，方便让我开开眼？",
            "rescue": "我们给聊天换个频道吧，先聊点轻松的，你是奶茶派还是咖啡派？",
            "care": "忙归忙，也要记得给自己充充电，不然身体要申请休假啦～",
            "compliment": "你这个认真劲儿挺加分的，和你聊天不小心就把时间聊没了。",
            "values": "来一道轻松版人生选择题：有分歧时，你是先冷静，还是当场把话说开？",
            "intimacy": "和你聊天有点像打开了隐藏副本，不知不觉就聊了这么久～",
            "closing": "今天的聊天先暂时存档，等你有空我们再续上，别让话题断更啦～",
            "analyze": "先别急着给聊天下结论，看看对方是不是只是暂时进入了省字模式～",
        },
        "mature": {
            "opening": "你好，很高兴认识你。我们可以从轻松交流开始，按彼此舒服的节奏慢慢了解。",
            "reply": "我理解你的意思了。你愿意的话，可以再说说这件事对你的感受。",
            "topic_extension": "如果你愿意，可以继续谈谈这个爱好对你的意义，以及它如何融入你的生活。",
            "rescue": "如果当前话题不太容易继续，我们可以换一个轻松的方向，不必勉强保持高频交流。",
            "care": "看起来你最近比较忙，处理好手头的事情更重要，有空时再好好休息。",
            "compliment": "你处理事情时表现出的认真和独立思考，是值得被尊重的特质。",
            "values": "如果两个人产生分歧，你更倾向于先冷静整理情绪，还是及时进行坦诚沟通？",
            "intimacy": "和你交流让我感到放松，我愿意在相互尊重的基础上继续了解彼此。",
            "closing": "今天先聊到这里，你先处理好自己的事情，之后我们再找合适的时间继续。",
            "analyze": "建议基于对方持续的具体回应和主动程度观察沟通状态，不要仅凭单条消息作结论。",
        },
    }
    if tone in tone_variants and scenario in tone_variants[tone]:
        content = tone_variants[tone][scenario]
    style = tone if tone in {"natural", "warm", "humorous", "mature"} else "natural"
    return {"analysis": analysis, "suggestions": [{"content": content, "style": style, "reason": reason}], "risk_level": "none", "risk_notice": None, "next_step": next_step}

def _mock_response(messages: list[dict[str, str]], *, json_mode: bool) -> str:
    user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    if "PROFILE_POLISH" in user:
        return json.dumps({"polished": user.split("\n", 1)[-1][:300], "changed_points": ["保留原意并调整表达"]}, ensure_ascii=False)
    if "SEARCH_PARSE" in user:
        return json.dumps({"normalized_query": user.split("\n", 1)[-1][:500], "filters": {}, "unresolved": []}, ensure_ascii=False)
    if "MATCH_EXPLAIN" in user:
        return json.dumps({"reason": "资料中的兴趣和生活方式存在重合，建议从共同兴趣开始交流。", "suggestions": ["可以从共同兴趣开始聊天"]}, ensure_ascii=False)
    if "ADVISOR_ADVICE" in user:
        scenario = _prompt_value(user, "Scenario") or "reply"
        tone = _prompt_value(user, "Tone") or "natural"
        return json.dumps(_advisor_mock_advice(scenario, tone), ensure_ascii=False)
    return "我可以帮你梳理这段聊天，并给出更具体的沟通建议。"




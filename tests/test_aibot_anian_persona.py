"""阿念良配式建档人设：维池 / 门禁口径 / 访谈；其他红娘不变。"""

from app.services.aibot_prompts import build_system_prompt, fallback_reply, get_agent_meta


def test_anian_persona_has_dim_pool_and_gate_tone():
    meta = get_agent_meta("anian")
    persona = meta["persona"]
    assert "顺着聊" in persona
    assert "跟话题" in persona
    assert "选项优先" in persona
    assert "preferred_name" in persona or "称呼" in persona
    assert "填表" in persona
    assert "专业红娘" in persona
    assert "恋爱报告" in persona
    assert "换个方向" in persona
    assert "维池" in persona
    assert "定居" in persona
    assert "生育" in persona
    assert "红线" in persona
    assert "兴趣" in persona
    assert "资产净值" in persona
    assert "父母社保" in persona
    assert "竞品" in persona
    assert "我对你的理解" in persona
    assert "【访谈开场】" in persona or "访谈开场" in persona
    assert "采集追问" in persona
    assert "<<SKIP_PROBE>>" in persona


def test_anian_fallback_collect_probe_stays_chat():
    out = fallback_reply(
        "anian",
        "【采集追问】用户刚答：题目「你现在更想？」，答案「认真找对象」。请接住再追问一层。",
    )
    assert out["intent"] == "chat"
    assert out["choices"] == []
    assert "记下" in out["reply"] or "具体" in out["reply"]


def test_anian_fallback_interview_kickoff_stays_chat():
    out = fallback_reply(
        "anian",
        "【访谈开场】身份锚点已齐。请按阿念建档维池自由访谈。当前尚未覆盖的维（请优先补）：settle_city、kids。",
    )
    assert out["intent"] == "chat"
    assert out["choices"] == []
    assert "定居" in out["reply"] or "城市" in out["reply"]


def test_anian_fallback_interview_continue_stays_chat():
    out = fallback_reply(
        "anian",
        "【访谈续聊】请优先补：希望定居城市、生育意向。不要问资产净值。",
    )
    assert out["intent"] == "chat"
    assert out["choices"] == []


def test_anian_system_prompt_keeps_common_guardrails():
    prompt = build_system_prompt("anian", nickname="林晓")
    assert "顺着聊" in prompt
    assert "申请认识" in prompt
    assert "不主动推销" in prompt
    assert "维池" in prompt


def test_other_agents_persona_unchanged_style():
    yuzhou = get_agent_meta("yuzhou")["persona"]
    assert "顺着聊" not in yuzhou
    assert "维池" not in yuzhou
    assert "30 岁以内男士" in yuzhou


def test_anian_fallback_follows_snippet_without_triple_buttons():
    out = fallback_reply("anian", "我刚创业很累，压力好大")
    assert out["intent"] == "chat"
    assert "创业" in out["reply"] or "累" in out["reply"] or "压力" in out["reply"]
    assert out["choices"] == []


def test_yuzhou_fallback_still_offers_default_choices():
    out = fallback_reply("yuzhou", "随便聊聊")
    assert len(out["choices"]) >= 2

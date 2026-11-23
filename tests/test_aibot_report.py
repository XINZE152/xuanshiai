"""恋爱报告生成口径：素材三段、结构收敛、情绪兜底、分项条收敛。"""

from app.services.aibot_report import (
    _member_title,
    _normalize_bars,
    _normalize_narrative,
    _rule_emotion,
    apply_live_member_basics,
    build_report_messages,
)


def test_prompt_carries_three_sources_and_no_invented_score():
    messages = build_report_messages(
        agent_id="anian",
        nickname="林晓",
        member_facts={"gender": "女", "city": "杭州", "gender_source": "precise"},
        ai_lines=["我的画像｜性格特征：慢热，熟了话多"],
        chat_lines=["用户：最近有点累", "红娘：先别急，我们慢慢来"],
        score_total=78,
        love_index_label="积极期",
    )
    assert len(messages) == 2
    system, user = messages[0]["content"], messages[1]["content"]
    # 三块素材都要进提示词；聊天须驱动整份再分析，不只是情绪
    assert "会员资料" in user and "AI 画像" in user and "最新 BOT 聊天记录" in user
    assert "整体重写" in user
    assert "一次输出三段" in system or "dimension_items" in system
    # 阿念分支：长文 AI 解读 + 维度细项
    assert "ai_reading" in user
    assert "900" in user or "900" in system
    assert "有意思的矛盾" in user
    assert "我说说我对你的理解" in user
    assert "dimension_items" in user
    assert "说到心坎" in system or "信任" in system
    assert "外部竞品" in system
    # 来源标记这类内部字段不得当成事实喂给模型
    assert "gender_source" not in user
    assert "78" in user
    # 红线写死在 system：不承诺结果、不制造焦虑
    assert "不承诺结果" in system or "不承诺关系结果" in system
    assert "命中注定" in system


def test_other_agent_report_prompt_has_no_anian_portrait_block():
    messages = build_report_messages(
        agent_id="yuzhou",
        nickname="会员",
        member_facts={},
        ai_lines=[],
        chat_lines=[],
        score_total=60,
        love_index_label="观望",
    )
    system, user = messages[0]["content"], messages[1]["content"]
    assert "ai_reading" not in user
    assert "portrait_summary" not in user
    assert "我说说我对你的理解" not in user
    assert "予舟" in system or "专属红娘【予舟】" in system


def test_normalize_keeps_ai_reading_and_portrait_summary():
    narrative = _normalize_narrative(
        {
            "letter": "寄语",
            "ai_reading": "我说说我对你的理解，你看准不准——你很清醒。",
            "dimension_items": [
                {"dim": "消费习惯", "side": "me", "text": "我习惯存钱并做计划。"},
                {"dim": "消费习惯", "side": "partner", "text": "我希望对方有储蓄意识。"},
                {"dim": "兴趣爱好", "side": "关于我", "text": "我喜欢小狗。"},
            ],
            "emotion": {"label": "平稳", "score": 0.2, "trend": "平"},
        }
    )
    assert "清醒" in narrative["ai_reading"]
    assert narrative["portrait_summary"] == narrative["ai_reading"]
    assert narrative["letter"] == "寄语"
    assert len(narrative["dimension_items"]) == 3
    assert narrative["dimension_items"][0]["side"] == "me"
    assert narrative["dimension_items"][1]["side"] == "partner"
    assert narrative["dimension_items"][2]["side"] == "me"

    # 兼容旧字段：仅 portrait_summary 时回填 ai_reading
    legacy = _normalize_narrative(
        {
            "letter": "短",
            "portrait_summary": "旧字段长文解读",
            "emotion": {"label": "平稳", "score": 0.1, "trend": "平"},
        }
    )
    assert legacy["ai_reading"] == "旧字段长文解读"
    assert legacy["portrait_summary"] == "旧字段长文解读"
    assert legacy["dimension_items"] == []


def test_member_title_does_not_default_to_female():
    assert _member_title("男") == "先生"
    assert _member_title("女") == "女士"
    assert _member_title(None) == ""
    assert _member_title("") == ""
    assert _member_title("未知") == ""


def test_apply_live_member_basics_refreshes_profile_without_touching_ai_copy():
    payload = {
        "member": {"name": "旧名", "title": "女士", "id": "XS-1"},
        "part01": {
            "profile": [{"k": "性别", "v": "女"}],
            "strengths": ["AI写的优势"],
            "summary": "AI摘要",
        },
        "letter": "旧寄语",
    }
    out = apply_live_member_basics(
        payload,
        nickname="新昵称",
        member_facts={"gender": "男", "city": "杭州", "age_band": "1991–1995"},
    )
    assert out["member"]["name"] == "新昵称"
    assert out["member"]["title"] == "先生"
    assert out["member"]["id"] == "XS-1"
    keys = {row["k"] for row in out["part01"]["profile"]}
    assert "性别" in keys and "城市" in keys and "年龄段" in keys
    assert {"k": "性别", "v": "男"} in out["part01"]["profile"]
    # AI 正文不动
    assert out["part01"]["strengths"] == ["AI写的优势"]
    assert out["part01"]["summary"] == "AI摘要"
    assert out["letter"] == "旧寄语"


def test_prompt_drops_score_number_when_client_did_not_send():
    messages = build_report_messages(
        agent_id="chengan",
        nickname="会员",
        member_facts={},
        ai_lines=[],
        chat_lines=[],
        score_total="未提供（按资料定性描述，不要写数字）",
        love_index_label="未明确",
    )
    user = messages[1]["content"]
    assert "不要写数字" in user
    assert "还没有对话" in user


def test_normalize_rejects_out_of_contract_values():
    narrative = _normalize_narrative(
        {
            "letter": " 你好 ",
            "emotion": {"label": "平稳期待", "score": 3.5, "trend": "乱填", "evidence": ["a", "b"]},
            "part04": {
                "current": [
                    {"item": "期望年龄", "raw": "28-33", "advice": "两端各放宽 2 岁", "tier": "瞎写"},
                    {"item": "", "raw": "x"},
                ]
            },
            "part06": {"issues": [{"name": "标准", "level": "极高", "note": "说明"}]},
        }
    )
    assert narrative["letter"] == "你好"
    assert narrative["emotion"]["score"] == 1.0  # 夹到 [-1, 1]
    assert narrative["emotion"]["trend"] == "平"  # 非法趋势回落
    assert narrative["part04"]["current"][0]["tier"] == "可调整项"
    assert len(narrative["part04"]["current"]) == 1  # 空 item 丢弃
    assert narrative["part06"]["issues"][0]["level"] == "中"


def test_rule_emotion_is_conservative_without_chat():
    empty = _rule_emotion([])
    assert empty["label"] == "待观察"
    assert empty["score"] == 0.0
    assert empty["evidence"]

    low = _rule_emotion(["用户：最近很累，也很焦虑，不太想聊"])
    assert low["label"] == "压力偏高"
    assert low["trend"] == "降"
    assert low["score"] < 0

    high = _rule_emotion(["用户：想认识看看，挺期待的，愿意见面"])
    assert high["label"] == "平稳偏好"
    assert high["trend"] == "升"


def test_bars_clamped_and_sorted():
    bars = _normalize_bars(
        [
            {"name": "外形条件", "pct": 120},
            {"name": "学历事业", "pct": -5},
            {"name": "", "pct": 50},
            "not a dict",
        ]
    )
    assert bars == [{"name": "外形条件", "pct": 100}, {"name": "学历事业", "pct": 0}]

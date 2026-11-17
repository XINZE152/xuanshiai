"""恋爱报告生成口径：素材三段、结构收敛、情绪兜底、分项条收敛。"""

from app.services.aibot_report import (
    _normalize_bars,
    _normalize_narrative,
    _rule_emotion,
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
    # 三块素材都要进提示词
    assert "会员资料" in user and "AI 画像" in user and "最近对话" in user
    # 来源标记这类内部字段不得当成事实喂给模型
    assert "gender_source" not in user
    assert "78" in user
    # 红线写死在 system：不承诺结果、不制造焦虑
    assert "不承诺关系结果" in system
    assert "命中注定" in system


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

"""BOT 模糊采集 ↔ 精准字段双轨规则。"""

from app.services.aibot_profile_sync import (
    facts_for_ai,
    merge_bot_intake,
    precise_fills_from_bot_intake,
)


def test_merge_keeps_option_as_is():
    merged = merge_bot_intake({}, {"ageBand": {"id": "age_28_32", "label": "28-32岁"}, "edu": "edu_211"})
    assert merged["ageBand"]["id"] == "age_28_32"
    assert merged["ageBand"]["label"] == "28-32岁"
    assert merged["edu"]["id"] == "edu_211"
    assert merged["edu"]["label"] == "edu_211"


def test_precise_fill_only_alignable_and_empty():
    bot = merge_bot_intake(
        {},
        {
            "gender": {"id": "女", "label": "女生"},
            "marital": {"id": "divorced", "label": "离异"},
            "edu": {"id": "edu_bach", "label": "全日制本科"},
            "height": {"id": "h_165_170", "label": "165-170"},
            "job": {"id": "job_teacher", "label": "教师"},
        },
    )
    fills = precise_fills_from_bot_intake(
        bot,
        current_gender=None,
        current_is_married=None,
        current_education_level=None,
        current_occupation=None,
    )
    assert fills["gender"] == 2
    assert fills["education_level"] == 3
    assert fills["occupation"] == "教师"
    # 离异不对齐 is_married；身高区间不取中值
    assert "is_married" not in fills
    assert "height" not in fills


def test_precise_not_overwritten_when_present():
    bot = merge_bot_intake({}, {"gender": {"id": "男", "label": "男生"}, "edu": {"id": "edu_col", "label": "大专"}})
    fills = precise_fills_from_bot_intake(
        bot,
        current_gender=2,
        current_is_married=None,
        current_education_level=4,
        current_occupation="工程师",
    )
    assert fills == {}


def test_facts_prefer_precise_fallback_bot():
    facts = facts_for_ai(
        precise={"gender": 1, "height": None, "education_level": 3},
        bot_intake={
            "gender": {"id": "女", "label": "女生"},
            "height": {"id": "h_170", "label": "170左右"},
            "edu": {"id": "edu_211", "label": "211"},
        },
    )
    assert facts["gender"] == "男"
    assert facts["gender_source"] == "precise"
    assert facts["height_band"] == "170左右"
    assert facts["height_source"] == "bot"
    assert facts["education_level"] == 3
    assert facts["education_source"] == "precise"

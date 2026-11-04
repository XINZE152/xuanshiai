"""资料完整度权重模型（profile-v5）契约测试。

钉住 2026-11-02 需求：
- 27 个计分项权重合计 100；
- 9 项详细资料纳入权重；
- score 与 items 共用同一份 completed 判定；
- 未填项权重之和恒等于 100 - score。
"""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import profile
from app.services.profile import (
    COMPLETION_RULES,
    _completion_score,
    _completion_state,
)


def _base_row() -> dict[str, Any]:
    return {
        "gender": None,
        "birthday": None,
        "is_married": None,
        "avatar": None,
        "is_single_pledge": 0,
        "realname_status": 0,
        "school": None,
        "company": None,
        "occupation": None,
        "education_level": None,
        "income": None,
        "height": None,
        "weight": None,
        "self_intro": None,
        "household": None,
        "household_province_code": None,
        "household_city_code": None,
        "ethnicity": None,
        "house": None,
        "car": None,
        "smoking": None,
        "drinking": None,
        "religion": None,
        "hometown_province_code": None,
        "hometown_city_code": None,
        "residence_province_code": None,
        "residence_city_code": None,
        "interest_tags": None,
        "personality_tags": None,
        "mbti": None,
        "tags": None,
        "preference_age_min": None,
        "preference_age_max": None,
        "album_done": 0,
    }


def _full_row() -> dict[str, Any]:
    row = _base_row()
    row.update(
        {
            "gender": 1,
            "birthday": date(1995, 5, 20),
            "is_married": 1,
            "avatar": "https://example.com/avatar.jpg",
            "is_single_pledge": 1,
            "realname_status": 2,
            "school": "浙江大学",
            "company": "某公司",
            "occupation": "教师",
            "education_level": 3,
            "income": 200000,
            "height": 175,
            "weight": 65,
            "self_intro": "认真生活，也认真期待一段稳定、真诚的关系，愿意一起成长。",
            "household_province_code": "330000",
            "household_city_code": "330100",
            "ethnicity": "汉族",
            "house": "已购房",
            "car": "已购车",
            "smoking": "不吸烟",
            "drinking": "不饮酒",
            "religion": "无宗教信仰",
            "hometown_province_code": "330000",
            "hometown_city_code": "330100",
            "residence_province_code": "310000",
            "residence_city_code": "310100",
            "interest_tags": '["做旅行攻略", "每周固定运动", "摄影旅行"]',
            "mbti": "INTJ",
            "preference_age_min": 25,
            "preference_age_max": 35,
            "album_done": 1,
        }
    )
    return row


def test_completion_rules_total_100() -> None:
    assert sum(weight for _, _, weight in COMPLETION_RULES) == 100
    assert len(COMPLETION_RULES) == 27


def test_detailed_fields_are_scored() -> None:
    keys = {key for key, _, _ in COMPLETION_RULES}
    assert {
        "company",
        "school",
        "house",
        "car",
        "household",
        "ethnicity",
        "religion",
        "smoking",
        "drinking",
    } <= keys


def test_empty_profile_scores_zero_and_all_weights_missing() -> None:
    completed = _completion_state(_base_row())
    score = _completion_score(completed)
    missing_weight = sum(weight for key, _, weight in COMPLETION_RULES if not completed[key])

    assert score == 0
    assert missing_weight == 100


def test_full_profile_scores_100_without_missing_weight() -> None:
    completed = _completion_state(_full_row())
    score = _completion_score(completed)
    missing_weight = sum(weight for key, _, weight in COMPLETION_RULES if not completed[key])

    assert score == 100
    assert missing_weight == 0


def test_score_plus_missing_weights_equals_100_for_middle_state() -> None:
    row = _full_row()
    row.update(
        {
            "avatar": None,
            "self_intro": None,
            "house": None,
            "company": None,
            "religion": None,
        }
    )
    completed = _completion_state(row)
    score = _completion_score(completed)
    missing_weight = sum(weight for key, _, weight in COMPLETION_RULES if not completed[key])

    assert score + missing_weight == 100
    assert 0 < score < 100


def test_placeholder_text_is_not_completed() -> None:
    row = _full_row()
    row.update({"house": "未填写", "car": "请选择", "smoking": " ", "company": "未填写"})
    completed = _completion_state(row)

    assert completed["house"] is False
    assert completed["car"] is False
    assert completed["smoking"] is False
    assert completed["company"] is False


def test_household_codes_complete_without_text() -> None:
    row = _base_row()
    row.update({"household_province_code": "330000", "household_city_code": "330100"})
    completed = _completion_state(row)

    assert completed["household"] is True


@pytest.mark.asyncio
async def test_get_completion_returns_score_and_items_from_same_state() -> None:
    row = _full_row()
    result = MagicMock()
    result.mappings.return_value.first.return_value = row
    result.mappings.return_value.one.return_value = row
    db = AsyncMock()
    db.execute.return_value = result

    completion = await profile.get_completion(db, 42)

    assert completion.score == 100
    assert sum(item.weight for item in completion.items if not item.completed) == 0
    assert completion.can_browse is True
    assert completion.can_apply is True
    assert completion.can_chat is True


@pytest.mark.asyncio
async def test_recalculate_completion_writes_profile_v5() -> None:
    row = _base_row()
    result = MagicMock()
    result.mappings.return_value.first.return_value = row
    db = AsyncMock()
    db.execute.return_value = result

    score = await profile.recalculate_completion(db, 42)

    assert score == 0
    statements = "\n".join(str(call.args[0]) for call in db.execute.await_args_list)
    assert "profile-v5" in statements

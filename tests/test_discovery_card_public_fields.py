"""卡片公开字段补齐（2026-10-29）：weight / hometown_display / residence_display / zodiac。

首页 9 格此前靠"再拉一次完整详情"补字段（详见 B-02），会把首页变成一次资料查看。
现在直接随卡片下发，命名与 ``ProfileResponse`` 一致（``weight`` 而非 ``weight_kg``）。
"""

from __future__ import annotations

from typing import Any

from app.services.discovery import CARD_SELECT, _card


def _row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "user_id": 9,
        "nickname": "阿乙",
        "avatar": None,
        "birthday": None,
        "height": 165,
        "education_level": 3,
        "occupation": "教师",
        "income": None,
        "residence_province_code": "360000",
        "residence_city_code": "360100",
        "residence_district_code": "360102",
        "hometown_province_code": "360000",
        "hometown_city_code": "360800",
        "hometown_district_code": None,
        "same_city": 1,
        "mbti": None,
        "self_intro": None,
        "qa_answers": None,
        "interest_tags": "[]",
        "personality_tags": "[]",
        "tags": None,
        "realname_status": 0,
        "is_single_pledge": 0,
        "is_vip": 0,
        "is_boosted": 0,
        "is_favorite": False,
        "is_liked": False,
        "weight": 52,
        "zodiac": "属兔",
        "online_status": 0,
        "hide_online_status": 0,
        "is_married": 1,
    }
    row.update(overrides)
    return row


def test_card_select_carries_raw_columns_for_display_fields() -> None:
    """展示字段由服务端按码合成，SELECT 必须带齐原始码与 weight/zodiac。"""
    for column in (
        "p.weight",
        "p.zodiac",
        "p.hometown_province_code",
        "p.hometown_city_code",
        "p.hometown_district_code",
        "p.residence_province_code",
        "p.residence_city_code",
        "p.residence_district_code",
    ):
        assert column in CARD_SELECT, column


def test_card_returns_public_fields_when_unlocked() -> None:
    card = _card(_row(), 60.0, "r")
    assert card.weight == 52
    assert card.zodiac == "属兔"
    assert card.hometown_display == "江西省 吉安市"
    assert card.residence_display == "江西省 南昌市 东湖区"


def test_card_clears_public_fields_when_detail_locked() -> None:
    card = _card(_row(), 60.0, "r", detail_locked=True)
    assert card.weight is None
    assert card.zodiac is None
    assert card.hometown_display is None
    assert card.residence_display is None


def test_card_tolerates_missing_columns() -> None:
    """历史行/裁剪查询可能缺列，必须降级为 None 而不是抛 KeyError。"""
    row = _row()
    for key in (
        "weight",
        "zodiac",
        "hometown_province_code",
        "hometown_city_code",
        "hometown_district_code",
        "residence_province_code",
        "residence_district_code",
    ):
        row.pop(key, None)
    card = _card(row, 60.0, "r")
    assert card.weight is None
    assert card.zodiac is None
    assert card.hometown_display is None
    # 只剩 residence_city_code 时按可用码合成（缺省码就不拼省名）
    assert card.residence_display == "南昌市"

"""``is_liked`` 与 ``is_favorite`` 的语义隔离（2026-10-29）。

背景：``DiscoveryCard`` 原先只有 ``is_favorite``（``user_favorite.type=2`` 旧收藏），
前端红心只读 ``is_liked``（``type=1`` 喜欢），导致红心恒空心、且首次点击会走 DELETE
弹「已取消收藏」。
"""

from __future__ import annotations

from typing import Any

from app.services.discovery import CARD_SELECT, _card


def _row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "user_id": 7,
        "nickname": "阿甲",
        "avatar": "a.png",
        "birthday": None,
        "height": 170,
        "education_level": 3,
        "occupation": "工程师",
        "income": 15000,
        "residence_province_code": "360000",
        "residence_city_code": "360100",
        "residence_district_code": None,
        "hometown_province_code": None,
        "hometown_city_code": None,
        "hometown_district_code": None,
        "same_city": 1,
        "mbti": "ENFP",
        "self_intro": "hi",
        "qa_answers": None,
        "interest_tags": "[]",
        "personality_tags": "[]",
        "tags": None,
        "realname_status": 2,
        "is_single_pledge": 1,
        "is_vip": 0,
        "is_boosted": 0,
        "is_favorite": False,
        "is_liked": False,
        "weight": 62,
        "zodiac": "属虎",
        "online_status": 1,
        "hide_online_status": 0,
        "is_married": 1,
    }
    row.update(overrides)
    return row


def test_card_select_reads_like_and_favorite_from_different_types() -> None:
    """SQL 必须分别取 type=1（喜欢）与 type=2（旧收藏），不得复用同一条件。"""
    assert "AS is_liked" in CARD_SELECT
    assert "AS is_favorite" in CARD_SELECT
    liked_clause = CARD_SELECT.split("AS is_liked")[0].rsplit("EXISTS", 1)[1]
    favorite_clause = CARD_SELECT.split("AS is_favorite")[0].rsplit("EXISTS", 1)[1]
    assert "f.type = 1" in liked_clause
    assert "f.type = 2" in favorite_clause


def test_card_maps_is_liked_independently_of_is_favorite() -> None:
    liked_only = _card(_row(is_liked=True, is_favorite=False), 70.0, "r")
    assert liked_only.is_liked is True
    assert liked_only.is_favorite is False

    favorite_only = _card(_row(is_liked=False, is_favorite=True), 70.0, "r")
    assert favorite_only.is_liked is False
    assert favorite_only.is_favorite is True


def test_is_liked_survives_detail_locked() -> None:
    """关系字段不受隐私/锁定影响：对方资料锁了，也要能看出自己有没有喜欢过。"""
    card = _card(_row(is_liked=True), 70.0, "r", detail_locked=True)
    assert card.is_liked is True
    assert card.detail_locked is True
    # 对照：普通资料字段在锁定态必须清空
    assert card.height is None
    assert card.mbti is None

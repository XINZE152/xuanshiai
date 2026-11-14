"""首页卡片「星座」展示契约（MEETING 无关，2026-11-14）。

背景：C 端首页「基础资料」第 4 格由「属相」改为「星座」。卡片原本只下发
``zodiac``（属相），本次新增 ``constellation``：优先取 ``user_profile.constellation``，
老数据为空时按生日推导。本文件锁住两件事：

1. 推导表本身正确（每个月切换日的前一天仍属上一个星座）；
2. ``_card`` 的取值顺序与锁定态：手填值优先，生日兜底，``detail_locked`` 一律 None。

纯函数测试，不依赖数据库，本地无 MySQL 也能跑。
"""

from datetime import date

import pytest

from app.services.discovery import _card, _constellation_from_birthday

# (生日, 期望星座)：每个星座的切换日与切换日前一天，覆盖 12 个边界
BOUNDARY_CASES = [
    (date(2000, 1, 19), "摩羯座"),
    (date(2000, 1, 20), "水瓶座"),
    (date(2000, 2, 18), "水瓶座"),
    (date(2000, 2, 19), "双鱼座"),
    (date(2000, 3, 20), "双鱼座"),
    (date(2000, 3, 21), "白羊座"),
    (date(2000, 4, 19), "白羊座"),
    (date(2000, 4, 20), "金牛座"),
    (date(2000, 5, 20), "金牛座"),
    (date(2000, 5, 21), "双子座"),
    (date(2000, 6, 21), "双子座"),
    (date(2000, 6, 22), "巨蟹座"),
    (date(2000, 7, 22), "巨蟹座"),
    (date(2000, 7, 23), "狮子座"),
    (date(2000, 8, 22), "狮子座"),
    (date(2000, 8, 23), "处女座"),
    (date(2000, 9, 22), "处女座"),
    (date(2000, 9, 23), "天秤座"),
    (date(2000, 10, 23), "天秤座"),
    (date(2000, 10, 24), "天蝎座"),
    (date(2000, 11, 22), "天蝎座"),
    (date(2000, 11, 23), "射手座"),
    (date(2000, 12, 21), "射手座"),
    (date(2000, 12, 22), "摩羯座"),
]


@pytest.mark.parametrize(("birthday", "expected"), BOUNDARY_CASES)
def test_constellation_switch_days(birthday: date, expected: str) -> None:
    assert _constellation_from_birthday(birthday) == expected


@pytest.mark.parametrize("value", [None, "", "1990-01-01", 19900101])
def test_constellation_ignores_non_date(value: object) -> None:
    assert _constellation_from_birthday(value) is None


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "user_id": 1,
        "birthday": date(2000, 10, 23),
        "zodiac": "龙",
        "realname_status": 2,
    }
    row.update(overrides)
    return row


def test_card_prefers_stored_constellation() -> None:
    card = _card(_row(constellation="双鱼座"), 60.0, "测试")
    assert card.constellation == "双鱼座"


def test_card_falls_back_to_birthday() -> None:
    # 2026-11-14：老数据 constellation 为空时必须用生日兜底，否则首页显示「未填写」
    # 而详情页有星座，两页自相矛盾。
    card = _card(_row(constellation=None), 60.0, "测试")
    assert card.constellation == "天秤座"


def test_card_hides_constellation_when_locked() -> None:
    card = _card(_row(constellation="双鱼座"), 60.0, "测试", detail_locked=True)
    assert card.constellation is None
    # 属相字段保持不变，避免影响详情页「属相」独立格
    assert card.zodiac is None


def test_card_keeps_zodiac_field() -> None:
    card = _card(_row(), 60.0, "测试")
    assert card.zodiac == "龙"

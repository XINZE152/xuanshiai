"""会员档案地区展示口径回归：编码优先，没有编码时回落到旧文本列。

修复点：``GET /users/{id}/profile`` 只用省市区编码拼 ``residence_display``/
``hometown_display``，而红娘录入（以及历史演示数据）在 user_profile 里只留了
「浙江 杭州」这类文本，编码全空，于是红娘待审卡片显示了地区、会员主页同一栏
显示「未填写」。卡片口径（文本优先、编码兜底）已经是他认可的，主页必须一致。
新增的 hometown_text/residence_text 只服务这层兜底，不随接口返回。
"""

from __future__ import annotations

import pytest

from app.schemas.auth import ProfileResponse
from app.services.profile import get_profile

PROFILE_COLUMNS = (
    "user_id", "nickname", "gender", "birthday", "is_married", "avatar",
    "height", "weight", "occupation", "industry", "education_level", "income",
    "house", "car", "ethnicity", "ideal_partner",
    "hometown_text", "residence_text",
    "hometown_province_code", "hometown_city_code", "hometown_district_code",
    "residence_province_code", "residence_city_code", "residence_district_code",
    "self_intro", "qa_answers", "interest_tags", "personality_tags", "mbti", "tags",
    "completion_score", "hide_school", "hide_company", "only_vip_can_see_detail",
)


class FakeResult:
    def __init__(self, row: dict | None) -> None:
        self._row = row

    def mappings(self) -> "FakeResult":
        return self

    def first(self) -> dict | None:
        return self._row

    def all(self) -> list[dict]:
        return []


class FakeDB:
    def __init__(self, row: dict) -> None:
        self.row = row

    async def execute(self, statement: object, params: dict | None = None) -> FakeResult:
        return FakeResult(self.row)


def _row(**overrides: object) -> dict:
    row: dict = {column: None for column in PROFILE_COLUMNS}
    row.update({
        "user_id": 41,
        "nickname": "主页验证会员",
        "gender": 1,
        "interest_tags": [],
        "personality_tags": [],
        "tags": None,
        "qa_answers": None,
        "hide_school": 0,
        "hide_company": 0,
        "only_vip_can_see_detail": 0,
        "completion_score": 48.0,
    })
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_region_display_prefers_codes_over_legacy_text() -> None:
    db = FakeDB(_row(
        residence_text="浙江 杭州",
        residence_province_code="330000",
        residence_city_code="330100",
    ))
    data = await get_profile(db, 41)  # type: ignore[arg-type]
    assert data["residence_display"] == "浙江省 杭州市"


@pytest.mark.asyncio
async def test_region_display_falls_back_to_legacy_text_without_codes() -> None:
    """只有文本的会员（红娘代录、早期演示数据）主页也要显示地区，与待审卡片同口径。"""
    db = FakeDB(_row(residence_text="浙江 杭州", hometown_text="武汉"))
    data = await get_profile(db, 41)  # type: ignore[arg-type]
    assert data["residence_display"] == "浙江 杭州"
    assert data["hometown_display"] == "武汉"


@pytest.mark.asyncio
async def test_region_display_stays_empty_when_nothing_is_filled() -> None:
    db = FakeDB(_row(residence_text="   "))
    data = await get_profile(db, 41, public=True)  # type: ignore[arg-type]
    assert data["residence_display"] is None
    assert data["hometown_display"] is None


@pytest.mark.asyncio
async def test_legacy_text_columns_do_not_leak_into_the_response() -> None:
    """hometown_text/residence_text 只是兜底计算用的内部列，接口响应里只有 *_display。"""
    db = FakeDB(_row(residence_text="浙江 杭州"))
    data = await get_profile(db, 41)  # type: ignore[arg-type]
    assert data["residence_text"] == "浙江 杭州"
    dumped = ProfileResponse.model_validate(data).model_dump()
    assert dumped["residence_display"] == "浙江 杭州"
    assert "hometown_text" not in dumped
    assert "residence_text" not in dumped

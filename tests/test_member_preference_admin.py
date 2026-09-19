"""单元测试：后台管理端择偶要求接口（GET/PUT /admin/members/preference/{user_id}）。

覆盖范围：
- 路由已在 OpenAPI 注册；
- 未登录 401（读 matchmaker.member.read / 写 matchmaker.member.manage）；
- ``MemberPreferenceAdminUpdate`` 入参校验（空 body 报错、年龄/身高区间、
  单选枚举白名单、补充说明 200 字上限）；
- ``MemberPreferenceAdminItem`` 可构造。
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.member_media_admin import MemberPreferenceAdminItem, MemberPreferenceAdminUpdate

client = TestClient(app)

BASE = "/api/v1/admin/members"


# ─── 路由注册（OpenAPI 路径断言） ───────────────────────────────


def test_preference_routes_are_registered() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "get" in paths[f"{BASE}/preference/{{user_id}}"]
    assert "put" in paths[f"{BASE}/preference/{{user_id}}"]


def test_preference_routes_require_authentication() -> None:
    assert client.get(f"{BASE}/preference/1").status_code == 401
    assert client.put(f"{BASE}/preference/1", json={"income_range": "不限"}).status_code == 401


# ─── 入参模型校验 ───────────────────────────────────────────────


def test_update_empty_body_rejected() -> None:
    with pytest.raises(ValidationError):
        MemberPreferenceAdminUpdate()


def test_update_age_range_validated() -> None:
    body = MemberPreferenceAdminUpdate(age_min=22, age_max=30)
    assert (body.age_min, body.age_max) == (22, 30)
    with pytest.raises(ValidationError):
        MemberPreferenceAdminUpdate(age_min=30, age_max=22)
    with pytest.raises(ValidationError):
        MemberPreferenceAdminUpdate(age_min=17)
    with pytest.raises(ValidationError):
        MemberPreferenceAdminUpdate(age_max=101)


def test_update_height_range_validated() -> None:
    body = MemberPreferenceAdminUpdate(height_min=160, height_max=175)
    assert (body.height_min, body.height_max) == (160, 175)
    with pytest.raises(ValidationError):
        MemberPreferenceAdminUpdate(height_min=175, height_max=160)
    with pytest.raises(ValidationError):
        MemberPreferenceAdminUpdate(height_min=99)
    with pytest.raises(ValidationError):
        MemberPreferenceAdminUpdate(height_max=251)


def test_update_single_choice_whitelists() -> None:
    assert MemberPreferenceAdminUpdate(income_range="8千-1万元").income_range == "8千-1万元"
    assert MemberPreferenceAdminUpdate(education_requirement="大专").education_requirement == "大专"
    assert MemberPreferenceAdminUpdate(preferred_occupation="央企/国企").preferred_occupation == "央企/国企"
    assert MemberPreferenceAdminUpdate(marriage_requirement="可接受离异未育").marriage_requirement == "可接受离异未育"
    assert MemberPreferenceAdminUpdate(housing_expectation="要有独立婚房").housing_expectation == "要有独立婚房"
    assert MemberPreferenceAdminUpdate(smoking_expectation="可以偶尔吸烟").smoking_expectation == "可以偶尔吸烟"
    assert MemberPreferenceAdminUpdate(drinking_expectation="可以偶尔小酌").drinking_expectation == "可以偶尔小酌"
    assert MemberPreferenceAdminUpdate(marriage_timeline="时机成熟时结婚").marriage_timeline == "时机成熟时结婚"
    # 白名单外一律拒绝
    with pytest.raises(ValidationError):
        MemberPreferenceAdminUpdate(income_range="月薪五千万")
    with pytest.raises(ValidationError):
        MemberPreferenceAdminUpdate(housing_expectation="有房")
    with pytest.raises(ValidationError):
        MemberPreferenceAdminUpdate(marriage_timeline="十年内结婚")


def test_update_extra_requirement_limit_200() -> None:
    assert MemberPreferenceAdminUpdate(extra_requirement="x" * 200).extra_requirement == "x" * 200
    with pytest.raises(ValidationError):
        MemberPreferenceAdminUpdate(extra_requirement="x" * 201)


def test_update_full_body_ok() -> None:
    body = MemberPreferenceAdminUpdate(
        age_min=22,
        age_max=30,
        height_min=160,
        height_max=175,
        income_range="1-2万元",
        education_requirement="本科",
        preferred_occupation="公务员",
        marriage_requirement="不限",
        housing_expectation="住房无所谓",
        smoking_expectation="不接受吸烟",
        drinking_expectation="不限",
        marriage_timeline="两年内结婚",
        extra_requirement="希望同城",
    )
    assert body.preferred_occupation == "公务员"
    assert body.smoking_expectation == "不接受吸烟"


# ─── 返回模型可构造 ─────────────────────────────────────────────


def test_item_construct() -> None:
    item = MemberPreferenceAdminItem(
        id=1,
        user_id=1,
        member_code="G000001",
        nickname="测试",
        avatar=None,
        age_min=22,
        age_max=30,
        height_min=160,
        height_max=175,
        income_range="年入百万",
        education_requirement="硕士",
        preferred_occupation="医生",
        marriage_requirement="视情况而定",
        housing_expectation="愿意和父母同住",
        smoking_expectation="吸烟无所谓",
        drinking_expectation="喝酒无所谓",
        marriage_timeline="一年内结婚",
        extra_requirement=None,
        updated_at=None,
    )
    assert item.member_code == "G000001"
    assert item.income_range == "年入百万"
    assert item.marriage_timeline == "一年内结婚"

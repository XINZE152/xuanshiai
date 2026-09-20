"""单元测试：后台会员私密资料接口（GET/PUT /admin/members/private-info/{user_id}）。

覆盖范围：
- 路由已在 OpenAPI 注册；
- 未登录 401（读 matchmaker.member.read / 写 matchmaker.member.manage）；
- 建表 DDL 存在且含唯一键、核心列；
- ``MemberPrivateInfoUpdate`` 入参校验（空 body 报错、长度上限、独生子女白名单）；
- ``MemberPrivateInfoItem`` 可构造。
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.db.business_schema import BUSINESS_TABLES
from app.main import app
from app.schemas.member_media_admin import MemberPrivateInfoItem, MemberPrivateInfoUpdate

client = TestClient(app)

BASE = "/api/v1/admin/members"


# ─── 路由注册（OpenAPI 路径断言） ───────────────────────────────


def test_private_info_routes_are_registered() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "get" in paths[f"{BASE}/private-info/{{user_id}}"]
    assert "put" in paths[f"{BASE}/private-info/{{user_id}}"]


def test_private_info_routes_require_authentication() -> None:
    assert client.get(f"{BASE}/private-info/1").status_code == 401
    assert client.put(f"{BASE}/private-info/1", json={"body_type": "匀称"}).status_code == 401


# ─── 建表 DDL ───────────────────────────────────────────────────


def test_private_info_table_ddl() -> None:
    ddl = BUSINESS_TABLES["user_private_info"]
    assert "uk_private_info_user" in ddl
    for column in (
        "body_type", "face_type", "skin_type", "eye_type", "love_experience",
        "longest_love", "single_duration", "work_status", "rest_schedule",
        "health_condition", "infectious_disease", "genetic_disease",
        "bad_habits", "criminal_record", "emotional_status", "children_status",
        "breakup_reason", "love_bottom_line", "divorce_reason",
        "parents_status", "family_structure", "family_members", "sibling_rank",
        "only_child", "other_members", "father_age", "father_occupation",
        "father_health", "father_retirement", "mother_age", "mother_occupation",
        "mother_health", "mother_retirement", "other_info",
    ):
        assert f"`{column}`" in ddl


# ─── 入参模型校验 ───────────────────────────────────────────────


def test_update_empty_body_rejected() -> None:
    with pytest.raises(ValidationError):
        MemberPrivateInfoUpdate()


def test_update_field_length_limits() -> None:
    assert MemberPrivateInfoUpdate(body_type="匀称").body_type == "匀称"
    assert MemberPrivateInfoUpdate(breakup_reason="x" * 200).breakup_reason == "x" * 200
    with pytest.raises(ValidationError):
        MemberPrivateInfoUpdate(breakup_reason="x" * 201)
    assert MemberPrivateInfoUpdate(other_info="汉" * 1000).other_info == "汉" * 1000
    with pytest.raises(ValidationError):
        MemberPrivateInfoUpdate(other_info="汉" * 1001)


def test_update_only_child_whitelist() -> None:
    assert MemberPrivateInfoUpdate(only_child="独生").only_child == "独生"
    with pytest.raises(ValidationError):
        MemberPrivateInfoUpdate(only_child="有兄弟")


def test_update_full_body_ok() -> None:
    body = MemberPrivateInfoUpdate(
        body_type="微胖",
        face_type="瓜子脸",
        longest_love="半年以内",
        rest_schedule="单休",
        criminal_record="无",
        breakup_reason="性格不合",
        love_bottom_line="不能接受欺骗",
        divorce_reason=None,
        family_structure="三口之家",
        only_child="独生",
        father_occupation="教师",
        mother_retirement="已退休",
        other_info="红娘补充说明",
    )
    assert body.only_child == "独生"
    assert body.father_occupation == "教师"


# ─── 返回模型可构造 ─────────────────────────────────────────────


def test_item_construct() -> None:
    item = MemberPrivateInfoItem(
        id=1,
        user_id=1,
        member_code="G000001",
        nickname="测试",
        body_type="未知",
        infectious_disease="无",
        love_bottom_line="真诚",
        only_child="非独生",
        other_info=None,
        updated_at=None,
    )
    assert item.member_code == "G000001"
    assert item.only_child == "非独生"

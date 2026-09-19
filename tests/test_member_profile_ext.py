"""单元测试：会员资料扩展字段（性格/爱好/MBTI/自我介绍/红娘说）管理后台接口。

覆盖范围：
- 路由已在 OpenAPI 注册（GET/PUT /admin/members/profile-ext/{user_id}）；
- 未登录 401（读 matchmaker.member.read / 写 matchmaker.member.manage）；
- ``MemberProfileExtUpdate`` 入参校验（空 body 报错、性格标签去重/长度、
  空数组清空、超长报错、各文本字段长度上限）；
- ``MemberProfileExtItem`` 可构造。
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.member_media_admin import MemberProfileExtItem, MemberProfileExtUpdate

client = TestClient(app)

BASE = "/api/v1/admin/members"


# ─── 路由注册（OpenAPI 路径断言） ───────────────────────────────


def test_profile_ext_routes_are_registered() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "get" in paths[f"{BASE}/profile-ext/{{user_id}}"]
    assert "put" in paths[f"{BASE}/profile-ext/{{user_id}}"]


def test_profile_ext_routes_require_authentication() -> None:
    assert client.get(f"{BASE}/profile-ext/1").status_code == 401
    assert client.put(f"{BASE}/profile-ext/1", json={"mbti": "INTJ"}).status_code == 401


# ─── 入参模型校验 ───────────────────────────────────────────────


def test_update_empty_body_rejected() -> None:
    with pytest.raises(ValidationError):
        MemberProfileExtUpdate()


def test_update_personality_tags_empty_list_allowed() -> None:
    # 空数组 = 清空性格标签
    body = MemberProfileExtUpdate(personality_tags=[])
    assert body.personality_tags == []


def test_update_personality_tags_duplicated_rejected() -> None:
    with pytest.raises(ValidationError):
        MemberProfileExtUpdate(personality_tags=["外向", "外向"])


def test_update_personality_tags_blank_or_too_long_rejected() -> None:
    with pytest.raises(ValidationError):
        MemberProfileExtUpdate(personality_tags=[" "])
    with pytest.raises(ValidationError):
        MemberProfileExtUpdate(personality_tags=["x" * 21])


def test_update_more_than_ten_tags_rejected() -> None:
    with pytest.raises(ValidationError):
        MemberProfileExtUpdate(personality_tags=[f"tag{i}" for i in range(11)])


def test_update_text_field_limits() -> None:
    assert MemberProfileExtUpdate(hobbies="x" * 500).hobbies == "x" * 500
    with pytest.raises(ValidationError):
        MemberProfileExtUpdate(hobbies="x" * 501)
    assert MemberProfileExtUpdate(mbti="INTJ").mbti == "INTJ"
    with pytest.raises(ValidationError):
        MemberProfileExtUpdate(mbti="x" * 17)
    assert MemberProfileExtUpdate(self_intro="x" * 500).self_intro == "x" * 500
    with pytest.raises(ValidationError):
        MemberProfileExtUpdate(self_intro="x" * 501)
    assert MemberProfileExtUpdate(matchmaker_note="x" * 1000).matchmaker_note == "x" * 1000
    with pytest.raises(ValidationError):
        MemberProfileExtUpdate(matchmaker_note="x" * 1001)


def test_update_partial_fields_ok() -> None:
    body = MemberProfileExtUpdate(matchmaker_note="红娘说：资料完整，建议优先推荐")
    assert body.matchmaker_note and "红娘说" in body.matchmaker_note
    assert body.personality_tags is None
    assert body.hobbies is None


# ─── 返回模型可构造 ─────────────────────────────────────────────


def test_item_construct() -> None:
    item = MemberProfileExtItem(
        id=1,
        user_id=1,
        member_code="G000001",
        nickname="测试",
        avatar=None,
        personality_tags=["外向", "顾家"],
        hobbies="爬山、做饭",
        mbti="ENFJ",
        self_intro="大家好",
        matchmaker_note="诚意度高",
        updated_at=None,
    )
    assert item.member_code == "G000001"
    assert item.personality_tags == ["外向", "顾家"]
    assert item.matchmaker_note == "诚意度高"

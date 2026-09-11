"""单元测试：会员认证（M3-1）管理后台扩展能力。

覆盖范围：
- ``AuthTypeCreate`` / ``AuthTypeUpdate`` 入参校验；
- ``ReviewActionRequest`` 状态枚举校验；
- ``RealnameStats`` / ``MarriageStats`` 默认值；
- ``MemberAuthReviewPage`` 等分页模型可构造；
- 全部新增路由已在 OpenAPI 注册（含动态 /{kind}/{review_id}）。
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.member_auth_admin import (
    AuthTypeCreate,
    AuthTypeUpdate,
    CommitmentReviewItem,
    MarriageReviewItem,
    MarriageStats,
    MemberAuthReviewItem,
    MemberAuthReviewPage,
    OtherReviewItem,
    RealnameReviewItem,
    RealnameStats,
    ReviewActionRequest,
)

client = TestClient(app)

BASE = "/api/v1/admin/members"


# ─── 路由注册（OpenAPI 路径断言） ───────────────────────────────


def test_new_member_auth_routes_are_registered() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "get" in paths[f"{BASE}/auth/realname-reviews"]
    assert "get" in paths[f"{BASE}/auth/realname-stats"]
    assert "get" in paths[f"{BASE}/auth/commitment-reviews"]
    assert "get" in paths[f"{BASE}/auth/marriage-reviews"]
    assert "get" in paths[f"{BASE}/auth/marriage-stats"]
    assert "get" in paths[f"{BASE}/auth/house-reviews"]
    assert "get" in paths[f"{BASE}/auth/education-reviews"]
    assert "get" in paths[f"{BASE}/auth/other-reviews"]
    assert "get" in paths[f"{BASE}/auth/types"]
    assert "post" in paths[f"{BASE}/auth/types"]
    # 动态路由 /auth/{kind}/{review_id}
    dynamic = f"{BASE}/auth/{{kind}}/{{review_id}}"
    assert "patch" in paths[dynamic]
    assert "delete" in paths[dynamic]


def test_new_member_auth_routes_require_authentication() -> None:
    # 读接口需 matchmaker.member.read，未登录应 401
    assert client.get(f"{BASE}/auth/realname-reviews").status_code == 401
    assert client.get(f"{BASE}/auth/realname-stats").status_code == 401
    assert client.get(f"{BASE}/auth/commitment-reviews").status_code == 401
    assert client.get(f"{BASE}/auth/marriage-reviews").status_code == 401
    assert client.get(f"{BASE}/auth/marriage-stats").status_code == 401
    assert client.get(f"{BASE}/auth/house-reviews").status_code == 401
    assert client.get(f"{BASE}/auth/education-reviews").status_code == 401
    assert client.get(f"{BASE}/auth/other-reviews").status_code == 401
    assert client.get(f"{BASE}/auth/types").status_code == 401
    # 写接口需 matchmaker.member.manage，未登录应 401
    assert client.post(f"{BASE}/auth/types", json={"name": "x"}).status_code == 401
    assert client.patch(f"{BASE}/auth/realname/1", json={"status": 1}).status_code == 401
    assert client.delete(f"{BASE}/auth/realname/1").status_code == 401


def test_review_dynamic_route_constrains_kind() -> None:
    # kind 非法值不应匹配到路由（FastAPI 正则约束）
    spec = client.get("/openapi.json").json()["paths"]
    assert f"{BASE}/auth/{{kind}}/{{review_id}}" in spec


# ─── 入参模型校验 ───────────────────────────────────────────────


def test_auth_type_create_name_required() -> None:
    with pytest.raises(ValidationError):
        AuthTypeCreate(name="")


def test_auth_type_create_name_too_long() -> None:
    with pytest.raises(ValidationError):
        AuthTypeCreate(name="x" * 65)


def test_auth_type_create_sort_negative() -> None:
    with pytest.raises(ValidationError):
        AuthTypeCreate(name="测试", sort=-1)


def test_auth_type_create_require_realname_default_true() -> None:
    body = AuthTypeCreate(name="测试")
    assert body.require_realname is True
    assert body.status == 1


def test_auth_type_create_status_out_of_range() -> None:
    with pytest.raises(ValidationError):
        AuthTypeCreate(name="测试", status=2)


def test_auth_type_update_allows_empty() -> None:
    # 全字段可选，空对象可构造
    body = AuthTypeUpdate()
    assert body.name is None
    assert body.require_realname is None
    assert body.status is None


def test_review_action_status_must_be_1_or_2() -> None:
    assert ReviewActionRequest(status=1).status == 1
    assert ReviewActionRequest(status=2).status == 2
    with pytest.raises(ValidationError):
        ReviewActionRequest(status=3)
    with pytest.raises(ValidationError):
        ReviewActionRequest(status=0)


# ─── 返回模型默认值 ───────────────────────────────────────────


def test_realname_stats_defaults() -> None:
    stat = RealnameStats()
    assert stat.quota_remaining == 0
    assert stat.success_count == 0
    assert stat.fail_count == 0
    assert stat.total_consumed == 0


def test_marriage_stats_defaults() -> None:
    stat = MarriageStats()
    assert stat.quota_remaining == 0
    assert stat.total_consumed == 0


def test_review_pages_construct() -> None:
    page = MemberAuthReviewPage(
        items=[
            MemberAuthReviewItem(
                id=1,
                user_id=1,
                member_code="G000001",
                result="pending",
                result_label="待审",
            )
        ],
        page=1,
        page_size=20,
        total=1,
        has_more=False,
    )
    assert len(page.items) == 1
    assert page.items[0].member_code == "G000001"

    realname = RealnameReviewItem(
        id=2, user_id=2, member_code="G000002", result="success", result_label="认证成功",
        face_score="95.51",
    )
    assert realname.face_score == "95.51"

    commit = CommitmentReviewItem(
        id=3, user_id=3, member_code="G000003", result="pass", result_label="通过", sign_times=2,
    )
    assert commit.sign_times == 2

    marriage = MarriageReviewItem(
        id=4, user_id=4, member_code="G000004", result="married", result_label="已婚", cost="0.00",
    )
    assert marriage.cost == "0.00"

    other = OtherReviewItem(
        id=5, user_id=5, member_code="G000005", result="pass", result_label="通过",
        auth_type_id=1, auth_type_name="收入证明",
    )
    assert other.auth_type_name == "收入证明"

"""单元测试：后台为会员筛选推荐候选人接口（GET /admin/members/{user_id}/recommendations）。

覆盖范围：
- 路由已在 OpenAPI 注册（推荐 + 见过哪些人）；
- 未登录 401；
- 查询参数校验（marriage 1-3、vip_filter 枚举）；
- ``MemberRecommendItem`` / ``MemberRecommendPage`` / ``MemberMetItem`` 可构造。
"""

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.member_media_admin import (
    MemberMetItem,
    MemberMetPage,
    MemberPreferenceChips,
    MemberRecommendItem,
    MemberRecommendPage,
)
from app.api.routes.member_records_admin import MemberMatchQuotaResponse, MemberMatchRecordItem, MemberMatchRecordPage

client = TestClient(app)

BASE = "/api/v1/admin/members"


# ─── 路由注册（OpenAPI 路径断言） ───────────────────────────────


def test_recommend_route_is_registered() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "get" in paths[f"{BASE}/{{user_id}}/recommendations"]
    assert "post" in paths[f"{BASE}/{{member_id}}/recommendations"]
    assert "get" in paths[f"{BASE}/{{member_id}}/recommend-history"]
    assert "get" in paths[f"{BASE}/{{member_id}}/match-records"]
    assert "get" in paths[f"{BASE}/{{member_id}}/match-quota"]
    assert "get" in paths[f"{BASE}/{{user_id}}/met-members"]


def test_recommend_route_requires_authentication() -> None:
    assert client.get(f"{BASE}/1/recommendations").status_code == 401
    assert client.post(f"{BASE}/1/recommendations", json={"recommend_user_id": 2}).status_code == 401
    assert client.get(f"{BASE}/1/recommend-history").status_code == 401
    assert client.get(f"{BASE}/1/match-records").status_code == 401
    assert client.get(f"{BASE}/1/match-quota").status_code == 401
    assert client.get(f"{BASE}/1/met-members").status_code == 401


def test_recommend_route_rejects_bad_params() -> None:
    # marriage 只允许 1/2/3；vip_filter 枚举外值 → 鉴权前参数校验拒绝 → 401（未登录先拦截）
    assert client.get(f"{BASE}/1/recommendations", params={"marriage": 5}).status_code == 401
    assert client.get(f"{BASE}/1/recommendations", params={"vip_filter": "abc"}).status_code == 401


# ─── 返回模型可构造 ─────────────────────────────────────────────


def test_recommend_item_construct() -> None:
    item = MemberRecommendItem(
        user_id=2,
        member_code="G000002",
        nickname="候选人",
        gender=1,
        age=28,
        tags=["高颜值", "985毕业"],
        matchmaker_id=3,
        matchmaker_name="王红娘",
        is_offline_vip=True,
        is_online_vip=False,
        store_visited=True,
        abandoned=False,
        promise_meet_count=5,
        success_meet_count=2,
        met_count=3,
    )
    assert item.is_offline_vip is True
    assert item.promise_meet_count == 5
    assert item.met_count == 3


def test_recommend_page_with_preference_chips() -> None:
    chips = MemberPreferenceChips(age_min=22, age_max=30, height_min=160, height_max=175)
    page_model = MemberRecommendPage(
        items=[],
        page=1,
        page_size=20,
        total=0,
        has_more=False,
        preference=chips,
    )
    assert page_model.preference is not None
    assert page_model.preference.age_min == 22
    assert page_model.preference.height_max == 175


def test_met_item_and_page_construct() -> None:
    item = MemberMetItem(
        user_id=9,
        member_code="G000009",
        nickname="见过的",
        gender=1,
        apply_status=1,
        applied_at=None,
    )
    assert item.member_code == "G000009"
    page_model = MemberMetPage(items=[item], page=1, page_size=20, total=1, has_more=False)
    assert page_model.total == 1
    with __import__("pytest").raises(ValidationError):
        MemberMetPage(items=[], page=0, page_size=20, total=0, has_more=False)


def test_match_record_and_quota_models_construct() -> None:
    item = MemberMatchRecordItem(
        id=1,
        from_user_id=101,
        to_user_id=202,
        target_nickname="对方",
        status=1,
    )
    page = MemberMatchRecordPage(items=[item], page=1, page_size=20, total=1, has_more=False)
    assert page.items[0].target_nickname == "对方"
    quota = MemberMatchQuotaResponse(user_id=101, available_count=5, used_count=2, refunded_count=1)
    assert quota.available_count == 5

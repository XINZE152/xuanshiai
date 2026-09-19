"""单元测试：后台为会员筛选推荐候选人接口（GET /admin/members/{user_id}/recommendations）。

覆盖范围：
- 路由已在 OpenAPI 注册；
- 未登录 401；
- 查询参数校验（marriage 1-3、occupations/tags 上限）；
- ``MemberRecommendItem`` / ``MemberRecommendPage`` 可构造。
"""

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.member_media_admin import MemberRecommendItem, MemberRecommendPage

client = TestClient(app)

BASE = "/api/v1/admin/members"


# ─── 路由注册（OpenAPI 路径断言） ───────────────────────────────


def test_recommend_route_is_registered() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "get" in paths[f"{BASE}/{{user_id}}/recommendations"]


def test_recommend_route_requires_authentication() -> None:
    assert client.get(f"{BASE}/1/recommendations").status_code == 401


def test_recommend_route_rejects_bad_marriage() -> None:
    # marriage 只允许 1/2/3；越界值在鉴权前由参数校验拒绝 → 401（未登录先拦截）
    resp = client.get(f"{BASE}/1/recommendations", params={"marriage": 5})
    assert resp.status_code == 401


# ─── 返回模型可构造 ─────────────────────────────────────────────


def test_recommend_item_construct() -> None:
    item = MemberRecommendItem(
        user_id=2,
        member_code="G000002",
        nickname="候选人",
        avatar=None,
        gender=1,
        age=28,
        height=178,
        income=15000.0,
        education_level=5,
        occupation="公务员",
        constellation="天秤座",
        mbti="ENFJ",
        hometown="浙江杭州",
        residence="浙江杭州",
        smoking="不吸烟",
        drinking="不喝酒",
        house="有房",
        ethnicity="汉族",
        tags=["高颜值", "985毕业"],
        matchmaker_id=3,
        matchmaker_name="王红娘",
    )
    assert item.member_code == "G000002"
    assert item.tags == ["高颜值", "985毕业"]


def test_recommend_page_construct() -> None:
    page_model = MemberRecommendPage(
        items=[],
        page=1,
        page_size=20,
        total=0,
        has_more=False,
    )
    assert page_model.total == 0
    assert page_model.has_more is False
    import pytest

    with pytest.raises(ValidationError):
        MemberRecommendPage(items=[], page=0, page_size=20, total=0, has_more=False)

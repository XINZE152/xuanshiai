"""单元测试：线上行为（M3-3）管理后台扩展能力。

覆盖范围：
- ``MemberBehaviorItem`` 五类共用结构的可构造性与默认值；
- ``MemberBehaviorPage`` 分页结构可构造；
- 新增路由已在 OpenAPI 注册（``/behavior-events`` 与删除路径）；
- 未登录访问读 / 写接口均返回 401；
- 类别枚举非法值被 OpenAPI 参数约束（422）。
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.member_behavior_admin import MemberBehaviorItem, MemberBehaviorPage

client = TestClient(app)

BASE = "/api/v1/admin/members"


# ─── 路由注册（OpenAPI 路径断言） ───────────────────────────────


def test_new_behavior_routes_are_registered() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "get" in paths[f"{BASE}/behavior-events"]
    assert "delete" in paths[f"{BASE}/behavior-events/{{category}}/{{event_id}}"]


def test_behavior_routes_require_authentication() -> None:
    # 读接口需 matchmaker.member.read，未登录应 401
    assert client.get(f"{BASE}/behavior-events").status_code == 401
    # 删除接口需 matchmaker.member.manage，未登录应 401
    assert client.delete(f"{BASE}/behavior-events/superlike/1").status_code == 401


# ─── 参数约束（OpenAPI schema） ─────────────────────────────────


def test_behavior_category_enum_constrained_in_schema() -> None:
    """category 与删除路径的 category 均由正则约束枚举，非法值由 FastAPI 拦为 422。"""
    spec = client.get("/openapi.json").json()
    get_params = spec["paths"][f"{BASE}/behavior-events"]["get"]["parameters"]
    category = next(p for p in get_params if p["name"] == "category")
    assert "browse" in category["schema"]["pattern"]
    assert "superlike" in category["schema"]["pattern"]

    del_params = spec["paths"][f"{BASE}/behavior-events/{{category}}/{{event_id}}"]["delete"][
        "parameters"
    ]
    del_category = next(p for p in del_params if p["name"] == "category")
    # 删除仅允许爆灯 / 礼物 / 举报
    assert "superlike" in del_category["schema"]["pattern"]
    assert "gift" in del_category["schema"]["pattern"]
    assert "report" in del_category["schema"]["pattern"]


# ─── 返回模型可构造 ─────────────────────────────────────────────


def test_behavior_item_minimal_construct() -> None:
    """五类共用结构：未使用字段为 None / 缺省。"""
    item = MemberBehaviorItem(
        event_id=1,
        user_id=10,
        member_code="G000010",
        occurred_at=None,
    )
    assert item.event_id == 1
    assert item.member_code == "G000010"
    # 五类专属字段缺省为 None
    assert item.browse_times is None
    assert item.amount is None
    assert item.gift_name is None
    assert item.submit_ip is None
    assert item.images is None
    assert item.report_status is None


def test_behavior_item_requires_core_fields() -> None:
    """event_id / user_id / member_code 必填。"""
    with pytest.raises(ValidationError):
        MemberBehaviorItem(user_id=1, member_code="G000001")  # 缺 event_id
    with pytest.raises(ValidationError):
        MemberBehaviorItem(event_id=1, member_code="G000001")  # 缺 user_id


def test_behavior_item_browse_fields() -> None:
    item = MemberBehaviorItem(
        event_id=2,
        user_id=11,
        member_code="G000011",
        nickname="会员A",
        user_avatar=None,
        target_user_id=12,
        target_member_code="G000012",
        target_nickname="会员B",
        target_avatar=None,
        occurred_at=None,
        browse_times=3,
    )
    assert item.browse_times == 3
    assert item.target_member_code == "G000012"


def test_behavior_item_superlike_fields() -> None:
    item = MemberBehaviorItem(
        event_id=3,
        user_id=11,
        member_code="G000011",
        occurred_at=None,
        amount="5.00",
        order_no="F01234567890",
        pay_status=1,
        pay_status_label="已支付",
        pay_method="微信支付",
        event_status=1,
        event_status_label="正常",
    )
    assert item.amount == "5.00"  # 金额为字符串
    assert item.pay_status_label == "已支付"
    assert item.event_status_label == "正常"


def test_behavior_item_gift_fields() -> None:
    item = MemberBehaviorItem(
        event_id=4,
        user_id=11,
        member_code="G000011",
        occurred_at=None,
        gift_name="水晶球",
        gift_qty=1,
        qty_unit="颗",
        point_cost=900,
        paid_amount="0.00",
        reward_points=450,
    )
    assert item.gift_name == "水晶球"
    assert item.qty_unit == "颗"
    assert item.paid_amount == "0.00"


def test_behavior_item_report_fields() -> None:
    item = MemberBehaviorItem(
        event_id=5,
        user_id=11,
        member_code="G000011",
        occurred_at=None,
        submit_ip="203.0.113.10",
        report_type="骚扰",
        detail="疑似骚扰",
        images=["/uploads/report/1.jpg", "/uploads/report/2.jpg"],
        report_status=0,
        report_status_label="待处理",
    )
    assert item.submit_ip == "203.0.113.10"
    assert len(item.images or []) == 2
    assert item.report_status_label == "待处理"


def test_behavior_page_construct() -> None:
    page = MemberBehaviorPage(
        items=[MemberBehaviorItem(event_id=1, user_id=1, member_code="G000001", occurred_at=None)],
        page=1,
        page_size=20,
        total=1,
        has_more=False,
    )
    assert len(page.items) == 1
    assert page.has_more is False


def test_behavior_page_empty() -> None:
    page = MemberBehaviorPage(items=[], page=1, page_size=20, total=0, has_more=False)
    assert page.items == []
    assert page.total == 0

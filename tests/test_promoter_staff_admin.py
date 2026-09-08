"""单元测试：总店红娘后台 → 推广红娘管理 API。

测试策略（与 matchmaker_staff_admin / commission_level_admin 一致）：
- OpenAPI 路径注册校验；
- 未登录 401；
- Pydantic schema 入参约束（channel 长度 / 枚举校验）。
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.promoter_staff_admin import PromoterStaffCreate, PromoterStaffUpdate, PromoterStatusUpdate

client = TestClient(app)

BASE = "/api/v1/admin/promoters"


def test_promoter_routes_are_registered() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "get" in paths[BASE]
    assert "post" in paths[BASE]
    assert "get" in paths[f"{BASE}/user-candidates"]
    assert "get" in paths[f"{BASE}/{{user_id}}"]
    assert "put" in paths[f"{BASE}/{{user_id}}"]
    assert "patch" in paths[f"{BASE}/{{user_id}}/status"]


def test_promoter_endpoints_require_authentication() -> None:
    assert client.get(BASE).status_code == 401
    assert client.get(f"{BASE}/user-candidates", params={"keyword": "张三"}).status_code == 401
    assert client.post(BASE, json={"lookup": "张三", "channel": "微信"}).status_code == 401
    assert client.get(f"{BASE}/1").status_code == 401
    assert client.put(f"{BASE}/1", json={"channel": "抖音"}).status_code == 401
    assert client.patch(f"{BASE}/1/status", json={"status": 2, "reason": "个人原因"}).status_code == 401


def test_query_param_validation_via_openapi() -> None:
    spec = client.get("/openapi.json").json()
    params = spec["paths"][BASE]["get"]["parameters"]
    names = {p["name"] for p in params}
    for required in ("page", "page_size", "keyword", "status"):
        assert required in names, f"missing query param {required}"


def test_create_channel_max_length() -> None:
    with pytest.raises(ValidationError):
        PromoterStaffCreate(lookup="张三", channel="x" * 65)


def test_create_intro_max_length() -> None:
    with pytest.raises(ValidationError):
        PromoterStaffCreate(lookup="张三", intro="x" * 501)


def test_status_update_requires_status() -> None:
    with pytest.raises(ValidationError):
        PromoterStatusUpdate()  # type: ignore[call-arg]


def test_update_status_enum_only_1_or_2() -> None:
    with pytest.raises(ValidationError):
        PromoterStaffUpdate(status=3)  # type: ignore[arg-type]


def test_update_channel_length_limit() -> None:
    with pytest.raises(ValidationError):
        PromoterStaffUpdate(channel="x" * 65)

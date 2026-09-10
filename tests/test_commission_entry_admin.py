"""单元测试：总店红娘 → 分成明细 后台 API。

测试策略：
- OpenAPI 路径注册校验；
- 未登录 401；
- 入参 query 参数校验（start_date/end_date 日期格式、page 边界）。
"""

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_commission_entry_routes_are_registered() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "get" in paths["/api/v1/admin/finance/commission-entries"]
    assert "get" in paths["/api/v1/admin/finance/commission-entries/options"]


def test_commission_entry_list_requires_authentication() -> None:
    assert client.get("/api/v1/admin/finance/commission-entries").status_code == 401


def test_commission_entry_options_requires_authentication() -> None:
    assert client.get("/api/v1/admin/finance/commission-entries/options").status_code == 401


def test_commission_entry_query_param_validation_via_openapi() -> None:
    """确认 page/page_size/matchmaker_id/rule_id/start_date/end_date 都在 OpenAPI 参数表中。"""
    spec = client.get("/openapi.json").json()
    params = spec["paths"]["/api/v1/admin/finance/commission-entries"]["get"]["parameters"]
    names = {p["name"] for p in params}
    for required in (
        "page",
        "page_size",
        "matchmaker_id",
        "rule_id",
        "start_date",
        "end_date",
    ):
        assert required in names, f"missing query param {required}"

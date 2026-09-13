"""单元测试：M5 分店管理（分站/门店 / 分店红娘 / 分店报表 / 分店分成明细）。

覆盖范围：
- 新增路由注册：门店 CRUD、门店报表 summary/monthly、分店分成明细 + 选项 + 统计卡
- 未登录访问上述 admin 端点均 401
- 红娘列表新增 ``in_store`` 过滤器与 ``menu_permission_count`` 字段
- schema 层：``StoreAdminCreate`` 编码/排序校验、分站模式枚举、报表金额字符串序列化
- 迁移：``_ensure_m5_columns`` 已定义并注册进主流程
- 配置域：``tools_branch`` 含 ``mode``
"""

import inspect
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.finance import StoreCommissionSummary
from app.schemas.matchmaker_staff_admin import MatchmakerStaffItem
from app.schemas.organization_admin import (
    StoreAdminCreate,
    StoreReportMonthlyRow,
    StoreReportSummary,
    StoreSubsiteModeUpdate,
)
from app.services import organization_admin as store_service
from app.services.admin_config import DEFAULT_CONFIGS
from app.services.finance import _STORE_BENEFICIARY_TYPE

client = TestClient(app)

STORE_BASE = "/api/v1/admin/matchmaker/stores"
FINANCE_BASE = "/api/v1/admin/finance"


# ─── 路由注册 ────────────────────────────────────────────────────────


def test_store_routes_registered() -> None:
    spec = client.get("/openapi.json").json()
    root = spec["paths"][STORE_BASE]
    assert "get" in root and "post" in root
    detail = spec["paths"][f"{STORE_BASE}/{{store_id}}"]
    assert "get" in detail and "patch" in detail and "delete" in detail
    assert "get" in spec["paths"][f"{STORE_BASE}/{{store_id}}/report/summary"]
    assert "get" in spec["paths"][f"{STORE_BASE}/{{store_id}}/report/monthly"]


def test_store_commission_routes_registered() -> None:
    spec = client.get("/openapi.json").json()
    assert "get" in spec["paths"][f"{FINANCE_BASE}/store-commission-entries"]
    assert "get" in spec["paths"][f"{FINANCE_BASE}/store-commission-entries/options"]
    assert "get" in spec["paths"][f"{FINANCE_BASE}/store-commission-entries/export"]
    assert "get" in spec["paths"][f"{FINANCE_BASE}/store-commission-summary"]


def test_new_endpoints_require_authentication() -> None:
    assert client.get(STORE_BASE).status_code == 401
    assert client.post(STORE_BASE, json={}).status_code == 401
    assert client.delete(f"{STORE_BASE}/1").status_code == 401
    assert client.get(f"{STORE_BASE}/1/report/summary").status_code == 401
    assert client.get(f"{STORE_BASE}/1/report/monthly").status_code == 401
    assert client.get(f"{FINANCE_BASE}/store-commission-entries").status_code == 401
    assert client.get(f"{FINANCE_BASE}/store-commission-entries/options").status_code == 401
    assert client.get(f"{FINANCE_BASE}/store-commission-entries/export").status_code == 401
    assert client.get(f"{FINANCE_BASE}/store-commission-summary").status_code == 401


# ─── schema 校验 ─────────────────────────────────────────────────────


def test_store_create_accepts_new_columns() -> None:
    body = StoreAdminCreate(
        code="nanjing-01",
        name="南京旗舰店",
        display_name="南京分站",
        region_code="320100",
        link_url="https://example.com/nanjing",
        sort_order=9,
        qr_code="/uploads/qr/nanjing.png",
        auto_redirect=True,
    )
    assert body.sort_order == 9
    assert body.auto_redirect is True


@pytest.mark.parametrize("code", ["南京", "a", "bad code", "store@1"])
def test_store_create_rejects_invalid_code(code: str) -> None:
    with pytest.raises(ValidationError):
        StoreAdminCreate(code=code, name="x")


def test_store_create_rejects_negative_sort() -> None:
    with pytest.raises(ValidationError):
        StoreAdminCreate(code="ok-1", name="x", sort_order=-1)


def test_subsite_mode_only_accepts_two_values() -> None:
    assert StoreSubsiteModeUpdate(mode="all").mode == "all"
    assert StoreSubsiteModeUpdate(mode="region").mode == "region"
    with pytest.raises(ValidationError):
        StoreSubsiteModeUpdate(mode="global")


def test_report_summary_serializes_money_as_str() -> None:
    summary = StoreReportSummary(
        store_id=1,
        store_name="南京分站",
        lead_count=3,
        member_count=10,
        online_match_count=2,
        online_vip_count=4,
        offline_vip_count=1,
        meeting_arranged_count=5,
        online_commission=Decimal("1234.50"),
        offline_performance=Decimal("88.00"),
    )
    payload = summary.model_dump(mode="json")
    assert payload["online_commission"] == "1234.50"
    assert payload["offline_performance"] == "88.00"

def test_report_monthly_row_money_as_str() -> None:
    row = StoreReportMonthlyRow(
        month="2026-08",
        new_male_members=1,
        new_female_members=2,
        new_leads=3,
        new_online_vip=4,
        new_match_requests=5,
        new_offline_meetings=6,
        new_offline_vip=7,
        online_commission="10.00",
        offline_performance="20.00",
    )
    assert row.model_dump(mode="json")["online_commission"] == "10.00"


def test_commission_summary_serializes_money_as_str() -> None:
    summary = StoreCommissionSummary(
        total_amount=Decimal("100.00"),
        current_month_amount=Decimal("10.00"),
        previous_month_amount=Decimal("20.00"),
        pending_amount=Decimal("5.00"),
    )
    payload = summary.model_dump(mode="json")
    assert payload == {
        "total_amount": "100.00",
        "current_month_amount": "10.00",
        "previous_month_amount": "20.00",
        "pending_amount": "5.00",
    }


# ─── 红娘列表（分店维度） ────────────────────────────────────────────


def test_list_staff_supports_in_store_filter() -> None:
    from app.services.matchmaker_staff_admin import list_staff

    assert "in_store" in inspect.signature(list_staff).parameters


def test_matchmaker_item_has_menu_permission_count() -> None:
    assert "menu_permission_count" in MatchmakerStaffItem.model_fields


# ─── 服务层辅助函数 ─────────────────────────────────────────────────


def test_store_user_scope_is_parameterized() -> None:
    sql = store_service._STORE_USER_SCOPE.format(col="u.id")
    assert ":store_id" in sql and "u.id" in sql and "resource_assignment" in sql


def test_recent_months_order_and_length() -> None:
    months = store_service._recent_months(6)
    assert len(months) == 6
    assert months == sorted(months)
    assert all(len(m) == 7 and m[4] == "-" for m in months)


def test_store_beneficiary_type_is_store() -> None:
    assert _STORE_BENEFICIARY_TYPE == "store"
    assert "store" in inspect.getsource(store_service.store_report_monthly)


# ─── 迁移与配置域 ────────────────────────────────────────────────────


def test_m5_columns_migration_registered() -> None:
    from database_setup_marriage import DatabaseManager

    source = inspect.getsource(DatabaseManager)
    assert "def _ensure_m5_columns" in source
    assert "self._ensure_m5_columns(cursor)" in source
    for column in ("link_url", "sort_order", "qr_code"):
        assert column in source


def test_tools_branch_config_has_mode() -> None:
    _, _, payload, _ = DEFAULT_CONFIGS["tools_branch"]
    assert payload.get("mode") == "all"

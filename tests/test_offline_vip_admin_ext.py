"""线下VIP会员服务（M3-6）契约与路由注册测试。

不连库：只做 Pydantic 校验、模型构造与 OpenAPI 路径断言。
"""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.offline_vip_admin import (
    OfflineVipCreate,
    OfflineVipItem,
    OfflineVipMeetLogItem,
    OfflineVipMeetLogPage,
    OfflineVipOptions,
    OfflineVipPage,
    OfflineVipStatistics,
    OfflineVipUpdate,
)

client = TestClient(app)

BASE = "/api/v1/admin/offline-vips"


# ─── 路由注册 ───────────────────────────────────────────────────


def test_all_offline_vip_routes_registered() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert set(paths[f"{BASE}"].keys()) == {"get", "post"}
    assert "get" in paths[f"{BASE}/statistics"]
    assert "get" in paths[f"{BASE}/options"]
    assert set(paths[f"{BASE}/{{vip_id}}"].keys()) == {"get", "put"}
    assert "get" in paths[f"{BASE}/{{vip_id}}/meet-logs"]


def test_static_paths_declared_before_dynamic() -> None:
    """确保 /statistics、/options 不会被 /{vip_id} 吃掉（按 OpenAPI 注册顺序断言）。"""
    keys = [path for path in client.get("/openapi.json").json()["paths"] if path.startswith(BASE)]
    assert keys.index(f"{BASE}/statistics") < keys.index(f"{BASE}/{{vip_id}}")
    assert keys.index(f"{BASE}/options") < keys.index(f"{BASE}/{{vip_id}}")


def test_routes_require_authentication() -> None:
    assert client.get(BASE).status_code == 401
    assert client.get(f"{BASE}/statistics").status_code == 401
    assert client.get(f"{BASE}/options").status_code == 401
    assert client.post(BASE, json={}).status_code == 401
    assert client.put(f"{BASE}/1", json={}).status_code == 401
    assert client.get(f"{BASE}/1/meet-logs").status_code == 401


# ─── 入参校验 ───────────────────────────────────────────────────


def test_create_rejects_invalid_lookup_by() -> None:
    with pytest.raises(ValidationError):
        OfflineVipCreate(lookup="张三", lookup_by="email")  # type: ignore[arg-type]


def test_create_rejects_negative_amount_and_counts() -> None:
    with pytest.raises(ValidationError):
        OfflineVipCreate(user_id=1, contract_amount=Decimal("-1"))
    with pytest.raises(ValidationError):
        OfflineVipCreate(user_id=1, promise_meet_count=-1)


def test_create_defaults() -> None:
    body = OfflineVipCreate(user_id=1)
    assert body.lookup_by == "nickname"
    assert body.contract_amount == Decimal("0.00")
    assert body.promise_meet_count == 0
    assert body.success_meet_count == 0
    assert body.attach_urls == []


def test_create_rejects_overlong_remark() -> None:
    with pytest.raises(ValidationError):
        OfflineVipCreate(user_id=1, remark="x" * 501)


def test_update_all_optional() -> None:
    body = OfflineVipUpdate()
    assert body.model_dump(exclude_unset=True) == {}


def test_update_rejects_invalid_progress() -> None:
    with pytest.raises(ValidationError):
        OfflineVipUpdate(progress="unknown")  # type: ignore[arg-type]


def test_update_accepts_progress_enum() -> None:
    body = OfflineVipUpdate(progress="met_parents", success_meet_count=3, meet_change_remark="人工修正")
    assert body.progress == "met_parents"
    assert body.success_meet_count == 3
    assert body.meet_change_remark == "人工修正"


# ─── 模型构造 ───────────────────────────────────────────────────


def test_item_constructs_with_minimal_fields() -> None:
    item = OfflineVipItem(
        id=1,
        user_id=396140,
        member_code="G396140",
        progress="matching",
        progress_label="匹配推荐中",
    )
    assert item.contract_amount == Decimal("0.00")
    assert item.contract_status == "none"
    assert item.attach_urls == []


def test_page_and_statistics_construct() -> None:
    page = OfflineVipPage(items=[], page=1, page_size=20, total=0, has_more=False)
    assert page.total == 0
    stats = OfflineVipStatistics()
    assert stats.refunded_count == 0
    assert stats.store_count == 0


def test_meet_log_models_construct() -> None:
    log = OfflineVipMeetLogItem(id=1, vip_id=2, before_count=1, after_count=3)
    assert log.changed_by_name is None
    page = OfflineVipMeetLogPage(items=[log], page=1, page_size=20, total=1, has_more=False)
    assert page.items[0].after_count == 3


def test_options_defaults() -> None:
    options = OfflineVipOptions()
    assert options.sales_matchmakers == []
    assert options.packages == []


def test_create_accepts_dates_and_decimal_amount() -> None:
    body = OfflineVipCreate(
        user_id=9,
        sign_date=date(2026, 9, 1),
        service_start=date(2026, 9, 1),
        service_end=date(2027, 9, 1),
        contract_amount=Decimal("19800.00"),
        package_name="尊享服务套餐",
        attach_urls=["/storage/contract/a.webp"],
    )
    assert body.sign_date == date(2026, 9, 1)
    assert body.contract_amount == Decimal("19800.00")
    assert body.attach_urls == ["/storage/contract/a.webp"]

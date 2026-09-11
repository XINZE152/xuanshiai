"""单元测试：推广红娘管理后台扩展能力（M2）。

覆盖范围：
- `PromoterStaffCreate.phone` 可选（修复 create_promoter 访问 body.phone 的 500 bug）；
- `PromoterStaffUpdate.visible` / `PromoterTeamUpdate` 等新入参模型；
- `PromoterStaffItem` 新增列表字段；
- 统计 / 团队 / 分成明细 / options 等新返回模型可构造；
- 8 条新路由已在 OpenAPI 注册。
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.promoter_staff_admin import (
    PromoterCommissionEntryItem,
    PromoterCommissionEntryOptions,
    PromoterCommissionEntryPage,
    PromoterCommissionEventOption,
    PromoterCommissionPromoterOption,
    PromoterDeleteResponse,
    PromoterPlatformTokenResponse,
    PromoterPosterResponse,
    PromoterStaffCreate,
    PromoterStaffItem,
    PromoterStaffUpdate,
    PromoterStatistics,
    PromoterTeamItem,
    PromoterTeamUpdate,
)

client = TestClient(app)

BASE = "/api/v1/admin/promoters"


# ─── 路由注册 ───────────────────────────────────────────────────


def test_new_promoter_routes_are_registered() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "get" in paths[f"{BASE}/statistics"]
    assert "get" in paths[f"{BASE}/teams"]
    assert "get" in paths[f"{BASE}/commission-entries"]
    assert "get" in paths[f"{BASE}/commission-entries/options"]
    assert "put" in paths[f"{BASE}/{{user_id}}/team"]
    assert "post" in paths[f"{BASE}/{{user_id}}/poster"]
    assert "post" in paths[f"{BASE}/{{user_id}}/platform-token"]
    assert "delete" in paths[f"{BASE}/{{user_id}}"]


def test_new_promoter_routes_require_authentication() -> None:
    assert client.get(f"{BASE}/statistics").status_code == 401
    assert client.get(f"{BASE}/teams").status_code == 401
    assert client.get(f"{BASE}/commission-entries").status_code == 401
    assert client.get(f"{BASE}/commission-entries/options").status_code == 401
    assert client.put(f"{BASE}/1/team", json={"team_id": None}).status_code == 401
    assert client.post(f"{BASE}/1/poster").status_code == 401
    assert client.post(f"{BASE}/1/platform-token").status_code == 401
    assert client.delete(f"{BASE}/1").status_code == 401


def test_list_query_params_extended() -> None:
    spec = client.get("/openapi.json").json()
    names = {p["name"] for p in spec["paths"][BASE]["get"]["parameters"]}
    for required in (
        "page",
        "page_size",
        "keyword",
        "status",
        "commission_level_id",
        "team_id",
        "visible",
        "sort",
    ):
        assert required in names, f"missing query param {required}"


# ─── 入参模型 ───────────────────────────────────────────────────


def test_create_accepts_optional_phone() -> None:
    """修复：create_promoter 访问 body.phone 时该字段此前不存在。"""
    assert PromoterStaffCreate(lookup="张三").phone is None
    assert PromoterStaffCreate(lookup="张三", phone="13800000000").phone == "13800000000"


def test_create_phone_max_length() -> None:
    with pytest.raises(ValidationError):
        PromoterStaffCreate(lookup="张三", phone="1" * 21)


def test_update_supports_visible() -> None:
    assert PromoterStaffUpdate().visible is None
    assert PromoterStaffUpdate(visible=False).visible is False


def test_team_update_allows_null_team_id() -> None:
    assert PromoterTeamUpdate().team_id is None
    assert PromoterTeamUpdate(team_id=3, reason="调整").team_id == 3


def test_team_update_team_id_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        PromoterTeamUpdate(team_id=0)


def test_team_update_reason_max_length() -> None:
    with pytest.raises(ValidationError):
        PromoterTeamUpdate(team_id=1, reason="x" * 256)


# ─── 返回模型 ───────────────────────────────────────────────────


def _sample_item(**overrides: object) -> PromoterStaffItem:
    payload: dict[str, object] = {
        "id": 12,
        "user_id": 12,
        "display_name": "李推广",
        "member_count": 3,
        "status": 1,
        "status_label": "在职",
    }
    payload.update(overrides)
    return PromoterStaffItem(**payload)  # type: ignore[arg-type]


def test_staff_item_new_fields_and_defaults() -> None:
    item = _sample_item()
    assert item.account is None
    assert item.matchmaker_type_label == "—"
    assert item.commission_level_name is None
    assert item.team_id is None
    assert item.team_name is None
    assert item.member_month == 0
    assert item.lead_total == 0
    assert item.lead_month == 0
    assert item.order_amount == "0.00"
    assert item.visible is True


def test_staff_item_carries_extended_values() -> None:
    item = _sample_item(
        account="tuiguang01",
        matchmaker_type="full_time",
        matchmaker_type_label="全职",
        commission_level_id=2,
        commission_level_name="推广大师",
        team_id=7,
        team_name="华中战队",
        member_month=5,
        lead_total=18,
        lead_month=4,
        order_amount="1234.56",
        visible=False,
    )
    assert item.account == "tuiguang01"
    assert item.matchmaker_type == "full_time"
    assert item.team_name == "华中战队"
    assert item.order_amount == "1234.56"
    assert item.visible is False


def test_statistics_defaults() -> None:
    stat = PromoterStatistics()
    for field in (
        "part_time_count",
        "full_time_count",
        "member_total",
        "member_month",
        "member_last_month",
        "lead_total",
        "lead_month",
        "lead_last_month",
    ):
        assert getattr(stat, field) == 0


def test_team_item_constructs() -> None:
    team = PromoterTeamItem(id=1, name="华南战队", owner_user_id=9, owner_name="王队长", status=1)
    assert team.name == "华南战队"
    assert team.owner_name == "王队长"


def test_commission_entry_item_defaults() -> None:
    entry = PromoterCommissionEntryItem(id=5, promoter_id=12)
    assert entry.base_amount == "0.00"
    assert entry.amount == "0.00"
    assert entry.status == "PENDING"


def test_commission_entry_page_and_options() -> None:
    page = PromoterCommissionEntryPage(
        items=[PromoterCommissionEntryItem(id=1, promoter_id=2)],
        page=1,
        page_size=20,
        total=1,
        has_more=False,
    )
    assert len(page.items) == 1
    options = PromoterCommissionEntryOptions(
        promoters=[PromoterCommissionPromoterOption(id=2, name="李推广", avatar=None)],
        events=[PromoterCommissionEventOption(id=8, name="注册奖励")],
    )
    assert options.promoters[0].name == "李推广"
    assert options.events[0].name == "注册奖励"


def test_utility_models_construct() -> None:
    assert PromoterPosterResponse(promoter_id=1, url="/x.png", qr_content="promoter:1").qr_content == "promoter:1"
    token = PromoterPlatformTokenResponse(
        promoter_id=1,
        access_token="a",
        refresh_token="r",
        expires_in=3600,
        jump_url="/matchmaker/workbench",
    )
    assert token.token_type == "bearer"
    assert PromoterDeleteResponse(id=1, deleted=True).deleted is True

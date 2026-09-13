"""单元测试：M6 合伙红娘（功能配置 / 分成配置 / 合伙人管理 / 团队关系 / 分成明细）。

覆盖范围：
- 新增路由注册：合伙人 CRUD + 统计 + 候选用户 + 团队下拉 + 分成明细（含录入）
- 团队关系：列表 / 人工绑定 / 移出
- 合伙分成配置：3 固定级别列表 / 详情 / 编辑
- 未登录访问上述 admin 端点均 401
- schema 层：添加合伙人必填校验、级别枚举、金额字符串序列化、录入金额必须 >0
- 服务层：级别展示文案、金额序列化、分成事件描述拼接、JSON bonus_items 容错解析
- 迁移：``_ensure_m6_columns`` 已定义并注册进主流程
- 配置域：``tools_love_partner`` 含 ``share_bonus``
"""

import inspect
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.partner_admin import (
    PartnerCommissionEntryCreate,
    PartnerCommissionEntryItem,
    PartnerRelationBind,
    PartnerStaffCreate,
    PartnerStaffItem,
    PartnerStaffUpdate,
)
from app.schemas.partner_level_admin import (
    PartnerBonusItem,
    PartnerLevelItem,
    PartnerLevelUpdate,
)
from app.services import partner_admin as partner_service
from app.services import partner_level_admin as level_service
from app.services.admin_config import DEFAULT_CONFIGS

client = TestClient(app)

PARTNER_BASE = "/api/v1/admin/partners"
RELATION_BASE = "/api/v1/admin/partner-relations"
LEVEL_BASE = "/api/v1/admin/partner-levels"


# ─── 路由注册 ────────────────────────────────────────────────────────


def test_partner_routes_registered() -> None:
    spec = client.get("/openapi.json").json()
    paths = spec["paths"]
    root = paths[PARTNER_BASE]
    assert "get" in root and "post" in root
    assert "get" in paths[f"{PARTNER_BASE}/statistics"]
    assert "get" in paths[f"{PARTNER_BASE}/user-candidates"]
    assert "get" in paths[f"{PARTNER_BASE}/team-options"]
    detail = paths[f"{PARTNER_BASE}/{{team_id}}"]
    assert "get" in detail and "put" in detail and "delete" in detail
    entries = paths[f"{PARTNER_BASE}/commission-entries"]
    assert "get" in entries and "post" in entries
    assert "get" in paths[f"{PARTNER_BASE}/commission-entries/options"]


def test_partner_relation_and_level_routes_registered() -> None:
    spec = client.get("/openapi.json").json()
    paths = spec["paths"]
    relations = paths[RELATION_BASE]
    assert "get" in relations and "post" in relations
    assert "post" in paths[f"{RELATION_BASE}/{{relation_id}}/remove"]
    levels = paths[LEVEL_BASE]
    assert "get" in levels
    level_detail = paths[f"{LEVEL_BASE}/{{level_id}}"]
    assert "get" in level_detail and "put" in level_detail


def test_static_partner_route_not_shadowed_by_dynamic_segment() -> None:
    """commission-entries 必须命中静态路由（401），不能被 /{team_id} 吞掉返回 422。"""
    assert client.get(f"{PARTNER_BASE}/commission-entries").status_code == 401
    assert client.get(f"{PARTNER_BASE}/statistics").status_code == 401
    assert client.get(f"{PARTNER_BASE}/user-candidates?keyword=a").status_code == 401
    assert client.get(f"{PARTNER_BASE}/team-options").status_code == 401


def test_new_endpoints_require_authentication() -> None:
    assert client.get(PARTNER_BASE).status_code == 401
    assert client.post(PARTNER_BASE, json={}).status_code == 401
    assert client.get(f"{PARTNER_BASE}/1").status_code == 401
    assert client.put(f"{PARTNER_BASE}/1", json={}).status_code == 401
    assert client.delete(f"{PARTNER_BASE}/1").status_code == 401
    assert client.get(f"{PARTNER_BASE}/commission-entries").status_code == 401
    assert client.post(f"{PARTNER_BASE}/commission-entries", json={}).status_code == 401
    assert client.get(RELATION_BASE).status_code == 401
    assert client.post(RELATION_BASE, json={}).status_code == 401
    assert client.post(f"{RELATION_BASE}/1/remove", json={}).status_code == 401
    assert client.get(LEVEL_BASE).status_code == 401
    assert client.get(f"{LEVEL_BASE}/1").status_code == 401
    assert client.put(f"{LEVEL_BASE}/1", json={}).status_code == 401


# ─── schema 校验：合伙人 ─────────────────────────────────────────────


def test_partner_create_requires_user_or_lookup() -> None:
    with pytest.raises(ValidationError):
        PartnerStaffCreate(team_name="校园红娘小队")
    assert PartnerStaffCreate(user_id=5, team_name="校园红娘小队").user_id == 5
    assert PartnerStaffCreate(lookup="lemon", team_name="t").lookup_by == "nickname"


def test_partner_create_rejects_unknown_level() -> None:
    with pytest.raises(ValidationError):
        PartnerStaffCreate(user_id=5, team_name="t", level_id=4)


def test_partner_update_requires_at_least_one_field() -> None:
    with pytest.raises(ValidationError):
        PartnerStaffUpdate()
    assert PartnerStaffUpdate(level_id=2).level_id == 2


def test_partner_item_money_fields_are_str() -> None:
    item = PartnerStaffItem(
        id=1,
        team_id=1,
        user_id=2,
        team_name="富婆爱1",
        level_id=1,
        performance_amount="0.00",
        commission_amount="2.00",
    )
    payload = item.model_dump(mode="json")
    assert payload["performance_amount"] == "0.00"
    assert payload["commission_amount"] == "2.00"
    assert payload["status_label"] == "正常"


# ─── schema 校验：团队关系 / 分成明细 ────────────────────────────────


def test_relation_bind_requires_promoter_target() -> None:
    with pytest.raises(ValidationError):
        PartnerRelationBind(team_id=1)
    assert PartnerRelationBind(promoter_lookup="Sofia", team_id=1).team_id == 1
    assert PartnerRelationBind(promoter_user_id=9, team_id=1).promoter_user_id == 9


def test_commission_entry_create_requires_positive_amount() -> None:
    with pytest.raises(ValidationError):
        PartnerCommissionEntryCreate(partner_user_id=2, amount=Decimal("0"))
    with pytest.raises(ValidationError):
        PartnerCommissionEntryCreate(partner_user_id=2, amount=Decimal("-1"))
    body = PartnerCommissionEntryCreate(partner_user_id=2, amount=Decimal("12.34"))
    assert body.amount == Decimal("12.34")


def test_commission_entry_item_defaults() -> None:
    item = PartnerCommissionEntryItem(id=1, partner_id=2)
    assert item.base_amount == "0.00"
    assert item.amount == "0.00"
    assert item.status == "PENDING"
    assert item.source == "order"


# ─── schema 校验：合伙分成级别 ───────────────────────────────────────


def test_partner_level_update_accepts_business_fields() -> None:
    body = PartnerLevelUpdate(
        level_name="中级合伙人",
        auto_split_mode="auto_rate",
        auto_split_rate=Decimal("40"),
        promote_performance_threshold=Decimal("10000.00"),
        promote_member_threshold=100,
        register_reward_male=Decimal("1.00"),
        promoter_join_reward=Decimal("2.00"),
        consume_commission_mode="auto_rate",
        consume_commission_rate=Decimal("40"),
        share_bonus=False,
        bonus_items=[PartnerBonusItem(name="牵手", amount=Decimal("13.65"))],
    )
    assert body.share_bonus is False
    assert body.bonus_items and body.bonus_items[0].amount == Decimal("13.65")


def test_partner_level_update_rejects_out_of_range_rate() -> None:
    with pytest.raises(ValidationError):
        PartnerLevelUpdate(auto_split_rate=Decimal("101"))
    with pytest.raises(ValidationError):
        PartnerLevelUpdate(promote_member_threshold=-1)


def test_partner_level_item_serializes_decimals_as_str() -> None:
    item = PartnerLevelItem(
        id=1,
        level_id=1,
        level_name="初级合伙人",
        auto_split_mode="auto_rate",
        auto_split_mode_label="按比例自动计算：35%",
        auto_split_rate=Decimal("35.0000"),
        promote_condition_text="默认",
        consume_commission_mode="auto_rate",
    )
    payload = item.model_dump(mode="json")
    assert payload["auto_split_rate"] == "35.0000"
    assert payload["register_reward_male"] == "0"
    assert payload["share_bonus"] is True


# ─── 服务层辅助函数 ─────────────────────────────────────────────────


def test_partner_money_serialization() -> None:
    assert partner_service._money(None) == "0.00"
    assert partner_service._money(Decimal("3.5")) == "3.50"
    assert partner_service._money(7) == "7.00"
    assert level_service._split_label({"auto_split_mode": "fixed_amount"}) == "自定义固定金额"


def test_split_label_renders_rate() -> None:
    label = level_service._split_label(
        {"auto_split_mode": "auto_rate", "auto_split_rate": Decimal("40.0000")}
    )
    assert "按比例自动计算" in label and "40" in label


def test_promote_condition_text_combines_both_thresholds() -> None:
    text = level_service._condition_text(
        {"promote_performance_threshold": Decimal("10000.00"), "promote_member_threshold": 100}
    )
    assert "10000" in text and "100人" in text and "或" in text


def test_promote_condition_text_default_when_empty() -> None:
    assert level_service._condition_text({}) == "默认"


def test_bonus_items_parsing_is_tolerant() -> None:
    assert level_service._bonus_items(None) == []
    assert level_service._bonus_items("not-json") == []
    assert level_service._bonus_items({"a": 1}) == []
    parsed = level_service._bonus_items('[{"name": "爆灯", "amount": "3.47"}]')
    assert len(parsed) == 1 and parsed[0].amount == Decimal("3.47")


def test_commission_event_description_contains_gender_and_member_code() -> None:
    item = partner_service._entry_item(
        {
            "id": 2,
            "beneficiary_id": 5,
            "event_name": "会员注册奖励",
            "consumer_id": 847150,
            "consumer_name": "Thera",
            "consumer_gender": 2,
        }
    )
    assert item.event_name == "相亲会员Thera(Q847150) - 女 - 会员注册奖励"
    assert item.event_type == "会员注册奖励"


def test_relation_status_labels() -> None:
    assert partner_service._RELATION_STATUS_LABEL[1] == "正常"
    assert partner_service._RELATION_STATUS_LABEL[2] == "移出"
    assert partner_service._RELATION_STATUS_LABEL[3] == "变更"


def test_partner_stats_columns_use_beneficiary_type_partner() -> None:
    assert "'partner'" in partner_service._STATS_COLUMNS
    assert "ce.beneficiary_id = t.owner_user_id" in partner_service._STATS_COLUMNS


def test_relation_sql_scopes_by_promoter_attribution() -> None:
    assert "promotion_attribution" in partner_service._RELATION_SELECT
    assert "payment_order" in partner_service._RELATION_SELECT


# ─── 迁移与配置域 ────────────────────────────────────────────────────


def test_m6_columns_migration_registered() -> None:
    from database_setup_marriage import DatabaseManager

    source = inspect.getsource(DatabaseManager)
    assert "def _ensure_m6_columns" in source
    assert "self._ensure_m6_columns(cursor)" in source
    assert "partner_level_config" in source
    assert "partner_team" in source


def test_partner_level_config_table_defined() -> None:
    from app.db.business_schema import BUSINESS_TABLES

    ddl = BUSINESS_TABLES["partner_level_config"]
    for column in (
        "level_id",
        "level_name",
        "auto_split_mode",
        "promote_performance_threshold",
        "promote_member_threshold",
        "promoter_join_reward",
        "share_bonus",
        "bonus_items",
    ):
        assert column in ddl, column
    assert "`level_id`" in BUSINESS_TABLES["partner_team"]


def test_commission_entry_allows_null_order_id() -> None:
    from app.db.business_schema import BUSINESS_TABLES

    ddl = BUSINESS_TABLES["commission_entry"]
    assert "`order_id` bigint unsigned DEFAULT NULL" in ddl
    assert "`source`" in ddl and "`remark`" in ddl


def test_tools_love_partner_config_has_share_bonus() -> None:
    _, _, payload, _ = DEFAULT_CONFIGS["tools_love_partner"]
    assert payload.get("share_bonus") is True


def test_partner_bonus_config_domain_exists() -> None:
    _, _, payload, _ = DEFAULT_CONFIGS["tools_partner_bonus"]
    assert "levels" in payload and "mode" in payload

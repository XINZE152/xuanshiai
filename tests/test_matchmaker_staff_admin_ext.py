"""单元测试：红娘管理后台扩展字段与工作量指标。

覆盖：
- matchmaker_profile 4 个新列与排序索引已写入建表 SQL；
- MatchmakerStaffCreate 入参约束（slogan 长度 / sort 非负 / contact_editable 默认 True）；
- MatchmakerStaffUpdate 全空可构造；
- MatchmakerWorkReport 新增指标字段默认值正确；
- OpenAPI 中 /api/v1/admin/matchmakers 仍正常注册（路由签名未改动）。
"""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.db.business_schema import BUSINESS_TABLES
from app.main import app
from app.schemas.matchmaker_staff_admin import (
    MatchmakerStaffCreate,
    MatchmakerStaffUpdate,
    MatchmakerWorkReport,
)

client = TestClient(app)

BASE = "/api/v1/admin/matchmakers"
PROFILE_DDL = BUSINESS_TABLES["matchmaker_profile"]


def test_matchmaker_profile_new_columns_exist_in_ddl() -> None:
    for column in ("slogan", "sort", "contact_editable", "lock_at"):
        assert column in PROFILE_DDL, f"建表 SQL 缺少列 {column}"
    # 排序索引
    assert "idx_matchmaker_profile_sort" in PROFILE_DDL
    assert "(`sort`, `deleted_at`)" in PROFILE_DDL


def test_create_slogan_max_length() -> None:
    with pytest.raises(ValidationError):
        MatchmakerStaffCreate(
            display_name="测试红娘", phone="13800138000", slogan="x" * 65
        )


def test_create_sort_must_be_non_negative() -> None:
    with pytest.raises(ValidationError):
        MatchmakerStaffCreate(
            display_name="测试红娘", phone="13800138000", sort=-1
        )


def test_create_contact_editable_default_true() -> None:
    obj = MatchmakerStaffCreate(display_name="测试红娘", phone="13800138000")
    assert obj.contact_editable is True
    assert obj.sort == 0
    assert obj.slogan is None
    assert obj.lock_at is None


def test_update_all_optional_constructible() -> None:
    obj = MatchmakerStaffUpdate()
    assert obj.slogan is None
    assert obj.sort is None
    assert obj.contact_editable is None
    assert obj.lock_at is None


def test_work_report_new_metrics_defaults() -> None:
    report = MatchmakerWorkReport(
        matchmaker_id=1, from_date=date(2026, 1, 1), to_date=date(2026, 1, 2)
    )
    assert report.new_member_count == 0
    assert report.lead_follow_up_count == 0
    assert report.meeting_request_count == 0
    assert report.meeting_arranged_count == 0
    assert report.offline_income == Decimal("0.00")


def test_matchmakers_openapi_registered() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert BASE in paths
    assert "get" in paths[BASE]
    assert "post" in paths[BASE]

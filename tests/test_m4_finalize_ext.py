"""单元测试：M4 收尾（客源批量导入 / 约会管理 / 推广管理）。

覆盖范围：
- 客源线索新增路由注册：``GET /import-template``、``POST /import``、``GET /options``
- 约会管理新增路由注册：``GET /statistics``、``POST ""``（直接添加约会）
- 推广管理路由注册：``GET/PATCH/DELETE /promotion-orders``、``GET /statistics``
- 未登录访问上述 admin 端点均 401
- ``build_import_template`` / ``parse_import_file`` 往返一致（openpyxl）
- schema 层：``PromotionOrderUpdate`` 互斥校验、``MeetingDirectCreate`` 默认值
"""

from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from app.main import app
from app.schemas.meeting import MeetingDirectCreate, MeetingRecordResponse, MeetingStatistics
from app.schemas.promotion_order_admin import PromotionOrderUpdate
from app.services import customer_lead_admin as lead_service

client = TestClient(app)

LEAD_BASE = "/api/v1/admin/customer-leads"
MEETING_BASE = "/api/v1/admin/matchmaker/meetings"
PROMO_BASE = "/api/v1/admin/promotion-orders"


# ─── 路由注册 ────────────────────────────────────────────────────────


def test_customer_lead_new_routes_registered() -> None:
    spec = client.get("/openapi.json").json()
    assert "get" in spec["paths"][f"{LEAD_BASE}/import-template"]
    assert "post" in spec["paths"][f"{LEAD_BASE}/import"]
    assert "get" in spec["paths"][f"{LEAD_BASE}/options"]


def test_meeting_new_routes_registered() -> None:
    spec = client.get("/openapi.json").json()
    assert "get" in spec["paths"][f"{MEETING_BASE}/statistics"]
    assert "post" in spec["paths"][MEETING_BASE]
    assert "get" in spec["paths"][f"{MEETING_BASE}/options"]


def test_promotion_order_routes_registered() -> None:
    spec = client.get("/openapi.json").json()
    root = spec["paths"][PROMO_BASE]
    assert "get" in root
    assert "get" in spec["paths"][f"{PROMO_BASE}/statistics"]
    detail = spec["paths"][f"{PROMO_BASE}/{{order_id}}"]
    assert "get" in detail and "patch" in detail and "delete" in detail


def test_new_endpoints_require_authentication() -> None:
    assert client.get(f"{LEAD_BASE}/import-template").status_code == 401
    assert client.get(f"{LEAD_BASE}/options").status_code == 401
    assert client.get(f"{MEETING_BASE}/statistics").status_code == 401
    assert client.post(f"{MEETING_BASE}", json={}).status_code == 401
    assert client.get(PROMO_BASE).status_code == 401
    assert client.get(f"{PROMO_BASE}/statistics").status_code == 401


# ─── Excel 模板与解析 ────────────────────────────────────────────────


def test_build_import_template_has_expected_headers() -> None:
    content = lead_service.build_import_template()
    workbook = load_workbook(BytesIO(content), read_only=True)
    headers = [cell for cell in next(workbook.active.iter_rows(min_row=1, max_row=1, values_only=True))]
    assert headers == ["姓名", "手机号", "微信号", "来源", "意向程度", "备注"]


def _make_workbook(rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["姓名", "手机号", "微信号", "来源", "意向程度", "备注"])
    for row in rows:
        sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_parse_import_file_ok() -> None:
    content = _make_workbook([
        ["张女士", "13800000001", "zhang_wx", "抖音", "中", "备注A"],
        ["李先生", "13800000002", "", "", "高", ""],
        ["", "", "", "", "", ""],  # 整行空白，应被跳过
    ])
    rows = lead_service.parse_import_file(content, "leads.xlsx")
    assert len(rows) == 2
    assert rows[0].name == "张女士"
    assert rows[0].intention_level == 2
    assert rows[1].source == "批量导入"  # 留空回退默认来源
    assert rows[1].wechat is None


def test_parse_import_file_rejects_legacy_xls() -> None:
    with pytest.raises(HTTPException) as exc:
        lead_service.parse_import_file(b"anything", "leads.xls")
    assert exc.value.status_code == 400


def test_parse_import_file_rejects_missing_columns() -> None:
    workbook = Workbook()
    workbook.active.append(["昵称", "备注"])
    buffer = BytesIO()
    workbook.save(buffer)
    with pytest.raises(HTTPException) as exc:
        lead_service.parse_import_file(buffer.getvalue(), "leads.xlsx")
    assert exc.value.status_code == 400


def test_parse_import_file_rejects_empty_body() -> None:
    content = _make_workbook([["", "", "", "", "", ""]])
    with pytest.raises(HTTPException) as exc:
        lead_service.parse_import_file(content, "leads.xlsx")
    assert exc.value.status_code == 400


# ─── schema 校验 ─────────────────────────────────────────────────────


def test_promotion_order_update_requires_change() -> None:
    with pytest.raises(ValueError):
        PromotionOrderUpdate()


def test_promotion_order_update_accepts_partial() -> None:
    body = PromotionOrderUpdate(pay_status="paid")
    assert body.model_dump(exclude_unset=True, exclude_none=True) == {"pay_status": "paid"}


def test_meeting_direct_create_defaults() -> None:
    body = MeetingDirectCreate(from_user_id=1, to_user_id=2, organizer_id=3)
    assert body.member_visible is True
    assert body.sms_remind is True
    assert body.met is False
    assert body.scheduled_at is None


def test_meeting_record_response_has_new_fields() -> None:
    fields = MeetingRecordResponse.model_fields
    for name in ("member_visible", "sms_remind", "from_nickname", "to_nickname", "organizer_name"):
        assert name in fields, f"MeetingRecordResponse 缺 {name}"


def test_meeting_statistics_defaults_zero() -> None:
    stats = MeetingStatistics()
    assert stats.model_dump() == {
        "total_arranged": 0, "total_met": 0, "month_arranged": 0,
        "month_waiting": 0, "month_met": 0, "month_not_met": 0,
    }

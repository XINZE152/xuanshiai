"""客源「本人资料」字段（对标站新建/详情表单）回归测试（2026-10-28）。

覆盖：
- schema：CustomerLeadCreate/Update/Response 携带资料字段，Create/Response 默认 None；
  Update 部分更新口径（仅空白联系方式视为无修改项）；OpenAPI 暴露字段；
- 服务：``_lead_select_for`` 只注入真实存在的列、age 由 birth_date 派生；
  ``create_lead`` 写入资料列并把 photos 序列化、birth_date 派生 raw age；
- 入库搬运：``_map_lead_marriage`` 映射、``_upsert_profile_row`` 缺列跳过 + JSON。

无真实 DB：monkeypatch 服务层 DB 助手；FakeDb 仅记录写调用。
"""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest
from pydantic import ValidationError

from app.schemas.customer_lead_admin import (
    CustomerLead, CustomerLeadCreate, CustomerLeadUpdate,
)
from app.services import customer_lead_admin as lead_service


def _lead(**overrides) -> CustomerLead:
    now = datetime(2026, 10, 28)
    base = dict(
        id=10, name="小李", phone="13800138000", wechat=None, source="抖音",
        intention_level=1, gender=1, status="NEW", audit_status="active",
        matchmaker_id=None, organization_id=None, promoter_id=None,
        next_follow_at=None, converted_user_id=None, remark=None, created_by=1,
        created_at=now, updated_at=now,
    )
    base.update(overrides)
    return CustomerLead(**base)


def _make_result(lastrowid: int = 10):
    class _R:
        def __init__(self) -> None:
            self.lastrowid = lastrowid

        def scalar(self):
            return None

        def first(self):
            return None

        def mappings(self):
            return self

        def all(self):
            return []

    return _R()


class _FakeDb:
    """记录写调用；INSERT 用 lastrowid=10，其它 execute 返回空结果。"""

    def __init__(self) -> None:
        self.writes: list[tuple[str, dict]] = []
        self.committed = False

    async def execute(self, stmt, params=None):  # noqa: ANN001
        sql = str(stmt)
        self.writes.append((sql, dict(params or {})))
        return _make_result(10 if "INSERT INTO customer_lead" in sql else 0)

    async def scalar(self, stmt, params=None):  # noqa: ANN001
        return None

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        pass


# --------------------------------------------------------------------------- schema

def test_create_schema_profile_fields_default_none() -> None:
    body = CustomerLeadCreate(name="王女士", phone="13800000000", source="线下")
    assert body.real_name is None and body.birth_date is None and body.photos is None
    full = CustomerLeadCreate(
        name="王女士", phone="13800000000", source="线下",
        real_name="王小明", birth_date=date(1995, 6, 1), height=165,
        weight=55, marriage="未婚", education="本科", photos=["a.jpg", "b.jpg"],
    )
    assert full.real_name == "王小明" and full.height == 165 and full.photos == ["a.jpg", "b.jpg"]


def test_update_requires_at_least_one_non_none_value() -> None:
    # 仅提交空白联系方式（归一化后为 None）等价于没有修改项 → 校验失败
    with pytest.raises(ValidationError):
        CustomerLeadUpdate(wechat="  ")
    # 单个新增资料字段即视为有效更新
    assert CustomerLeadUpdate(real_name="张三").real_name == "张三"
    # 空列表是显式值（清空相册），不算「未提供」
    assert CustomerLeadUpdate(photos=[]).photos == []


def test_response_carries_age_and_profile_fields() -> None:
    lead = _lead(real_name="张三", age=30, birth_date=date(1995, 1, 1), photos=["x"])
    assert lead.age == 30 and lead.real_name == "张三" and lead.photos == ["x"]


def test_openapi_exposes_profile_fields() -> None:
    from app.main import app

    spec = app.openapi()
    props = spec["components"]["schemas"]["CustomerLeadCreate"]["properties"]
    for field in ("real_name", "birth_date", "marriage", "height", "weight", "photos"):
        assert field in props
    resp_props = spec["components"]["schemas"]["CustomerLead"]["properties"]
    assert "age" in resp_props


# --------------------------------------------------------------------------- select

def test_lead_select_derives_age_from_birth_date() -> None:
    sql = lead_service._lead_select_for(frozenset({"gender", "birth_date", "photos", "age"}))
    assert "TIMESTAMPDIFF(YEAR, birth_date, UTC_DATE()) AS age" in sql
    assert "photos" in sql and "CAST(gender AS SIGNED) AS gender" in sql
    # 派生 age 时不再输出 raw age 列，避免重名
    assert sql.count(" AS age") == 1


def test_lead_select_skips_missing_columns() -> None:
    sql = lead_service._lead_select_for(frozenset({"real_name"}))
    assert "real_name" in sql
    assert "gender" not in sql and "photos" not in sql and "AS age" not in sql


# --------------------------------------------------------------------------- mapping

@pytest.mark.parametrize(
    ("raw", "expected"),
    [("未婚", 1), ("离异", 2), ("丧偶", 3), ("1", 1), ("3", 3),
     ("已婚", None), ("", None), (None, None), ("0", None), ("9", None)],
)
def test_map_lead_marriage(raw, expected) -> None:
    assert lead_service._map_lead_marriage(raw) == expected


# --------------------------------------------------------------------------- upsert

@pytest.mark.asyncio
async def test_upsert_profile_row_skips_missing_and_serializes_json() -> None:
    db = _FakeDb()
    await lead_service._upsert_profile_row(
        db, "user_profile", 55,
        {"height": 165, "ethnicity": "汉族", "photos": json.dumps(["a.jpg"], ensure_ascii=False), "weight": None},
        frozenset({"height", "photos"}),  # ethnicity 列不存在 → 跳过；weight None → 跳过
    )
    sql, params = db.writes[0]
    assert "INSERT INTO user_profile" in sql and "ON DUPLICATE KEY UPDATE" in sql
    assert params["height"] == 165 and params["photos"] == '["a.jpg"]'
    assert "ethnicity" not in params and "weight" not in params


@pytest.mark.asyncio
async def test_upsert_profile_row_noop_when_no_writable_column() -> None:
    db = _FakeDb()
    await lead_service._upsert_profile_row(
        db, "user_auth", 55, {"real_name": None, "school": None}, frozenset({"real_name", "school"}),
    )
    assert db.writes == []


# --------------------------------------------------------------------------- create_lead

@pytest.mark.asyncio
async def test_create_lead_writes_profile_and_derives_age(monkeypatch: pytest.MonkeyPatch) -> None:
    cols = frozenset({"gender", "real_name", "birth_date", "height", "photos", "age", "marriage"})

    async def fake_columns(_db):
        return cols

    async def noop(*_a, **_k):
        return None

    monkeypatch.setattr(lead_service, "_lead_columns", fake_columns)
    monkeypatch.setattr(lead_service, "ensure_contact_available", noop)
    monkeypatch.setattr(lead_service, "_ensure_user_in_scope", noop)
    monkeypatch.setattr(lead_service, "resolve_assigned_matchmaker", noop)
    monkeypatch.setattr(lead_service, "get_lead", lambda *_a, **_k: _async_ret(_lead(real_name="王小明", age=31)))
    db = _FakeDb()

    request = CustomerLeadCreate(
        name="王小明", phone="13800138000", source="抖音",
        real_name="王小明", birth_date=date(1995, 1, 1), height=180,
        photos=["a.jpg", "b.jpg"], marriage="未婚",
    )
    await lead_service.create_lead(db, 1, request)

    insert_sql, insert_params = next(
        (sql, p) for sql, p in db.writes if "INSERT INTO customer_lead" in sql
    )
    assert "real_name" in insert_sql and "height" in insert_sql and "photos" in insert_sql
    assert insert_params["real_name"] == "王小明" and insert_params["height"] == 180
    assert json.loads(insert_params["photos"]) == ["a.jpg", "b.jpg"]
    # birth_date 有值 → 派生 raw age 列（整数）
    assert isinstance(insert_params["age"], int)


def _async_ret(value):
    async def _coro():
        return value
    return _coro()

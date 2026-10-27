"""2026-10-24 接口缺口清单 A16/A14/A17 回归。

覆盖范围（无需 DB，纯 schema / 服务逻辑 / 路由注册顺序）：
- A16：客源软删除——列表 / 统计 / 详情 SQL 在迁移已执行时排除 deleted_at；
  未迁移时缺列降级不加谓词；单条删除缺列 503；批量删除部分成功 + 一份汇总审计；
  batch-delete 静态路径先于 /{lead_id} 注册；请求体边界。
- A14：来源字典——kind 参数校验、引用守卫 SQL、reorder 逐条写 sort、
  字典路由挂在 /admin/customer-leads/sources。（客源标签字典已下线。）
- A17：核验额度充值——默认 provider=disabled、套餐目录只读、订单列与建表 DDL 一致、
  路由注册。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api.routes import customer_leads_admin as lead_routes
from app.api.routes import member_auth_admin as auth_routes
from app.db.business_schema import BUSINESS_TABLES
from app.schemas.customer_lead_admin import CustomerLeadBatchDeleteRequest
from app.schemas.customer_lead_dict_admin import LeadDictOrder, LeadDictUpdate
from app.schemas.member_auth_recharge_admin import QuotaPackage, RechargeCreateRequest, RechargeOrder
from app.services import customer_lead_dict_admin as dict_service
from app.services import member_auth_recharge_admin as recharge_service
from app.services.customer_lead_admin import batch_delete_leads, delete_lead


class _Result:
    def __init__(self, *, rows=None, mappings_rows=None, scalar=None, lastrowid=None):
        self._rows = list(rows or [])
        self._mappings_rows = list(mappings_rows or [])
        self._scalar = scalar
        self.lastrowid = lastrowid

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._scalar

    def mappings(self):
        return SimpleNamespace(all=lambda: self._mappings_rows, first=lambda: (self._mappings_rows[0] if self._mappings_rows else None))


class _StubDB:
    """按序返回预设结果；记录写库语句，用于无 DB 环境下的服务逻辑断言。"""

    def __init__(self, results: list[_Result]):
        self._results = list(results)
        self.writes: list[tuple[str, dict]] = []
        self.commits = 0

    async def execute(self, statement, params=None):
        sql = str(statement)
        if self._results:
            return self._results.pop(0)
        if sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            self.writes.append((sql, dict(params or {})))
        return _Result(rows=[], mappings_rows=[])

    async def commit(self):
        self.commits += 1


# --------------------------------------------------------------------------- #
# A16 服务：软删除过滤读路径（迁移已执行时排除 deleted_at）
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_get_lead_filter_reflects_column_presence(monkeypatch) -> None:
    import app.services.customer_lead_admin as lead_service

    captured: dict[str, str] = {}

    async def _fake_columns(_db):
        return frozenset({"deleted_at"})

    async def _fake_execute(statement, params=None):
        captured["sql"] = str(statement)
        return _Result(mappings_rows=[])  # 空结果 → get_lead 抛 404

    db = SimpleNamespace(execute=_fake_execute)
    monkeypatch.setattr(lead_service, "_lead_columns", _fake_columns)
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        await lead_service.get_lead(db, 1)
    assert "deleted_at IS NULL" in captured["sql"]


# --------------------------------------------------------------------------- #
# A16 服务：单条 / 批量删除
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_delete_lead_503_when_column_missing() -> None:
    db = _StubDB([_Result(rows=[("id",)])])  # 无 deleted_at 列
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await delete_lead(db, account_id=1, lead_id=1, scope="1=1", scope_params={})
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_batch_delete_partial_success_single_audit() -> None:
    db = _StubDB([
        _Result(rows=[("deleted_at",)]),   # 迁移已执行
        _Result(rows=[(1,), (2,)]),        # FOR UPDATE：仅 1/2 在范围内且未删除
    ])
    deleted, failed = await batch_delete_leads(db, 9, [1, 2, 3], scope="1=1", scope_params={})
    assert deleted == [1, 2]
    assert failed == [{"id": "3", "error": "客源不存在、已删除或越权"}]
    assert db.commits == 1
    inserts = [sql for sql, _ in db.writes]
    assert sum("business_audit_log" in s for s in inserts) == 1
    assert sum("UPDATE customer_lead" in s for s in inserts) == 1
    # 3 不进入删除集合
    del_params = next(params for sql, params in db.writes if "UPDATE customer_lead" in sql)
    assert "bdel_2" not in del_params and del_params["del_0"] == 1 and del_params["del_1"] == 2


def test_batch_delete_request_bounds() -> None:
    CustomerLeadBatchDeleteRequest(lead_ids=[1, 2, 3])
    with pytest.raises(Exception):
        CustomerLeadBatchDeleteRequest(lead_ids=[])
    with pytest.raises(Exception):
        CustomerLeadBatchDeleteRequest(lead_ids=list(range(1, 202)))


# --------------------------------------------------------------------------- #
# A16 路由注册顺序 + 出参 schema
# --------------------------------------------------------------------------- #


def test_batch_delete_route_registered_before_dynamic_lead_id() -> None:
    paths = [route.path for route in lead_routes.router.routes]
    assert "/admin/customer-leads/batch-delete" in paths
    assert paths.index("/admin/customer-leads/batch-delete") < paths.index("/admin/customer-leads/{lead_id}")


def test_single_delete_route_exists() -> None:
    delete_paths = [r.path for r in lead_routes.router.routes if "DELETE" in (r.methods or set())]
    assert "/admin/customer-leads/{lead_id}" in delete_paths


# --------------------------------------------------------------------------- #
# A14 字典服务
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_dict_rejects_unknown_kind() -> None:
    from fastapi import HTTPException

    db = _StubDB([])
    with pytest.raises(HTTPException) as exc:
        await dict_service.list_dict(db, "unknown")
    assert exc.value.status_code == 422


def test_dict_columns_include_color_for_sources() -> None:
    assert "color" in dict_service._COLUMNS["sources"]


def test_dict_referenced_sql_targets_correct_tables() -> None:
    assert "FROM customer_lead" in dict_service._REFERENCED_SQL["sources"]
    assert "deleted_at IS NULL" in dict_service._REFERENCED_SQL["sources"]


@pytest.mark.asyncio
async def test_dict_reorder_writes_sort_by_index() -> None:
    db = _StubDB([])
    await dict_service.reorder_dict(db, "sources", [10, 20, 30])
    sorts = [params["sort"] for _, params in db.writes]
    assert sorts == [1, 2, 3]
    assert db.commits == 1


def test_lead_dict_update_requires_at_least_one_field() -> None:
    with pytest.raises(Exception):
        LeadDictUpdate()
    assert LeadDictUpdate(enabled=0).enabled == 0


def test_lead_dict_order_bounds() -> None:
    LeadDictOrder(ids=[1, 2])
    with pytest.raises(Exception):
        LeadDictOrder(ids=[])


def test_dict_routes_registered_under_customer_leads() -> None:
    paths = [route.path for route in lead_routes.router.routes]
    # 前端契约：复数段、挂在 /admin/customer-leads 下
    for static in (
        "/admin/customer-leads/sources",
        "/admin/customer-leads/sources/reorder",
    ):
        assert static in paths
    # 静态字典段必须先于 /{lead_id} 动态路由注册，否则被抢占
    dynamic_idx = paths.index("/admin/customer-leads/{lead_id}")
    assert paths.index("/admin/customer-leads/sources") < dynamic_idx
    # 客源标签字典路由已下线
    assert "/admin/customer-leads/tags" not in paths
    # 旧的独立字典路由已下线
    assert not hasattr(lead_routes, "dict_router")


def test_source_dict_table_registered_in_business_schema() -> None:
    ddl = BUSINESS_TABLES["customer_lead_source"]
    assert "CREATE TABLE IF NOT EXISTS `customer_lead_source`" in ddl
    assert "uk_customer_lead_source" in ddl


# --------------------------------------------------------------------------- #
# A17 核验额度充值骨架
# --------------------------------------------------------------------------- #


def test_default_provider_is_disabled() -> None:
    assert recharge_service.current_provider() == "disabled"


def test_list_packages_filter_and_shape() -> None:
    assert len(recharge_service.list_packages()) == 6
    realname = recharge_service.list_packages("realname")
    assert realname and all(p.kind == "realname" for p in realname)
    assert all(isinstance(p, QuotaPackage) for p in realname)


def test_recharge_order_columns_exist_in_ddl() -> None:
    ddl = BUSINESS_TABLES["member_auth_recharge_order"]
    head = recharge_service._ORDER_SELECT.split("FROM", 1)[0]
    for column in head.replace("SELECT", "").split(","):
        column = column.strip()
        if column:
            assert f"`{column}`" in ddl, column


@pytest.mark.asyncio
async def test_create_order_503_when_disabled() -> None:
    from fastapi import HTTPException

    db = _StubDB([])
    with pytest.raises(HTTPException) as exc:
        await recharge_service.create_order(db, 1, "realname", "realname_100", 1)
    assert exc.value.status_code == 503


def test_recharge_request_bounds() -> None:
    RechargeCreateRequest(kind="realname", package_id="realname_100", quantity=1)
    with pytest.raises(Exception):
        RechargeCreateRequest(kind="unknown", package_id="x")
    with pytest.raises(Exception):
        RechargeCreateRequest(kind="realname", package_id="x", quantity=0)


def test_recharge_routes_registered_before_dynamic_review() -> None:
    paths = [route.path for route in auth_routes.router.routes]
    for static in (
        "/admin/members/auth/quota-packages",
        "/admin/members/auth/recharge",
        "/admin/members/auth/recharge/{order_no}",
    ):
        assert static in paths
    dynamic_idx = paths.index("/admin/members/auth/{kind}/{review_id}")
    assert paths.index("/admin/members/auth/quota-packages") < dynamic_idx
    assert paths.index("/admin/members/auth/recharge") < dynamic_idx


def test_recharge_order_model_roundtrip() -> None:
    order = RechargeOrder(
        order_no="VR1", kind="realname", package_id="realname_100", package_name="实名认证 100 次",
        quantity=100, unit_price=200.0, total_amount=200.0, currency="CNY", status="pending",
        provider="mock", pay_url="https://mock.pay/VR1", qr_code_url=None,
        created_at="2026-10-24T00:00:00", paid_at=None,
    )
    assert order.model_dump(mode="json")["status"] == "pending"

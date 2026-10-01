"""D-4 回归：后台数据范围下沉（第 3–6 组）。

覆盖方案清单 D-4 的：
- 第 3 组 会员建档创建/更新（`matchmaker_member_admin.py`）
- 第 4 组 约见后台列表/统计（`meeting.py` / `services/meeting.py`）
- 第 5 组 finance 汇总/日报/订单/提现/结算/导出（`finance.py` / `services/finance.py`）
- 第 6 组 门店/组织/资源分配（`organization_admin.py` / `services/organization_admin.py`）

验收口径：
1. A 组织账号访问 B 组织数据 → 0 条或 403/404；
2. 无 data_scope 绑定的账号访问业务数据 → 0 条（fail-closed）；
3. 作用域逻辑只在 `dependencies.py` 一处实现。
"""

from __future__ import annotations

import inspect
from datetime import datetime

import pytest
from fastapi import HTTPException

from app.api.dependencies import CurrentMatchmakerAdmin
from app.schemas.matchmaker_admin import MatchmakerAdminAccount

MATCHMAKER_USER_ID = 7
ORGANIZATION_ID = 10
_NOW = datetime(2026, 10, 4, 12, 0, 0)


def _admin(scope: str, *, permissions: set[str] | None = None, matchmaker_user_id: int | None = MATCHMAKER_USER_ID, organization_id: int | None = ORGANIZATION_ID) -> CurrentMatchmakerAdmin:
    return CurrentMatchmakerAdmin(
        account=MatchmakerAdminAccount(
            id=1,
            username="admin",
            display_name="Admin",
            matchmaker_user_id=matchmaker_user_id,
            data_scope=scope,
            organization_id=organization_id,
            status=1,
            last_login_at=None,
        ),
        session_id=1,
        permissions=frozenset(permissions or set()),
    )


class StubResult:
    def __init__(self, *, rows=None, scalar=None, first=None, one=None, lastrowid=1, rowcount=0):
        self._rows = list(rows or [])
        self._scalar = scalar
        self._first = first
        self._one = one
        self.lastrowid = lastrowid
        self.rowcount = rowcount

    def all(self):
        return list(self._rows)

    def scalar(self):
        return self._scalar

    def first(self):
        return self._first

    def one(self):
        if self._one is None:
            raise AssertionError("one() 未预期被调用")
        return self._one

    def mappings(self):
        return self


class StubDB:
    def __init__(self, results=None):
        self.statements: list[tuple[str, dict]] = []
        self._results = list(results or [])
        self.committed = False

    async def execute(self, statement, params=None):
        self.statements.append((str(statement), dict(params or {})))
        if self._results:
            return self._results.pop(0)
        return StubResult()

    async def scalar(self, statement, params=None):
        return (await self.execute(statement, params)).scalar()

    async def rollback(self):
        self.rolled_back = True

    async def commit(self):
        self.committed = True

    def sql(self) -> str:
        return "\n".join(text for text, _ in self.statements)


# --------------------------------------------------------------------------- #
# 组织维度作用域工具
# --------------------------------------------------------------------------- #


def test_scope_organization_clause_tiers() -> None:
    all_params: dict[str, object] = {}
    assert _admin("ALL").scope_organization_clause(all_params, column="o.id") == "1 = 1"
    assert all_params == {}

    store = _admin("STORE").scope_organization_clause({}, column="o.id")
    assert "org_type = 'store'" in store and "parent_id" not in store

    org = _admin("ORGANIZATION").scope_organization_clause({}, column="o.id")
    assert "parent_id" in org

    # SELF 账号对「组织自有资源」一无所见，不得退化为按个人 ID 匹配组织 ID。
    self_params: dict[str, object] = {}
    assert _admin("SELF").scope_organization_clause(self_params, column="o.id") == "1 = 0"
    assert self_params == {}

    assert _admin("ORGANIZATION", organization_id=None).scope_organization_clause({}, column="o.id") == "1 = 0"


# --------------------------------------------------------------------------- #
# 第 3 组：会员建档
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_member_detail_service_is_scoped() -> None:
    from app.services.matchmaker_member_admin import _member

    db = StubDB([StubResult(first=None)])
    params: dict[str, object] = {}
    scope = _admin("SELF").scope_exists_clause(params, correlation="scope_assignment.user_id = u.id")
    with pytest.raises(HTTPException) as exc:
        await _member(db, 99, scope, params)
    assert exc.value.status_code == 404
    assert "scope_assignment.matchmaker_id = :scope_matchmaker_user_id" in db.statements[0][0]


@pytest.mark.asyncio
async def test_member_create_attributes_owner_within_scope() -> None:
    """建档必须落归属，否则门店/组织账号建出的会员谁都看不到。"""
    from app.schemas.matchmaker_member_admin import MatchmakerMemberCreate
    from app.services import matchmaker_member_admin as service

    body = MatchmakerMemberCreate(phone="13800000000", nickname="新会员", gender=1)
    db = StubDB([
        StubResult(scalar=None),          # 手机号重复检查
        StubResult(lastrowid=501),        # INSERT users
        StubResult(),                     # INSERT resource_assignment
        StubResult(),                     # INSERT member_note
        StubResult(),                     # INSERT audit
        StubResult(first={                 # 回读会员
            "id": 501, "nickname": "新会员", "phone": "13800000000", "gender": 1,
            "status": 1, "created_at": _NOW, "updated_at": _NOW,
            "matchmaker_id": MATCHMAKER_USER_ID, "vip_end_at": None, "is_vip": 0,
        }),
    ])
    await service.create_member(
        db, body, 1, organization_id=ORGANIZATION_ID, matchmaker_user_id=MATCHMAKER_USER_ID
    )
    assert "INSERT INTO resource_assignment" in db.sql()
    assert db.statements[2][1] == {
        "user_id": 501,
        "organization_id": ORGANIZATION_ID,
        "matchmaker_id": MATCHMAKER_USER_ID,
        "assigned_by": 1,
    }
    assert db.committed is True


# --------------------------------------------------------------------------- #
# 第 4 组：约见后台
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_meeting_admin_list_requests_is_scoped() -> None:
    from app.services.meeting import admin_list_requests

    db = StubDB([StubResult(rows=[]), StubResult(rows=[]), StubResult(scalar=0)])
    page = await admin_list_requests(db, 1, 20, admin=_admin("SELF"))
    assert page.total == 0
    assert "r.matchmaker_id = :scope_matchmaker_user_id" in db.sql()


@pytest.mark.asyncio
async def test_meeting_admin_list_meetings_fail_closed_when_unbound() -> None:
    from app.services.meeting import admin_list_meetings

    db = StubDB([StubResult(rows=[]), StubResult(rows=[]), StubResult(scalar=0)])
    page = await admin_list_meetings(
        db, 1, 20, admin=_admin("ORGANIZATION", matchmaker_user_id=None, organization_id=None)
    )
    assert page.total == 0
    assert "1 = 0" in db.sql()


@pytest.mark.asyncio
async def test_meeting_detail_hides_out_of_scope_record() -> None:
    from app.services.meeting import admin_get_meeting

    db = StubDB([StubResult(first=None)])
    with pytest.raises(HTTPException) as exc:
        await admin_get_meeting(db, 99, admin=_admin("STORE"))
    assert exc.value.status_code == 404
    assert "mr.organizer_id" in db.statements[0][0]


@pytest.mark.asyncio
async def test_meeting_statistics_is_scoped() -> None:
    from app.services.meeting import admin_meeting_statistics

    keys = ("total_arranged", "total_met", "month_arranged", "month_waiting", "month_met", "month_not_met")
    db = StubDB([StubResult(one={key: 0 for key in keys})])
    await admin_meeting_statistics(db, admin=_admin("STORE"))
    assert "mr.organization_id IN (SELECT id FROM organization" in db.statements[0][0]
    assert db.statements[0][1].get("scope_store_id") == ORGANIZATION_ID


@pytest.mark.asyncio
async def test_meeting_create_rolls_back_when_owner_out_of_scope() -> None:
    """A 组织账号不得用本组织范围外的红娘/组织建档。"""
    from app.schemas.meeting import MeetingDirectCreate
    from app.services.meeting import admin_create_meeting

    db = StubDB([
        StubResult(rows=[(1,), (2,)]),    # 双方会员存在
        StubResult(scalar=1),             # 服务红娘存在
        StubResult(lastrowid=11),         # INSERT meeting_request
        StubResult(lastrowid=22),         # INSERT meeting_record
        StubResult(scalar=None),          # 归属校验：不在范围内
    ])
    body = MeetingDirectCreate(from_user_id=1, to_user_id=2, organizer_id=5)
    with pytest.raises(HTTPException) as exc:
        await admin_create_meeting(db, body, 1, admin=_admin("ORGANIZATION"))
    assert exc.value.status_code == 403
    assert db.rolled_back is True
    assert db.committed is False


# --------------------------------------------------------------------------- #
# 第 5 组：财务后台
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_finance_orders_use_member_anchor() -> None:
    from app.services.finance import admin_list_orders

    db = StubDB([StubResult(rows=[]), StubResult(scalar=0)])
    page = await admin_list_orders(db, 1, 20, admin=_admin("SELF"))
    assert page.total == 0
    assert "scope_assignment.user_id = po.user_id" in db.sql()
    assert db.statements[0][1]["scope_matchmaker_user_id"] == MATCHMAKER_USER_ID


@pytest.mark.asyncio
async def test_finance_withdrawals_fail_closed_for_unbound_org() -> None:
    from app.services.finance import admin_list_withdrawals

    db = StubDB([StubResult(rows=[]), StubResult(scalar=0)])
    page = await admin_list_withdrawals(
        db, 1, 20, admin=_admin("ORGANIZATION", matchmaker_user_id=None, organization_id=None)
    )
    assert page.total == 0
    assert "1 = 0" in db.sql()


@pytest.mark.asyncio
async def test_finance_withdrawals_split_user_and_store_accounts() -> None:
    from app.services.finance import admin_list_withdrawals

    db = StubDB([StubResult(rows=[]), StubResult(scalar=0)])
    await admin_list_withdrawals(db, 1, 20, admin=_admin("STORE"))
    sql = db.sql()
    assert "wr.account_type = 'user'" in sql
    assert "wr.account_type = 'store'" in sql
    assert "org_type = 'store'" in sql


@pytest.mark.asyncio
async def test_finance_ledger_is_scoped_by_organization_for_store_account() -> None:
    from app.services.finance import admin_list_ledger

    db = StubDB([StubResult(rows=[]), StubResult(scalar=0)])
    await admin_list_ledger(db, 1, 20, admin=_admin("ORGANIZATION"))
    assert "al.account_id IN (SELECT id FROM organization" in db.sql()
    assert db.statements[0][1]["scope_organization_id"] == ORGANIZATION_ID


@pytest.mark.asyncio
async def test_finance_report_is_scoped() -> None:
    from app.services.finance import admin_finance_report

    db = StubDB([StubResult(rows=[])])
    await admin_finance_report(db, admin=_admin("SELF"))
    sql = db.statements[0][0]
    # 分成的 beneficiary_type 类型域是 service_matchmaker/promoter/partner/store，
    # 按 'user' 过滤会让普通权限账号永远列不出应得分成（审计第 2c 项）。
    assert "ce.beneficiary_type IN ('service_matchmaker', 'promoter', 'partner')" in sql
    assert "ce.beneficiary_type = 'user'" not in sql
    assert "scope_assignment.matchmaker_id = :scope_matchmaker_user_id" in sql
    assert "scope_assignment.user_id = ce.beneficiary_id" in sql
    assert db.statements[0][1]["scope_matchmaker_user_id"] == MATCHMAKER_USER_ID

    unbound = StubDB([StubResult(rows=[])])
    await admin_finance_report(unbound, admin=_admin("SELF", matchmaker_user_id=None, organization_id=None))
    assert "1 = 0" in unbound.sql()


@pytest.mark.asyncio
async def test_finance_daily_report_is_scoped() -> None:
    from app.services.finance import admin_revenue_daily_report

    db = StubDB([StubResult(rows=[])])
    await admin_revenue_daily_report(db, None, None, admin=_admin("ORGANIZATION"))
    assert "scope_assignment.user_id = po.user_id" in db.statements[0][0]
    assert "scope_assignment.organization_id IN (SELECT id FROM organization" in db.statements[0][0]


@pytest.mark.asyncio
async def test_finance_settlement_rejects_out_of_scope_order() -> None:
    from app.services.finance import mark_order_paid_and_settle

    db = StubDB([StubResult(first=None)])
    with pytest.raises(HTTPException) as exc:
        await mark_order_paid_and_settle(
            db, None, 99, scope_admin=_admin("SELF")  # type: ignore[arg-type]
        )
    assert exc.value.status_code == 404
    assert "scope_assignment.user_id = payment_order.user_id" in db.statements[0][0]


@pytest.mark.asyncio
async def test_finance_store_commission_export_shares_list_scope() -> None:
    from app.services.finance import build_store_commission_export

    db = StubDB([StubResult(rows=[])])
    content = await build_store_commission_export(db, admin=_admin("SELF"))
    assert content[:2] == b"PK"  # xlsx 为 zip 容器
    assert "scope_assignment.user_id = po.user_id" in db.statements[0][0]


# --------------------------------------------------------------------------- #
# 第 6 组：门店 / 组织 / 资源分配
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_store_list_is_scoped_to_organization_subtree() -> None:
    from app.services.organization_admin import list_stores_admin

    db = StubDB([StubResult(rows=[]), StubResult(scalar=0)])
    items, total = await list_stores_admin(db, 1, 20, None, None, admin=_admin("ORGANIZATION"))
    assert items == [] and total == 0
    assert "o.id IN (SELECT id FROM organization" in db.sql()
    assert "parent_id" in db.sql()


@pytest.mark.asyncio
async def test_store_detail_is_hidden_from_self_scope_account() -> None:
    from app.services.organization_admin import get_store_admin

    db = StubDB([StubResult(first=None)])
    with pytest.raises(HTTPException) as exc:
        await get_store_admin(db, 99, admin=_admin("SELF"))
    assert exc.value.status_code == 404
    assert "1 = 0" in db.statements[0][0]


@pytest.mark.asyncio
async def test_store_member_removal_is_scoped() -> None:
    from app.services.organization_admin import remove_store_member

    db = StubDB([StubResult(first=None)])
    with pytest.raises(HTTPException) as exc:
        await remove_store_member(db, 5, "越权测试", 1, admin=_admin("STORE"))
    assert exc.value.status_code == 404
    assert "om.organization_id IN (SELECT id FROM organization" in db.statements[0][0]


@pytest.mark.asyncio
async def test_assignment_end_is_scoped() -> None:
    from app.services.organization_admin import end_assignment

    db = StubDB([StubResult(scalar=None)])
    with pytest.raises(HTTPException) as exc:
        await end_assignment(db, 3, "越权测试", 1, admin=_admin("SELF"))
    assert exc.value.status_code == 404
    assert "ra.matchmaker_id = :scope_matchmaker_user_id" in db.statements[0][0]


def test_d4_scope_logic_lives_only_in_dependencies() -> None:
    """作用域四档判定必须只存在于 dependencies.py，各服务/路由不得自建。"""
    from app.api.routes import finance as finance_routes
    from app.api.routes import meeting as meeting_routes
    from app.api.routes import organization_admin as organization_routes
    from app.services import finance as finance_service
    from app.services import meeting as meeting_service
    from app.services import organization_admin as organization_service

    for module in (
        finance_service, meeting_service, organization_service,
        finance_routes, meeting_routes, organization_routes,
    ):
        source = inspect.getsource(module)
        # 真正的信号：不得自行分支 data_scope 或重写四档判定。
        assert "data_scope ==" not in source, module.__name__
        assert "data_scope !=" not in source, module.__name__

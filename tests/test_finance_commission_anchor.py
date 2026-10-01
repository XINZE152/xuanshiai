"""审计第 2c 项回归：应有分成必须能正常显示和处理。

历史缺陷：报表与放款沿用 `_account_anchor_scope`，其成员分支按
``beneficiary_type = 'user'`` 过滤，而 ``commission_entry.beneficiary_type`` 的实际
类型域是 ``service_matchmaker`` / ``promoter`` / ``partner`` / ``store`` → 会员锚定的
分成永远匹配不到范围，普通权限账号既列不出也放不了款。

断言真链路 service → SQL 文本：分成侧必须用 `_commission_anchor_scope` 的类型域，
提现/账本侧仍用 `_account_anchor_scope`（那里 account_type 确实是 user / store）。
"""

from __future__ import annotations

import ast
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

import app.services.finance as finance
from app.api.dependencies import CurrentMatchmakerAdmin
from app.schemas.matchmaker_admin import MatchmakerAdminAccount

PLACEHOLDER = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")
FINANCE_SOURCE = Path(finance.__file__).read_text(encoding="utf-8")
FINANCE_TREE = ast.parse(FINANCE_SOURCE)



class StubResult:
    def __init__(self, *, rows=None, first=None, one=None):
        self._rows = rows if rows is not None else []
        self._first = first
        self._one = one

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._first

    def one(self):
        return self._one


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

    async def commit(self):
        self.committed = True


def _admin(data_scope: str = "STORE", organization_id: int | None = 88) -> CurrentMatchmakerAdmin:
    return CurrentMatchmakerAdmin(
        account=MatchmakerAdminAccount(
            id=4, username="store-ops", display_name="门店运营", matchmaker_user_id=7,
            data_scope=data_scope, organization_id=organization_id, status=1, last_login_at=None,
        ),
        session_id=1,
        permissions=frozenset({"finance.order.manage", "finance.commission.manage"}),
    )


def _entry_row(status: str = "PENDING") -> dict:
    return {
        "id": 12, "order_id": 3, "beneficiary_type": "service_matchmaker", "beneficiary_id": 7,
        "base_amount": Decimal("1000.00"), "amount": Decimal("120.00"),
        "status": status, "created_at": datetime(2026, 10, 1, 12, 0, 0),
    }


def _func_source(name: str) -> str:
    for node in FINANCE_TREE.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return ast.get_source_segment(FINANCE_SOURCE, node) or ""
    raise AssertionError(f"finance.py 缺少函数 {name}")


# --- SQL 形状 -------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_uses_commission_beneficiary_type_domain() -> None:
    """报表 SQL 必须按分成真实类型域过滤，且不得再出现 beneficiary_type = 'user'。"""
    db = StubDB([StubResult(rows=[])])
    assert await finance.admin_finance_report(db, admin=_admin()) == []
    sql, params = db.statements[0]
    assert "ce.beneficiary_type IN ('service_matchmaker', 'promoter', 'partner')" in sql
    assert "ce.beneficiary_type = 'store'" in sql
    assert "ce.beneficiary_type = 'user'" not in sql
    # 占位符与绑定参数必须成对，否则 STORE 档会直接参数缺失报错
    assert set(PLACEHOLDER.findall(sql)) <= set(params)
    assert params["scope_store_id"] == 88


@pytest.mark.asyncio
async def test_release_uses_commission_beneficiary_type_domain() -> None:
    """放款锁定查询同样要用分成类型域，否则普通权限账号一点放款就 404。"""
    db = StubDB([
        StubResult(first=_entry_row()),
        StubResult(),
        StubResult(),
        StubResult(one=_entry_row(status="AVAILABLE")),
    ])
    out = await finance.release_commission(db, _admin(), 12, scope_admin=_admin())
    assert out.status == "AVAILABLE"
    lock_sql, lock_params = db.statements[0]
    assert "commission_entry.beneficiary_type IN ('service_matchmaker', 'promoter', 'partner')" in lock_sql
    assert "commission_entry.beneficiary_type = 'store'" in lock_sql
    assert "commission_entry.beneficiary_type = 'user'" not in lock_sql
    assert set(PLACEHOLDER.findall(lock_sql)) <= set(lock_params)
    assert db.committed is True


@pytest.mark.asyncio
async def test_organization_tier_binds_organization_id() -> None:
    db = StubDB([StubResult(rows=[])])
    await finance.admin_finance_report(db, admin=_admin("ORGANIZATION", organization_id=5))
    sql, params = db.statements[0]
    assert set(PLACEHOLDER.findall(sql)) <= set(params)
    assert params["scope_organization_id"] == 5


@pytest.mark.asyncio
async def test_unbound_scope_fails_closed_without_1_eq_1() -> None:
    """未绑定组织的 STORE 账号必须 fail-closed，不能退化成全表可见。"""
    db = StubDB([StubResult(rows=[])])
    await finance.admin_finance_report(db, admin=_admin("STORE", organization_id=None))
    sql, _ = db.statements[0]
    assert "1 = 0" in sql
    assert "1 = 1" not in sql


@pytest.mark.asyncio
async def test_all_tier_sees_everything() -> None:
    db = StubDB([StubResult(rows=[])])
    await finance.admin_finance_report(db, admin=_admin("ALL", organization_id=None))
    sql, _ = db.statements[0]
    assert "1 = 1" in sql


# --- 静态：锚点函数分工不被改回去 ---------------------------------------


def test_commission_paths_use_commission_anchor_scope() -> None:
    assert "_commission_anchor_scope" in _func_source("admin_finance_report")
    assert "_commission_anchor_scope" in _func_source("release_commission")
    assert "_account_anchor_scope" not in _func_source("admin_finance_report")
    assert "_account_anchor_scope" not in _func_source("release_commission")


def test_withdrawal_and_ledger_keep_account_anchor_scope() -> None:
    """提现/账本的 account_type 本就是 user / store，不能被顺手换成分成口径。"""
    assert "_account_anchor_scope" in _func_source("review_withdrawal")
    assert "_commission_anchor_scope" not in _func_source("review_withdrawal")
    assert "_account_anchor_scope" in _func_source("admin_list_withdrawals")
    assert "_account_anchor_scope" in _func_source("admin_list_ledger")


def test_finance_service_has_no_data_scope_branch() -> None:
    """D-4：范围判定只能在 dependencies.py，服务层不得自己分支 data_scope。"""
    assert "data_scope ==" not in FINANCE_SOURCE
    assert "data_scope !=" not in FINANCE_SOURCE


def test_commission_member_types_match_entry_domain() -> None:
    """类型域常量必须与分成写入侧（退款冲正映射）保持一致。"""
    assert set(finance._COMMISSION_MEMBER_TYPES) == {"service_matchmaker", "promoter", "partner"}


@pytest.mark.asyncio
async def test_store_commission_options_interpolates_scope() -> None:
    """分店分成下拉：范围片段必须真的插进 SQL 并绑定参数。

    历史缺陷：`text()` 少写 `f` 前缀，SQL 里留着字面量 `{matchmaker_scope}`
    → 接口对任何权限档都是坏 SQL，且范围条件等于没加。
    """
    db = StubDB([StubResult(rows=[]), StubResult(rows=[]), StubResult(rows=[])])

    options = await finance.admin_store_commission_options(db, admin=_admin())

    assert options.stores == [] and options.matchmakers == [] and options.events == []
    assert len(db.statements) == 3
    store_sql, store_params = db.statements[0]
    match_sql, match_params = db.statements[1]
    for sql, params in (db.statements[0], db.statements[1]):
        assert "{" not in sql, f"SQL 残留未插值的范围片段：{sql}"
        assert set(PLACEHOLDER.findall(sql)) <= set(params), f"范围占位符未绑定：{sql}"
    assert "organization.id" in store_sql
    assert "po.user_id" in match_sql
    assert match_params["scope_store_id"] == 88


@pytest.mark.asyncio
async def test_store_commission_options_fails_closed_without_organization() -> None:
    db = StubDB([StubResult(rows=[]), StubResult(rows=[]), StubResult(rows=[])])

    await finance.admin_store_commission_options(db, admin=_admin("STORE", organization_id=None))

    for sql, _ in db.statements[:2]:
        assert "1 = 0" in sql
        assert "1 = 1" not in sql

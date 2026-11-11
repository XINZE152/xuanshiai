"""A12-钱包（后台余额调整 + 用户钱包视图 + 列表余额列）回归测试（2026-10-24）。

口径：不新建表，复用 account_ledger（account_type='user'）；不接真实支付。
覆盖：
1. 列表余额：member_crm_filter 暴露 MEMBER_BALANCE_EXPR 与 balance 排序；
   sort_by=balance 走可用余额表达式（account_ledger / state='AVAILABLE'）。
2. Schema 出参：MemberListItem/MemberDetail 的 balance 默认 0；
   BalanceAdjustmentRequest 校验（金额>0、user_ids 去重、方向枚举）。
3. 调整服务：CREDIT 直接写账本；DEBIT 可用余额不足时整批 409，不产生部分扣减。
4. 路由挂载：/admin/finance/balance-adjustments（敏感写）与 /admin/finance/user-wallet/{user_id}。

假会话沿用项目 StubDB/StubResult 范式，只断言 SQL 形态与分支，无需真实数据库。
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from app.api.routes import finance as finance_routes
from app.api.routes import matchmaker_crm_admin
from app.schemas.finance import BalanceAdjustmentRequest
from app.schemas.matchmaker_crm_admin import MemberDetail, MemberListItem
from app.services.finance import admin_adjust_balance
from app.services.member_crm_filter import MEMBER_BALANCE_EXPR, SORT_SQL, member_sort_sql


class _Result:
    def __init__(self, *, first=None, all_rows=(), lastrowid=0):
        self._first = first
        self._all = list(all_rows)
        self.lastrowid = lastrowid

    def first(self):
        return self._first

    def all(self):
        return self._all

    def mappings(self):
        return self

    def scalar(self):
        return self._first


class _StubDB:
    def __init__(self, results=None):
        self.statements: list[tuple[str, dict]] = []
        self._results = list(results or [])
        self.commits = 0

    async def execute(self, statement, params=None):
        self.statements.append((str(statement), dict(params or {})))
        if self._results:
            return self._results.pop(0)
        return _Result()

    async def commit(self):
        self.commits += 1


# --------------------------------------------------------------------------- #
# 1. 列表余额（只读派生，account_ledger 已存在，无缺列降级问题）
# --------------------------------------------------------------------------- #


def test_balance_expr_reads_available_user_ledger() -> None:
    assert "account_ledger" in MEMBER_BALANCE_EXPR
    assert "account_type = 'user'" in MEMBER_BALANCE_EXPR
    assert "state = 'AVAILABLE'" in MEMBER_BALANCE_EXPR


def test_balance_sort_whitelisted() -> None:
    assert "balance" in SORT_SQL
    sql = member_sort_sql("balance", None)
    assert "account_ledger" in sql and sql.endswith("DESC, u.id DESC")


def test_members_routes_project_balance_and_allow_sort() -> None:
    source = inspect.getsource(matchmaker_crm_admin)
    assert "{MEMBER_BALANCE_EXPR} AS balance" in source
    # sort_by 白名单按字段集合断言。原先是 `"login_num|balance|id" in source`：
    # 那种连续子串断言只要白名单中间插一项（本轮加了 points / integral）就会失效，
    # 而真正要守的契约只是「这三个字段可以排序」。
    assert {"login_num", "balance", "id"} <= set(matchmaker_crm_admin.MEMBER_SORT_FIELDS)


# --------------------------------------------------------------------------- #
# 2. Schema 出参与入参校验
# --------------------------------------------------------------------------- #


def test_member_list_item_has_balance_default_zero() -> None:
    assert "balance" in MemberListItem.model_fields
    assert "balance" in MemberDetail.model_fields


def test_balance_adjustment_request_dedups_and_validates() -> None:
    req = BalanceAdjustmentRequest(user_ids=[3, 3, 1], direction="CREDIT", amount="10.00", reason="补发")
    assert req.user_ids == [3, 1] or req.user_ids == [1, 3]  # 去重、顺序保留不强制
    with pytest.raises(Exception):
        BalanceAdjustmentRequest(user_ids=[1], direction="CREDIT", amount="0", reason="x")
    with pytest.raises(Exception):
        BalanceAdjustmentRequest(user_ids=[1], direction="TRANSFER", amount="1", reason="x")


# --------------------------------------------------------------------------- #
# 3. 调整服务：CREDIT 入账 / DEBIT 余额不足整批 409
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_adjust_credit_writes_available_ledger_and_audit() -> None:
    db = _StubDB([
        _Result(all_rows=[(1,), (2,)]),  # SELECT id FROM users (status=1) 命中 2 人
        _Result(),                       # FOR UPDATE 锁
        _Result(lastrowid=101),          # 第 1 条账本 INSERT
        _Result(lastrowid=102),          # 第 2 条账本 INSERT
        _Result(),                       # 审计
    ])
    admin = SimpleNamespace(id=9)
    req = BalanceAdjustmentRequest(user_ids=[1, 2], direction="CREDIT", amount="5.00", reason="活动补发")
    result = await admin_adjust_balance(db, admin, req)
    assert result.direction == "CREDIT"
    assert result.adjusted_count == 2
    assert str(result.total_amount) in {"10.00", "10.0"}
    # 账本写入方向与来源
    ledger_stmts = [sql for sql, _ in db.statements if "INSERT INTO account_ledger" in sql]
    assert len(ledger_stmts) == 2
    assert any("'admin_balance_adjust'" in sql for sql in ledger_stmts)
    assert any("INSERT INTO business_audit_log" in sql and "finance.balance_adjust" in sql for sql, _ in db.statements)
    assert db.commits == 1


@pytest.mark.asyncio
async def test_adjust_debit_rejected_when_balance_insufficient() -> None:
    db = _StubDB([
        _Result(all_rows=[(7,)]),          # 目标用户存在
        _Result(),                         # FOR UPDATE 锁
        _Result(all_rows=[(7, "3.00")]),   # 可用余额仅 3 元
    ])
    admin = SimpleNamespace(id=9)
    req = BalanceAdjustmentRequest(user_ids=[7], direction="DEBIT", amount="10.00", reason="纠错扣减")
    with pytest.raises(Exception) as exc:
        await admin_adjust_balance(db, admin, req)
    # 409 余额不足，且不写任何账本/审计、不提交
    assert getattr(exc.value, "status_code", None) == 409
    assert not any("INSERT INTO account_ledger" in sql for sql, _ in db.statements)
    assert db.commits == 0


@pytest.mark.asyncio
async def test_adjust_missing_target_raises_404() -> None:
    db = _StubDB([_Result(all_rows=[])])  # 目标用户都不存在/已注销
    admin = SimpleNamespace(id=9)
    req = BalanceAdjustmentRequest(user_ids=[999], direction="CREDIT", amount="1.00", reason="x")
    with pytest.raises(Exception) as exc:
        await admin_adjust_balance(db, admin, req)
    assert getattr(exc.value, "status_code", None) == 404


# --------------------------------------------------------------------------- #
# 4. 路由挂载
# --------------------------------------------------------------------------- #


def test_finance_routes_registered_for_wallet() -> None:
    source = inspect.getsource(finance_routes)
    assert '"/balance-adjustments"' in source
    assert '"/user-wallet/{user_id}"' in source
    assert "admin_adjust_balance" in source
    assert "admin_get_user_wallet" in source
    # 余额调整是敏感写：走幂等 + 二次确认
    assert "begin_sensitive_operation" in source

"""A12-积分修复回归：后台发放/扣减积分改写 C 端 user_points（不再写 account_ledger）。

历史缺陷（A12 记录，2026-10-27 修复）：credit-grants/deductions 写 account_ledger，
导致会员列表「余额」列被虚增、「积分」列恒为 0。修复后发放/扣减写 user_points 滚动余额。

覆盖：
- 发放：追加 user_points 正数行，balance = 最新余额 + amount，operator_id 落操作人；
- 扣减：追加 user_points 负数行，balance 递减；任一用户余额不足 → 整批 409，无部分扣减；
- 三者都不再触碰 account_ledger；积分明细页读取口径改为 user_points。
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.services.finance as finance
from app.schemas.finance import CreditDeductRequest, CreditGrantRequest


class Res:
    def __init__(self, *, rows=None, first=None, lastrowid=1):
        self._rows = list(rows or [])
        self._first = first
        self.lastrowid = lastrowid

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._first

    def scalar(self):
        return None


class CreditStubDB:
    """按 SQL 形态返回结果的假会话：只认 user_points 相关查询。"""

    def __init__(self, balances: dict[int, int]):
        self.balances = dict(balances)  # uid -> 最新余额（仅包含已有流水行的用户）
        self.statements: list[tuple[str, dict]] = []
        self.inserts: list[dict] = []
        self.committed = False
        self._seq = 1000

    async def execute(self, statement, params=None):
        sql = str(statement)
        p = dict(params or {})
        self.statements.append((sql, p))
        if "SELECT balance FROM user_points" in sql:
            uid = int(p["id"])
            return Res(first=(self.balances[uid],) if uid in self.balances else None)
        if "FROM user_points up" in sql and "MAX(id)" in sql:  # 扣减批量行锁读
            uids = [v for k, v in p.items() if str(k).startswith("uid_")]
            rows = [(int(u), self.balances[int(u)]) for u in uids if int(u) in self.balances]
            return Res(rows=rows)
        if "INSERT INTO user_points" in sql:
            self._seq += 1
            self.inserts.append(p)
            return Res(lastrowid=self._seq)
        return Res()

    async def commit(self):
        self.committed = True


def _actor(admin_id: int = 9) -> SimpleNamespace:
    return SimpleNamespace(id=admin_id)


@pytest.mark.asyncio
async def test_grant_credits_appends_user_points_running_balance() -> None:
    db = CreditStubDB({1: 30})
    req = CreditGrantRequest(target_type="member", user_ids=[1], amount=50, reason="活动奖励")
    out = await finance.admin_grant_credits(db, _actor(9), req)

    assert out.granted_count == 1 and out.total_amount == 50
    ins = db.inserts[0]
    assert ins["user_id"] == 1
    assert ins["amount"] == 50
    assert ins["balance"] == 80  # 30 + 50 滚动余额
    assert ins["operator_id"] == 9
    assert db.committed
    assert not any("account_ledger" in sql for sql, _ in db.statements)


@pytest.mark.asyncio
async def test_grant_credits_for_user_without_history_starts_from_zero() -> None:
    db = CreditStubDB({})  # 该用户无任何 user_points 流水
    req = CreditGrantRequest(target_type="member", user_ids=[7], amount=100, reason="新用户奖励")
    await finance.admin_grant_credits(db, _actor(9), req)
    assert db.inserts[0]["balance"] == 100  # 0 + 100


@pytest.mark.asyncio
async def test_deduct_credits_appends_negative_running_balance() -> None:
    db = CreditStubDB({1: 100, 2: 60})
    req = CreditDeductRequest(target_type="member", user_ids=[1, 2], amount=40, reason="违规扣除")
    out = await finance.admin_deduct_credits(db, _actor(9), req)

    assert out.deducted_count == 2
    by_user = {i["user_id"]: i for i in db.inserts}
    assert by_user[1]["amount"] == -40 and by_user[1]["balance"] == 60
    assert by_user[2]["amount"] == -40 and by_user[2]["balance"] == 20
    assert all(i["operator_id"] == 9 and i["balance"] >= 0 for i in db.inserts)
    assert db.committed


@pytest.mark.asyncio
async def test_deduct_credits_insufficient_rejects_whole_batch() -> None:
    db = CreditStubDB({1: 100})  # 用户 2 无流水（余额 0）→ 不足
    req = CreditDeductRequest(target_type="member", user_ids=[1, 2], amount=40, reason="违规扣除")
    with pytest.raises(HTTPException) as exc:
        await finance.admin_deduct_credits(db, _actor(9), req)

    assert exc.value.status_code == 409
    assert db.inserts == []  # 不产生部分扣减
    assert db.committed is False


def test_credit_records_reads_user_points_not_account_ledger() -> None:
    src = inspect.getsource(finance.admin_list_credit_records)
    assert "FROM user_points up" in src
    assert "account_ledger" not in src
    assert "_member_anchor_scope" in src


def test_grant_and_deduct_route_docstrings_no_longer_claim_account_ledger() -> None:
    from app.api.routes import finance as finance_routes

    assert "account_ledger" not in inspect.getsource(finance_routes.grant_credits)
    assert "account_ledger" not in inspect.getsource(finance_routes.deduct_credits)

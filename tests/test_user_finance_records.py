from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.finance import list_my_withdrawals, list_user_finance_records


class Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class DB:
    def __init__(self, rows):
        self.rows = rows
        self.statements = []

    async def scalar(self, sql, params):
        self.statements.append((str(sql), params))
        return len(self.rows)

    async def execute(self, sql, params):
        self.statements.append((str(sql), params))
        return Result(self.rows)


@pytest.mark.asyncio
async def test_balance_records_are_scoped_and_show_withdrawal_status():
    db = DB([{'id': 1, 'amount': '20', 'direction': 'DEBIT', 'state': 'AVAILABLE',
              'source_type': 'withdrawal', 'created_at': datetime(2026, 11, 17), 'status': 'PENDING_REVIEW'}])
    result = await list_user_finance_records(db, 17, 'balance', 1, 20)
    assert result.items[0].title == '提现申请'
    assert result.items[0].status == 'PENDING_REVIEW'
    assert all(params['user_id'] == 17 for _, params in db.statements)
    assert all("al.account_type='user' AND al.account_id=:user_id" in sql for sql, _ in db.statements)
    assert 'idempotency_key' not in db.statements[1][0]


@pytest.mark.asyncio
async def test_expenses_only_include_paid_non_mock_orders_for_current_user():
    db = DB([])
    result = await list_user_finance_records(db, 17, 'expense', 2, 20)
    assert result.items == [] and not result.has_more
    assert all(params['user_id'] == 17 for _, params in db.statements)
    assert 'po.pay_time IS NOT NULL' in db.statements[1][0]
    assert "<> 'mock'" in db.statements[1][0]
    assert db.statements[1][1]['offset'] == 20


def test_finance_history_requires_login():
    client = TestClient(app)
    assert client.get('/api/v1/finance/ledger').status_code == 401
    assert client.get('/api/v1/finance/expenses').status_code == 401
    assert client.get('/api/v1/finance/withdrawals?page=2&page_size=20').status_code == 401


@pytest.mark.asyncio
async def test_withdrawals_are_scoped_and_paged():
    db = DB([])
    assert await list_my_withdrawals(db, 17, 3, 20) == []
    sql, params = db.statements[0]
    assert "account_type = 'user' AND account_id = :user_id" in sql
    assert params == {'user_id': 17, 'limit': 20, 'offset': 40}

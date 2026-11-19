"""⑥ 服务红娘手动录入分成（2026-10-28；2026-11-14 调整账本契约）。

覆盖：
- 服务层 admin_create_commission_entry 写 commission_entry（PENDING/manual/service_matchmaker）
  + account_ledger 待结算余额明细（CREDIT/PENDING、source_type='commission'、
  键 ledger:commission:{entry_id}）+ business_audit_log；
- 释放结算 release_commission 将同一条账本行翻转 AVAILABLE（此后计入红娘可提现余额）；
- 分成事件（rule）须为 service_matchmaker 且启用，否则 400；
- 红娘账号不存在 → 400；
- 路由注册 + 未登录 401（写操作另受 finance.write 约束）。
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.services.finance as finance
from app.main import app
from app.schemas.finance import MatchmakerCommissionEntryCreate

client = TestClient(app)

BASE = "/api/v1/admin/finance/commission-entries"


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

    def one(self):
        return self._first


class StubDB:
    """按 SQL 形态返回结果的假会话，记录所有写调用。"""

    def __init__(self, *, users_exist=True, rule=None, entry_row=None):
        self.users_exist = users_exist
        self.rule = rule
        self.entry_row = entry_row or {
            "id": 555,
            "created_at": datetime(2026, 10, 28, 12, 0, 0),
            "beneficiary_id": 42,
            "rule_id": 7,
            "base_amount": "100.00",
            "amount": "30.00",
            "status": "PENDING",
            "source": "manual",
            "remark": "线下活动分成",
            "matchmaker_name": "红娘小美",
        }
        self.statements: list[tuple[str, dict]] = []
        self.committed = False

    async def scalar(self, statement, params=None):
        sql = str(statement)
        self.statements.append((sql, dict(params or {})))
        if "SELECT id FROM users" in sql:
            return 42 if self.users_exist else None
        return None

    async def execute(self, statement, params=None):
        sql = str(statement)
        p = dict(params or {})
        self.statements.append((sql, p))
        if "FROM commission_rule" in sql:
            return Res(first=self.rule)
        if "INSERT INTO commission_entry" in sql:
            return Res(lastrowid=555)
        if "FROM commission_entry ce LEFT JOIN users" in sql:
            return Res(first=self.entry_row)
        return Res()

    async def commit(self):
        self.committed = True


def _actor(admin_id: int = 9) -> SimpleNamespace:
    return SimpleNamespace(id=admin_id)


@pytest.mark.asyncio
async def test_create_writes_entry_ledger_and_audit() -> None:
    db = StubDB(rule={"id": 7, "name": "撮合成功奖", "version": 2})
    body = MatchmakerCommissionEntryCreate(
        matchmaker_user_id=42, rule_id=7, amount="30.00", base_amount="100.00",
        consumer_user_id=88, remark="线下活动分成",
    )
    out = await finance.admin_create_commission_entry(db, _actor(9), body)

    assert db.committed
    assert out.entry.id == 555
    assert out.entry.status == "PENDING"
    assert out.entry.source == "manual"
    assert out.entry.matchmaker_id == 42
    assert out.entry.amount == "30.00"
    assert out.entry.rule_name == "撮合成功奖"

    inserts = [sql for sql, _ in db.statements if sql.strip().upper().startswith("INSERT")]
    assert any("commission_entry" in s for s in inserts)
    assert any("business_audit_log" in s for s in inserts)
    # 2026-11-14 契约：录入即生成待结算余额明细，与订单结算同键位，释放时翻转 AVAILABLE。
    ledger_sql, ledger_params = next((sql, p) for sql, p in db.statements if "INSERT INTO account_ledger" in sql)
    assert "'CREDIT'" in ledger_sql and "'PENDING'" in ledger_sql and "'commission'" in ledger_sql
    assert ledger_params["account_id"] == 42
    assert ledger_params["amount"] == "30.00"
    assert ledger_params["source_id"] == 555
    assert ledger_params["key"] == "ledger:commission:555"

    entry_insert_params = next(p for sql, p in db.statements if "INSERT INTO commission_entry" in sql)
    assert entry_insert_params["beneficiary_id"] == 42
    assert "service_matchmaker" in next(sql for sql, _ in db.statements if "INSERT INTO commission_entry" in sql)


@pytest.mark.asyncio
async def test_release_flips_manual_entry_ledger_to_available() -> None:
    pending = {
        "id": 555, "order_id": None, "beneficiary_type": "service_matchmaker", "beneficiary_id": 42,
        "base_amount": "100.00", "amount": "30.00", "status": "PENDING",
        "created_at": datetime(2026, 11, 14, 12, 0, 0),
    }
    available = {**pending, "status": "AVAILABLE"}

    class ReleaseDB(StubDB):
        async def execute(self, statement, params=None):
            sql = str(statement)
            p = dict(params or {})
            self.statements.append((sql, p))
            if "FROM commission_entry" in sql and "FOR UPDATE" in sql:
                return Res(first=pending)
            if "FROM commission_entry" in sql:
                return Res(first=available)
            return Res()

    db = ReleaseDB()
    out = await finance.release_commission(db, _actor(9), 555)
    assert out.status == "AVAILABLE"
    assert out.order_id is None  # 手工录入无关联订单，响应契约允许 NULL
    assert db.committed
    update_sql, update_params = next((sql, p) for sql, p in db.statements if "UPDATE account_ledger" in sql)
    assert "state = 'AVAILABLE'" in update_sql
    assert "source_type = 'commission'" in update_sql
    assert update_params["entry_id"] == 555


@pytest.mark.asyncio
async def test_matchmaker_not_found_raises_400() -> None:
    db = StubDB(users_exist=False)
    body = MatchmakerCommissionEntryCreate(matchmaker_user_id=999, amount="10.00")
    with pytest.raises(HTTPException) as exc:
        await finance.admin_create_commission_entry(db, _actor(9), body)
    assert exc.value.status_code == 400
    assert not db.committed


@pytest.mark.asyncio
async def test_disabled_or_mismatched_rule_raises_400() -> None:
    db = StubDB(rule=None)  # commission_rule 查询返回空 → 视为不存在/停用
    body = MatchmakerCommissionEntryCreate(matchmaker_user_id=42, rule_id=7, amount="10.00")
    with pytest.raises(HTTPException) as exc:
        await finance.admin_create_commission_entry(db, _actor(9), body)
    assert exc.value.status_code == 400


def test_route_registered_and_requires_authentication() -> None:
    spec = client.get("/openapi.json").json()["paths"]
    assert "post" in spec[BASE]
    assert client.post(BASE, json={}).status_code == 401

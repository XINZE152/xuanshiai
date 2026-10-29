"""② 推广红娘手动录入分成（2026-10-29）：补齐缺失的 POST /admin/promoters/commission-entries。

覆盖：
- 服务层 create_commission_entry 只写 commission_entry（PENDING/manual/promoter）
  + business_audit_log，绝不写 account_ledger（不凭空增加可提现余额）；
- 分成事件（rule）须为 promoter 且启用，否则 400；
- 推广红娘身份不存在 → 400；
- 路由注册 + 未登录 401（写操作另受 finance.write 约束）。
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.services.promoter_staff_admin as service
from app.main import app
from app.schemas.promoter_staff_admin import PromoterCommissionEntryCreate

client = TestClient(app)

BASE = "/api/v1/admin/promoters/commission-entries"


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

    def __init__(self, *, promoter_exists=True, rule=None, entry_row=None):
        self.promoter_exists = promoter_exists
        self.rule = rule
        self.entry_row = entry_row or {
            "id": 777,
            "created_at": datetime(2026, 10, 29, 12, 0, 0),
            "beneficiary_id": 42,
            "rule_id": 7,
            "base_amount": "100.00",
            "amount": "30.00",
            "status": "PENDING",
            "event_name": "推广红娘入伙",
            "promoter_name": "推广小美",
            "promoter_avatar": None,
        }
        self.statements: list[tuple[str, dict]] = []
        self.committed = False

    async def scalar(self, statement, params=None):
        sql = str(statement)
        self.statements.append((sql, dict(params or {})))
        if "SELECT id FROM users" in sql:
            return 88
        return None

    async def execute(self, statement, params=None):
        sql = str(statement)
        p = dict(params or {})
        self.statements.append((sql, p))
        if "FROM commission_rule" in sql:
            return Res(first=self.rule)
        if "FROM user_matchmaker_apply ma" in sql:
            return Res(first={"user_id": 42} if self.promoter_exists else None)
        if "INSERT INTO commission_entry" in sql:
            return Res(lastrowid=777)
        if "FROM commission_entry ce" in sql:
            return Res(first=self.entry_row)
        return Res()

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_create_writes_entry_and_audit_but_never_ledger() -> None:
    db = StubDB(rule={"id": 7, "name": "推广红娘入伙", "version": 1})
    body = PromoterCommissionEntryCreate(
        promoter_user_id=42, rule_id=7, amount="30.00", base_amount="100.00",
        consumer_user_id=88, remark="线下推广分成",
    )
    out = await service.create_commission_entry(db, 9, body)

    assert db.committed
    assert out.entry.id == 777
    assert out.entry.status == "PENDING"
    assert out.entry.promoter_id == 42
    assert out.entry.amount == "30.00"
    assert out.entry.event_name == "推广红娘入伙"

    inserts = [sql for sql, _ in db.statements if sql.strip().upper().startswith("INSERT")]
    assert any("commission_entry" in s for s in inserts)
    assert any("business_audit_log" in s for s in inserts)
    # 核心红线：绝不动余额账本
    assert not any("account_ledger" in sql for sql, _ in db.statements)

    entry_insert_sql = next(sql for sql, _ in db.statements if "INSERT INTO commission_entry" in sql)
    assert "promoter" in entry_insert_sql
    entry_insert_params = next(p for sql, p in db.statements if "INSERT INTO commission_entry" in sql)
    assert entry_insert_params["beneficiary_id"] == 42


@pytest.mark.asyncio
async def test_promoter_not_found_raises_400() -> None:
    db = StubDB(promoter_exists=False)
    body = PromoterCommissionEntryCreate(promoter_user_id=999, amount="10.00")
    with pytest.raises(HTTPException) as exc:
        await service.create_commission_entry(db, 9, body)
    assert exc.value.status_code == 400
    assert not db.committed


@pytest.mark.asyncio
async def test_disabled_or_mismatched_rule_raises_400() -> None:
    db = StubDB(rule=None)  # commission_rule 查询返回空 → 视为不存在/停用
    body = PromoterCommissionEntryCreate(promoter_user_id=42, rule_id=7, amount="10.00")
    with pytest.raises(HTTPException) as exc:
        await service.create_commission_entry(db, 9, body)
    assert exc.value.status_code == 400
    assert not db.committed


@pytest.mark.asyncio
async def test_null_rule_allows_entry_without_event() -> None:
    db = StubDB()
    body = PromoterCommissionEntryCreate(promoter_user_id=42, amount="10.00")
    out = await service.create_commission_entry(db, 9, body)
    assert db.committed
    assert out.entry.id == 777


def test_route_registered_and_requires_authentication() -> None:
    spec = client.get("/openapi.json").json()["paths"]
    assert "post" in spec[BASE]
    assert client.post(BASE, json={}).status_code == 401

"""批量导入客源的行级手机号口径回归。

业务口径：录入线索必须留得下能联系到人的手机号（只有手机号能一键入库成会员），
所以导入的每一行都必须带 11 位有效手机号；缺号或格式错的行单独失败并给出可读行号，
不得阻断同批其余行，也不得把微信号当手机号兜底。
"""

from __future__ import annotations

import pytest

from app.schemas.customer_lead_admin import CustomerLeadImportRow
from app.services.customer_lead_admin import _insert_leads


class _StubResult:
    def __init__(self, *, rows=None, scalar=None, lastrowid=0):
        self._rows = list(rows or [])
        self._scalar = scalar
        self.lastrowid = lastrowid

    def all(self):
        return list(self._rows)

    def scalar(self):
        return self._scalar

    def mappings(self):
        return self


class _StubDB:
    def __init__(self, results: list[_StubResult] | None = None) -> None:
        self.statements: list[tuple[str, dict]] = []
        self._results = list(results or [])
        self._next_id = 100

    async def execute(self, statement, params=None):
        self.statements.append((str(statement), dict(params or {})))
        if self._results:
            return self._results.pop(0)
        # 兜底：既当「无冲突」的查询结果，又当带 lastrowid 的 INSERT 结果。
        self._next_id += 1
        return _StubResult(lastrowid=self._next_id)

    async def commit(self) -> None:
        self.committed = True


def _row(**overrides) -> CustomerLeadImportRow:
    payload = {"name": "张女士", "phone": "13800000000", "source": "抖音"}
    payload.update(overrides)
    return CustomerLeadImportRow(**payload)


def _inserted_params(db: _StubDB) -> list[dict]:
    return [params for sql, params in db.statements if sql.strip().startswith("INSERT INTO customer_lead")]


@pytest.mark.asyncio
async def test_import_row_without_phone_fails_and_keeps_the_rest() -> None:
    db = _StubDB([_StubResult(scalar=None)])
    summary = await _insert_leads(db, 1, [
        _row(),
        _row(phone=None, wechat="wx_only"),
        _row(phone="  139****0101  "),
    ])
    assert (summary.created, summary.skipped, summary.failed) == (1, 0, 2)
    assert "第 3 行" in summary.errors[0] and "缺少手机号" in summary.errors[0]
    assert "第 4 行" in summary.errors[1] and "11 位" in summary.errors[1]
    # 只有合规行落库，且落的是归一化后的手机号
    assert len(_inserted_params(db)) == 1
    assert _inserted_params(db)[0]["phone"] == "13800000000"


@pytest.mark.asyncio
async def test_import_row_phone_is_trimmed_before_the_duplicate_check() -> None:
    db = _StubDB([_StubResult(scalar=None)])
    summary = await _insert_leads(db, 1, [_row(phone=" 13800000000 ")])
    assert summary.created == 1
    duplicate_check = next(params for sql, params in db.statements if "SELECT id FROM customer_lead" in sql)
    assert duplicate_check["phone"] == "13800000000"


@pytest.mark.asyncio
async def test_import_row_with_malformed_phone_never_reaches_sql() -> None:
    db = _StubDB()
    summary = await _insert_leads(db, 1, [_row(phone="138-0000-0000"), _row(phone="23800000000")])
    assert (summary.created, summary.failed) == (0, 2)
    # 坏号行一条写入 SQL 都不能发出去；列探测（information_schema）是只读的，允许发生。
    assert [sql for sql, _ in db.statements if not sql.strip().startswith("SELECT COLUMN_NAME")] == []

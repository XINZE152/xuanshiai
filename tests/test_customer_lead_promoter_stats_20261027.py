"""推广红娘获客统计 SQL 列歧义回归（2026-10-27 生产 1052 事故）。

背景：`promoter_acquisition_stats` 的查询 `FROM customer_lead cl LEFT JOIN users u`
中，`gender/status/promoter_id` 等列在两表中同名但裸写，MySQL 报
`1052 Column 'gender' in field list is ambiguous`，接口整段 500。

本文件冻结修复后的形状：所有 customer_lead 列引用必须带 `cl.` 前缀，
且不得再出现裸 `gender`（`u.` 前缀的用户表列除外）。
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from app.services.customer_lead_admin import promoter_acquisition_stats


class _Result:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self._rows = list(rows or [])

    def all(self):
        return list(self._rows)

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class RecordingDB:
    """按 SQL 内容分流：information_schema 探测返回列清单，主查询记录 SQL。"""

    def __init__(self, lead_columns: list[str]) -> None:
        self.lead_columns = lead_columns
        self.main_sql = ""
        self.params: dict[str, Any] = {}

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "information_schema" in sql:
            return _Result([(name,) for name in self.lead_columns])
        self.main_sql = sql
        self.params = dict(params or {})
        return _Result([])


@pytest.mark.asyncio
async def test_promoter_stats_qualifies_gender_column() -> None:
    """gender 相关聚合必须限定在 cl. 上，杜绝 1052 歧义。"""
    db = RecordingDB(["gender", "deleted_at", "audit_status", "status", "promoter_id"])
    await promoter_acquisition_stats(db, promoter_id=3)

    sql = db.main_sql
    assert "SUM(cl.gender = 1)" in sql
    assert "SUM(cl.gender = 2)" in sql
    # 主查询里不允许出现任何裸 gender（无 cl./u. 前缀）
    assert re.search(r"(?<![.\w])gender", sql.replace("cl.gender", "").replace("u.gender", "")) is None
    # WHERE/GROUP BY 中的同名列同样加前缀
    assert "cl.promoter_id = :promoter_id" in sql
    assert "cl.deleted_at IS NULL" in sql
    assert "GROUP BY cl.promoter_id" in sql
    assert db.params["promoter_id"] == 3


@pytest.mark.asyncio
async def test_promoter_stats_without_gender_column_falls_back() -> None:
    """customer_lead 未迁移 gender 列时不引用 cl.gender，占位常量为 0。"""
    db = RecordingDB(["deleted_at", "audit_status", "status", "promoter_id"])
    await promoter_acquisition_stats(db)

    sql = db.main_sql
    assert "gender" not in sql.replace("u.gender", "")
    assert "0 male, 0 female, 0 valid_male, 0 valid_female" in sql

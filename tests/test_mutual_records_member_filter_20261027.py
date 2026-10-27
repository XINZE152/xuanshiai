"""2026-10-27 互选记录（/admin/mutual-records）新增「发起方」会员资料筛选回归。

补齐真后端缺口 #3：此前只支持 activity_id/actor_id/keyword/result，无收入/年龄/学历。
新增参数一律作用于发起方 from_user（fu），与会员 CRM 列表同口径：
- 年龄：users.birthday 的 TIMESTAMPDIFF；
- 收入：user_profile.income 数值区间（前端把「小于10w / 10-20w / …」档位换算成 min/max）；
- 保密：income_secret=true → income 为 NULL（未填写）；
- 学历：user_auth.education 多选（英文逗号分隔）。

无需 DB：桩库捕获 SQL 与绑定参数，另用 OpenAPI 校验参数已暴露。
"""

from __future__ import annotations

import pytest

from app.main import app
from app.services import mutual_selection_admin as service

RECORD_PATH = "/api/v1/admin/mutual-records"


class _Result:
    def mappings(self) -> "_Result":
        return self

    def all(self) -> list:
        return []

    def scalar(self) -> int:
        return 0


class _StubDB:
    def __init__(self) -> None:
        self.statements: list[tuple[str, dict]] = []

    async def execute(self, statement, params=None):  # noqa: ANN001, ANN201
        self.statements.append((str(statement), dict(params or {})))
        return _Result()

    def where_clause(self) -> str:
        # 取第一条 SELECT 明细语句 WHERE 之后的片段。
        select_sql = next(sql for sql, _ in self.statements if sql.lstrip().upper().startswith("SELECT P.*"))
        return select_sql.split(" WHERE ", 1)[1]

    def merged_params(self) -> dict:
        merged: dict = {}
        for _, params in self.statements:
            merged.update(params)
        return merged


@pytest.mark.asyncio
async def test_member_filters_bind_as_params() -> None:
    db = _StubDB()
    await service.list_records(
        db, 1, 20, None, None, None, None,
        age_min=25, age_max=35, income_min=100000, income_max=200000, education="本科,硕士",
    )
    clause = db.where_clause()
    assert "TIMESTAMPDIFF(YEAR, fu.birthday, CURDATE()) >= :age_min" in clause
    assert "TIMESTAMPDIFF(YEAR, fu.birthday, CURDATE()) <= :age_max" in clause
    assert "fp.income >= :income_min" in clause
    assert "fp.income <= :income_max" in clause
    assert "fa.education IN (:edu_0, :edu_1)" in clause
    params = db.merged_params()
    assert params["age_min"] == 25 and params["age_max"] == 35
    assert params["income_min"] == 100000 and params["income_max"] == 200000
    assert params["edu_0"] == "本科" and params["edu_1"] == "硕士"


@pytest.mark.asyncio
async def test_income_secret_filters_null() -> None:
    db = _StubDB()
    await service.list_records(db, 1, 20, None, None, None, None, income_secret=True)
    clause = db.where_clause()
    assert "NOT EXISTS (SELECT 1 FROM user_profile fp WHERE fp.user_id = fu.id AND fp.income IS NOT NULL)" in clause


@pytest.mark.asyncio
async def test_absent_member_filters_add_nothing() -> None:
    db = _StubDB()
    await service.list_records(db, 1, 20, None, None, None, None)
    clause = db.where_clause()
    for token in ("user_profile fp", "user_auth fa", "TIMESTAMPDIFF"):
        assert token not in clause


@pytest.mark.asyncio
async def test_member_filters_apply_to_actor_side_only() -> None:
    """筛选谓词只限定发起方 fu，不得引用被选方 tu 的资料列。"""
    db = _StubDB()
    await service.list_records(db, 1, 20, None, None, None, None, age_min=20, education="大专")
    clause = db.where_clause()
    assert "fu.birthday" in clause
    assert "tu.birthday" not in clause
    assert "fa.user_id = fu.id" in clause


def test_record_route_exposes_member_filter_params() -> None:
    params = {item["name"] for item in app.openapi()["paths"][RECORD_PATH]["get"]["parameters"]}
    assert {"age_min", "age_max", "income_min", "income_max", "income_secret", "education"} <= params

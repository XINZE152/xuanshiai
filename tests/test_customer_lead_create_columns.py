"""后台单条录入/复制资料（POST /admin/customer-leads）的落库列回归。

2026-10-26 修复：拼接 INSERT 时写成 ``VALUES (:{', '.join(columns)})``，冒号只落在第一列上，
其余列名被 MySQL 当成列引用，按列默认值插入 —— 操作员填的手机/来源/意向/备注/出生全部静默丢失。
本文件钉住「录入表单采集的字段逐列绑定」这条契约（复制资料走的是同一个 INSERT）。
不依赖 MySQL：用 StubDB 按 SQL 文本分派结果，走真实 service 逻辑。
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app.schemas.customer_lead_admin import CustomerLeadCreate
from app.services.customer_lead_admin import create_lead

PHONE = "13800000000"
NOW = datetime(2026, 10, 26, 12, 0, 0)
NEW_LEAD_ID = 77

_MIGRATED_COLUMNS = ["id", "name", "real_name", "phone", "wechat", "source", "photo", "gender", "birthday",
                     "intention_level", "status", "audit_status", "matchmaker_id", "organization_id", "promoter_id",
                     "next_follow_at", "converted_user_id", "remark", "created_by", "created_at", "updated_at",
                     "tags_raw", "deleted_at"]


class StubResult:
    def __init__(self, *, first=None, scalar=None, lastrowid=None, rows=None):
        self._first = first
        self._scalar = scalar
        self.lastrowid = lastrowid
        self._rows = rows if rows is not None else []

    def all(self):
        return self._rows

    def scalar(self):
        return self._scalar

    def first(self):
        return self._rows[0] if self._rows else self._first

    def mappings(self):
        return self


class StubDB:
    """按 SQL 特征分派返回值，因此不同分支不会因查询条数不同而错位。"""

    def __init__(self, lead_row: dict, *, migrated_columns: list[str] = _MIGRATED_COLUMNS):
        self.statements: list[tuple[str, dict]] = []
        self.committed = False
        self._lead_row = lead_row
        self._columns = [(name,) for name in migrated_columns]

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append((sql, dict(params or {})))
        if "information_schema.COLUMNS" in sql:
            return StubResult(rows=self._columns)
        if sql.lstrip().startswith("INSERT INTO customer_lead"):
            return StubResult(lastrowid=NEW_LEAD_ID)
        if "SELECT" in sql and "customer_lead" in sql:
            return StubResult(rows=[self._lead_row])
        return StubResult()

    async def scalar(self, statement, params=None):
        self.statements.append((str(statement), dict(params or {})))
        return None

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        pass

    def insert(self) -> tuple[str, dict]:
        return next((sql, params) for sql, params in self.statements if sql.lstrip().startswith("INSERT INTO customer_lead"))


def _lead_row(**overrides) -> dict:
    row = {
        "id": NEW_LEAD_ID, "name": "秦砚舟（示例）", "real_name": "秦某某", "phone": PHONE, "wechat": "wx_123",
        "source": "本地群", "photo": "http://img/1.jpg", "gender": 1, "birthday": date(1995, 5, 5),
        "intention_level": 3, "status": "NEW", "audit_status": "active",
        "matchmaker_id": None, "organization_id": None, "promoter_id": None,
        "next_follow_at": None, "converted_user_id": None, "remark": "希望近期了解牵线服务",
        "created_by": 1, "created_at": NOW, "updated_at": NOW, "tags_raw": None,
    }
    row.update(overrides)
    return row


def _request() -> CustomerLeadCreate:
    return CustomerLeadCreate(
        name="秦砚舟（示例）", real_name="秦某某", phone=PHONE, wechat="wx_123", source="本地群",
        photo="http://img/1.jpg", birthday=date(1995, 5, 5), intention_level=3, gender=1,
        remark="希望近期了解牵线服务",
    )


@pytest.mark.asyncio
async def test_create_binds_every_column_it_writes() -> None:
    """录入表单采集的每个字段都要有自己的 ``:占位符``；少一个冒号就会写进列默认值。"""
    db = StubDB(_lead_row())
    lead = await create_lead(db, 1, _request())
    sql, params = db.insert()
    columns_part, values_part = sql.split("VALUES")
    written = [column.strip() for column in columns_part.split("(", 1)[1].rsplit(")", 1)[0].split(",")]
    placeholders = [token.strip() for token in values_part.strip().lstrip("(").rstrip(")").split(",")]

    assert placeholders == [f":{column}" for column in written]
    assert set(written) >= {"name", "real_name", "phone", "wechat", "source", "intention_level", "remark", "created_by"}
    assert params["phone"] == PHONE
    assert params["source"] == "本地群"
    assert params["intention_level"] == 3
    assert params["remark"] == "希望近期了解牵线服务"
    assert lead.id == NEW_LEAD_ID
    assert db.committed is True


@pytest.mark.asyncio
async def test_create_writes_the_optional_profile_columns() -> None:
    """照片/出生/性别是录入表单里的可填项，列已迁移时必须一起落库（复制资料靠它们搬资料）。"""
    db = StubDB(_lead_row())
    await create_lead(db, 1, _request())
    _, params = db.insert()

    assert params["photo"] == "http://img/1.jpg"
    assert params["birthday"] == date(1995, 5, 5)
    assert params["gender"] == 1


@pytest.mark.asyncio
async def test_create_skips_columns_that_are_not_migrated_yet() -> None:
    """先发版后迁移：缺列时从写入列里剔除，不能拼出 Unknown column 的 500。"""
    db = StubDB(
        _lead_row(photo=None, gender=None, birthday=None),
        migrated_columns=[name for name in _MIGRATED_COLUMNS if name not in ("photo", "gender", "birthday")],
    )
    await create_lead(db, 1, _request())
    sql, params = db.insert()
    columns_part, values_part = sql.split("VALUES")

    for column in ("photo", "gender", "birthday"):
        assert column not in columns_part
        assert f":{column}" not in values_part
    assert params["phone"] == PHONE

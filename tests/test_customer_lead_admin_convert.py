"""后台一键入库（admin/customer-leads/{id}/convert）回归。

2026-10-24 契约变更（合并上游后）：convert 与 assignment 职责分离——
归属不再由 convert 请求体当场确认，改为「线索已有分派 → 操作账号归属」回退，
当场改归属走 PATCH /{lead_id}/assignment。本文件钉住当前契约：
1. 已入库（converted_user_id）→ 幂等回放 (lead, user_id, True)，不重复建号；
2. 已关闭/已弃海的客源 → 409，且不建账号；
3. 无手机号 → 409；手机号不是有效 11 位 → 422，且不建账号；
4. 手机号已是会员 → 409 可读提示（含会员 ID），不抛数据库唯一键异常；
5. 归属回退：线索无分派时取操作账号的门店/红娘，写入 resource_assignment；
6. 四张表写入内容（users / member_review / resource_assignment / customer_lead）；
7. 连点两次入库撞唯一键时转 409，不冒 500；
8. 建号写入 lead_convert_initial_password 的 bcrypt 哈希（后台须知承诺的初始密码），配置为空时不设密码。
全部用 StubDB 按 SQL 文本分派结果，走真实 service 逻辑，不依赖 MySQL。
"""

from __future__ import annotations

import re
from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.security import verify_password
from app.schemas.customer_lead_admin import CustomerLead, CustomerLeadConvertRequest
from app.services.customer_lead_admin import LEAD_SELECT, convert_lead, list_leads

PHONE = "13800000000"
NOW = datetime(2026, 10, 1, 12, 0, 0)
NEW_USER_ID = 4200

_LEAD_COLUMNS = [("id",), ("name",), ("real_name",), ("phone",), ("wechat",), ("source",),
                 ("photo",), ("gender",), ("birthday",), ("intention_level",), ("status",),
                 ("audit_status",), ("matchmaker_id",), ("organization_id",), ("promoter_id",),
                 ("next_follow_at",), ("converted_user_id",), ("remark",), ("created_by",),
                 ("created_at",), ("updated_at",), ("deleted_at",)]


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
        if self._rows:
            return self._rows[0]
        return self._first

    def mappings(self):
        return self


class StubDB:
    """按 SQL 特征分派返回值，因此不同校验分支不会因查询条数不同而错位。"""

    def __init__(self, lead_row: dict, *, phone_taken_by: int | None = None, conflict_on_user_insert: bool = False):
        self.statements: list[tuple[str, dict]] = []
        self.committed = False
        self.rolled_back = False
        self._lead_row = lead_row
        self._phone_taken_by = phone_taken_by
        self._conflict = conflict_on_user_insert

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append((sql, dict(params or {})))
        if "information_schema.COLUMNS" in sql:
            return StubResult(rows=_LEAD_COLUMNS)
        if sql.strip().startswith("SELECT COUNT"):
            return StubResult(scalar=1)
        # 外层 SELECT 的列名清单由 _lead_select_for 按真实列动态拼装（上游 2026-10-28 起
        # real_name 等资料列不再硬写在 LEAD_SELECT 里），所以只按固定前缀识别线索行查询。
        if "SELECT id, name, phone" in sql:
            return StubResult(rows=[self._lead_row])
        if "SELECT phone FROM customer_lead" in sql:
            return StubResult(first=(self._lead_row.get("phone"),))
        if "FROM users WHERE phone" in sql:
            return StubResult(scalar=self._phone_taken_by)
        if "INSERT INTO users" in sql:
            if self._conflict:
                raise IntegrityError(sql, {}, RuntimeError("Duplicate entry for key 'uk_phone'"))
            return StubResult(lastrowid=NEW_USER_ID)
        return StubResult()

    async def scalar(self, statement, params=None):
        sql = str(statement)
        self.statements.append((sql, dict(params or {})))
        if "FROM users WHERE phone" in sql:
            return self._phone_taken_by
        # guard: SELECT id ... FOR UPDATE
        return self._lead_row.get("id")

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    def sql(self) -> str:
        return "\n".join(text for text, _ in self.statements)

    def params_for(self, needle: str) -> dict:
        return next(params for sql, params in self.statements if needle in sql)


def _lead_row(**overrides) -> dict:
    row = {
        "id": 3, "name": "张女士", "real_name": None, "phone": PHONE, "wechat": None,
        "source": "抖音", "photo": None, "gender": 2, "birthday": None,
        "intention_level": 2, "status": "NEW", "audit_status": "active",
        "matchmaker_id": None, "organization_id": None, "promoter_id": None,
        "next_follow_at": None, "converted_user_id": None, "remark": None,
        "created_by": 1, "created_at": NOW, "updated_at": NOW,
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_convert_creates_a_pending_member_and_writes_four_tables() -> None:
    db = StubDB(_lead_row(matchmaker_id=7, organization_id=9))
    lead, user_id, replayed = await convert_lead(
        db, 1, None, None, 3, CustomerLeadConvertRequest()
    )
    assert replayed is False
    assert user_id == NEW_USER_ID
    assert lead.id == 3
    assert db.committed is True

    assert db.params_for("INSERT INTO users")["openid"] == "lead-convert-3"
    assert db.params_for("INSERT INTO users")["phone"] == PHONE
    assert db.params_for("INSERT INTO users")["nickname"] == "张女士"
    assert db.params_for("INSERT INTO users")["gender"] is None
    assert db.params_for("INSERT INTO matchmaker_member_review") == {"user_id": NEW_USER_ID}
    # 归属取线索上已有的分派（route 未传操作账号归属时）
    assert db.params_for("INSERT INTO resource_assignment") == {
        "user_id": NEW_USER_ID, "organization_id": 9, "matchmaker_id": 7, "assigned_by": 1,
    }
    assert "'lead_convert'" in db.sql()
    assert db.params_for("customer_lead.convert")["actor"] == 1


@pytest.mark.asyncio
async def test_convert_writes_the_configured_initial_password_as_a_bcrypt_hash(monkeypatch) -> None:
    """后台须知承诺「初始登录密码 abc123」，入库建号必须真的写 users.password（只存哈希）。"""
    monkeypatch.setattr(settings, "lead_convert_initial_password", "abc123")
    db = StubDB(_lead_row())
    await convert_lead(db, 1, None, None, 3, CustomerLeadConvertRequest())
    params = db.params_for("INSERT INTO users")
    assert params["password_algo"] == "bcrypt"
    assert params["password"] != "abc123"
    assert verify_password("abc123", params["password"]) is True


@pytest.mark.asyncio
async def test_convert_creates_a_passwordless_account_when_the_initial_password_is_blank(monkeypatch) -> None:
    """配置留空表示不给初始密码，新账号只能走验证码/微信登录。"""
    monkeypatch.setattr(settings, "lead_convert_initial_password", "")
    db = StubDB(_lead_row())
    await convert_lead(db, 1, None, None, 3, CustomerLeadConvertRequest())
    params = db.params_for("INSERT INTO users")
    assert params["password"] is None
    assert params["password_algo"] is None


@pytest.mark.asyncio
async def test_convert_replays_when_the_lead_is_already_converted() -> None:
    """已入库客源再次入库：幂等回放已有会员，不重复建号、不写库。"""
    db = StubDB(_lead_row(converted_user_id=88, status="CONVERTED"))
    lead, user_id, replayed = await convert_lead(
        db, 1, None, None, 3, CustomerLeadConvertRequest()
    )
    assert replayed is True
    assert user_id == 88
    assert "INSERT INTO users" not in db.sql()
    assert db.committed is False


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["CLOSED", "LOST"])
async def test_convert_refuses_closed_and_lost_leads(status: str) -> None:
    db = StubDB(_lead_row(status=status))
    with pytest.raises(HTTPException) as err:
        await convert_lead(db, 1, None, None, 3, CustomerLeadConvertRequest())
    assert err.value.status_code == 409
    assert "INSERT INTO users" not in db.sql()
    assert db.committed is False


@pytest.mark.asyncio
async def test_convert_requires_a_phone() -> None:
    db = StubDB(_lead_row(phone=None))
    with pytest.raises(HTTPException) as err:
        await convert_lead(db, 1, None, None, 3, CustomerLeadConvertRequest())
    assert err.value.status_code == 409
    assert "INSERT INTO users" not in db.sql()
    assert db.committed is False


@pytest.mark.asyncio
@pytest.mark.parametrize("phone", [
    "12345678901",  # 首位不是 13-19
    "1380000000",  # 只有 10 位
    "138 0000 0000",  # 带空格
    "wx_zhang",  # 微信号被当成电话录进历史线索
])
async def test_convert_blocks_an_unusable_lead_phone_before_creating_the_member(phone: str) -> None:
    """入库直接拿手机号建会员账号，微信-only/占位号码必须当场挡住，否则留下登不进来的号。"""
    db = StubDB(_lead_row(phone=phone))
    with pytest.raises(HTTPException) as err:
        await convert_lead(db, 1, None, None, 3, CustomerLeadConvertRequest())
    assert err.value.status_code == 422
    assert phone in err.value.detail
    assert "INSERT INTO users" not in db.sql()
    assert db.committed is False


@pytest.mark.asyncio
async def test_convert_reports_existing_member_instead_of_a_unique_key_error() -> None:
    db = StubDB(_lead_row(), phone_taken_by=55)
    with pytest.raises(HTTPException) as err:
        await convert_lead(db, 1, None, None, 3, CustomerLeadConvertRequest())
    assert err.value.status_code == 409
    assert "55" in err.value.detail
    assert "INSERT INTO users" not in db.sql()


@pytest.mark.asyncio
async def test_convert_falls_back_to_the_operator_scope_when_the_lead_has_no_owner() -> None:
    """线索没有分派时，归属回退到操作账号的门店/红娘（route 传入）。"""
    db = StubDB(_lead_row())
    await convert_lead(db, 1, 9, 7, 3, CustomerLeadConvertRequest())
    assert db.params_for("INSERT INTO resource_assignment") == {
        "user_id": NEW_USER_ID, "organization_id": 9, "matchmaker_id": 7, "assigned_by": 1,
    }


@pytest.mark.asyncio
async def test_convert_skips_assignment_when_no_owner_is_known() -> None:
    """线索无分派且操作账号也无归属时不写 resource_assignment（后续走 assignment 接口补）。"""
    db = StubDB(_lead_row())
    await convert_lead(db, 1, None, None, 3, CustomerLeadConvertRequest())
    assert "INSERT INTO resource_assignment" not in db.sql()


@pytest.mark.asyncio
async def test_convert_maps_a_duplicate_key_race_to_a_readable_conflict() -> None:
    """撞库检查到写库之间被另一个请求抢先入库时，唯一键异常转成 409，不冒 500。"""
    db = StubDB(_lead_row(matchmaker_id=7), conflict_on_user_insert=True)
    with pytest.raises(HTTPException) as err:
        await convert_lead(db, 1, None, None, 3, CustomerLeadConvertRequest())
    assert err.value.status_code == 409
    assert db.rolled_back is True
    assert db.committed is False


def test_lead_select_provides_every_required_customer_lead_field() -> None:
    """LEAD_SELECT 少选一列，客源列表/详情/入库全部 500（Pydantic: Field required）。"""
    required = {name for name, field in CustomerLead.model_fields.items() if field.is_required()}
    assert required, "CustomerLead 应有必填字段"
    assert {name for name in required if name not in LEAD_SELECT} == set()


@pytest.mark.asyncio
async def test_list_leads_normalizes_null_gender_rows() -> None:
    """A13 加性别列之前的客源行 gender 为 NULL；CustomerLead 是 Literal[0,1,2]
    不接受 None，_lead 必须归一为 0（未知）。

    线上曾复现：默认 page_size=20 拉到 NULL 行整页 500，而 page_size=5 只返回
    有 gender 的新行所以验证时漏过——这类「页大小相关」的坑必须用全量页钉住。
    """
    db = StubDB(_lead_row(gender=None, id=99, name="历史NULL行"))
    page = await list_leads(db, 1, 20, None, None, None, None)
    assert page.total == 1
    assert page.items[0].gender == 0
    assert page.items[0].name == "历史NULL行"


def test_gender_injection_lands_only_on_the_outer_from() -> None:
    """gender 列注入必须带表名边界。

    客源标签 2026-10-27 在上游下线后 LEAD_SELECT 不含子查询，但注入仍要只落在外层
    `FROM customer_lead` 上：一旦有人再往里加 `FROM customer_lead_tag …` 这类以
    "FROM customer_lead" 开头的子查询，无边界替换会把子查询改成返回 2 列，MySQL 报
    1241（客源列表/详情 500）。StubDB 按 SQL 文本分派、不校验语法，这类错误单测抓不到，
    只能静态钉住。
    """
    injected = re.sub(
        r"FROM customer_lead(?![a-zA-Z0-9_])",
        ", CAST(gender AS SIGNED) AS gender FROM customer_lead",
        LEAD_SELECT,
    )
    assert injected.count("CAST(gender AS SIGNED) AS gender") == 1
    assert "FROM customer_lead_tag" not in LEAD_SELECT

"""审计第 1 项回归：结构化微信号字段不得返回完整值，掩码回显不得写回。

覆盖三个泄漏点的修复与防回写护栏：
1. 后台会员 CRM 详情（``MemberDetail.wechat``）按权限分级掩码；
2. 客源线索序列化（``_lead``）默认标准档 fail-closed，任何一档都无完整值；
3. 更新路径丢弃掩码回显（``update_member`` / ``update_lead``），保持库中原值，
   而合法提交的新完整微信号仍正常写入。
另钉住 ``mask_middle(keep_tail=0)`` 的回归：``value[-0:]`` 曾把整串拼回掩码尾部。
全部用 StubDB 走真实 service/route → SQL 文本，不依赖 MySQL。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.api.routes import matchmaker_crm_admin as crm
from app.core.sensitive_fields import (
    REDACTION_LEVEL_ELEVATED,
    REDACTION_LEVEL_STANDARD,
    is_masked_value,
    mask_contact,
    mask_middle,
)
from app.schemas.customer_lead_admin import CustomerLeadUpdate
from app.schemas.matchmaker_member_admin import MatchmakerMemberUpdate
from app.services.customer_lead_admin import _lead, update_lead
from app.services.matchmaker_member_admin import update_member

WECHAT = "zhang_san_wx88"
PHONE = "13800000000"
NOW = datetime(2026, 10, 1, 12, 0, 0)


class StubResult:
    def __init__(self, *, rows=None, scalar=None, first=None, rowcount=0):
        self._rows = list(rows or [])
        self._scalar = scalar
        self._first = first
        self.rowcount = rowcount

    def all(self):
        return list(self._rows)

    def scalar(self):
        return self._scalar

    def first(self):
        return self._first

    def mappings(self):
        return self


class StubDB:
    def __init__(self, results=None):
        self.statements: list[tuple[str, dict]] = []
        self._results = list(results or [])
        self.committed = False

    async def execute(self, statement, params=None):
        self.statements.append((str(statement), dict(params or {})))
        if self._results:
            return self._results.pop(0)
        return StubResult()

    async def scalar(self, statement, params=None):
        return (await self.execute(statement, params)).scalar()

    async def commit(self):
        self.committed = True

    def sql(self) -> str:
        return "\n".join(text for text, _ in self.statements)


# --------------------------------------------------------------------------- #
# mask_middle 回归：keep_tail=0 曾等价于保留整串
# --------------------------------------------------------------------------- #
def test_mask_middle_with_zero_tail_never_returns_full_value() -> None:
    assert mask_middle("abcdefg", 3, 0) == "abc****"
    assert mask_middle("wxid_ab12cd34ef", 3, 0) == "wxi" + "*" * 12
    # 长度不足以保留时全掩码，短值不会"越短越可见"
    assert mask_middle("abc", 3, 0) == "***"
    assert mask_middle("ab", 3, 0) == "**"
    assert mask_middle("verylongwechat_id_1", 3, 4) == "ver" + "*" * 12 + "id_1"


def test_mask_contact_levels_and_fail_closed_default() -> None:
    assert mask_contact(WECHAT) == mask_contact(WECHAT, REDACTION_LEVEL_STANDARD)
    assert WECHAT not in mask_contact(WECHAT, REDACTION_LEVEL_STANDARD)
    elevated = mask_contact(WECHAT, REDACTION_LEVEL_ELEVATED)
    assert WECHAT not in elevated
    assert elevated.startswith("zha") and elevated.endswith("x88")
    assert mask_contact(None) is None
    assert mask_contact("   ") is None


def test_is_masked_value_recognises_echo_but_not_real_ids() -> None:
    assert is_masked_value("zha***")
    assert is_masked_value("***")
    assert not is_masked_value(WECHAT)
    assert not is_masked_value(None)


# --------------------------------------------------------------------------- #
# CRM 会员详情：wechat 按权限分级掩码（route 真链路：information_schema → 主查询）
# --------------------------------------------------------------------------- #
def _current(permissions: set[str] | None = None):
    from types import SimpleNamespace

    def _clause(params, correlation=None):
        return "1 = 1"

    return SimpleNamespace(permissions=set(permissions or set()), scope_exists_clause=_clause)


def _member_detail_row(wechat: str | None) -> dict:
    return {
        "id": 9, "nickname": "小宣", "phone": PHONE, "gender": 1, "status": 1,
        "is_vip": False, "vip_end_at": None, "matchmaker_id": 7,
        "created_at": NOW, "avatar": None, "birthday": None,
        "is_married": 1, "residence_city_code": None,
        "wechat": wechat, "tags": None,
    }


_CRM_COLUMNS = [("user_profile", "wechat"), ("user_auth", "education")]


def _crm_db(wechat: str | None) -> StubDB:
    return StubDB([
        StubResult(rows=_CRM_COLUMNS),            # information_schema 列探测
        StubResult(first=_member_detail_row(wechat)),
    ])


@pytest.mark.asyncio
async def test_crm_member_detail_masks_wechat_standard_and_elevated() -> None:
    for permissions, level in (({"message.read"}, REDACTION_LEVEL_STANDARD), ({"*"}, REDACTION_LEVEL_ELEVATED)):
        db = _crm_db(WECHAT)
        detail = await crm.member_detail(member_id=9, current=_current(permissions), db=db)
        assert detail.wechat == mask_contact(WECHAT, level)
        assert WECHAT not in (detail.wechat or "")
        assert "*" in (detail.wechat or "")
        assert WECHAT not in detail.model_dump_json()
        # SQL 原样取列（口径集中在出口），掩码发生在序列化处
        assert "AS wechat" in db.statements[1][0]


@pytest.mark.asyncio
async def test_crm_member_detail_keeps_none_wechat() -> None:
    detail = await crm.member_detail(member_id=9, current=_current(), db=_crm_db(None))
    assert detail.wechat is None


# --------------------------------------------------------------------------- #
# 客源线索：序列化掩码 + 更新护栏
# --------------------------------------------------------------------------- #
def _lead_row(**overrides) -> dict:
    row = {
        "id": 3, "name": "张女士", "phone": PHONE, "wechat": WECHAT,
        "source": "抖音", "intention_level": 2, "status": "NEW", "audit_status": "active",
        "matchmaker_id": None, "organization_id": None, "promoter_id": None,
        "next_follow_at": None, "converted_user_id": None, "remark": None,
        "created_by": 1, "created_at": NOW, "updated_at": NOW,
        "tags_raw": None,
    }
    row.update(overrides)
    return row


def test_lead_serialization_masks_by_default_and_by_level() -> None:
    default = _lead(_lead_row())
    assert default.wechat == mask_contact(WECHAT, REDACTION_LEVEL_STANDARD)
    assert WECHAT not in (default.wechat or "")
    elevated = _lead(_lead_row(), REDACTION_LEVEL_ELEVATED)
    assert WECHAT not in (elevated.wechat or "")
    assert _lead(_lead_row(wechat=None)).wechat is None


def _update_lead_db() -> StubDB:
    """get_lead → _raw_contact(phone) → _raw_contact(wechat) → 冲突查询 → UPDATE → 审计 → get_lead"""
    return StubDB([
        StubResult(first=_lead_row()),
        StubResult(first=(PHONE,)),
        StubResult(first=(WECHAT,)),
        StubResult(scalar=None),      # 重复性校验：无冲突
        StubResult(),                 # UPDATE
        StubResult(),                 # 审计
        StubResult(first=_lead_row()),
    ])


@pytest.mark.asyncio
async def test_lead_update_drops_masked_wechat_echo() -> None:
    db = _update_lead_db()
    updated = await update_lead(db, 1, 3, CustomerLeadUpdate(wechat="zha***"))
    update_stmts = [(s, p) for s, p in db.statements if s.startswith("UPDATE customer_lead")]
    assert len(update_stmts) == 1
    sql, params = update_stmts[0]
    # 掩码值既不绑定也不出现在 SET 子句；字段全被丢弃时不得拼出 ``SET ,`` 病态 SQL
    assert "wechat" not in params
    assert "wechat = :wechat" not in sql
    assert "SET updated_at" in sql
    assert not any("zha***" in str(p.values()) for _, p in db.statements)
    assert updated.wechat == mask_contact(WECHAT, REDACTION_LEVEL_STANDARD)


@pytest.mark.asyncio
async def test_lead_update_still_writes_real_new_wechat() -> None:
    # 提交真实新值时不再读旧 wechat，序列少一次 _raw_contact
    db = StubDB([
        StubResult(first=_lead_row()),
        StubResult(first=(PHONE,)),
        StubResult(scalar=None),
        StubResult(),
        StubResult(),
        StubResult(first=_lead_row()),
    ])
    await update_lead(db, 1, 3, CustomerLeadUpdate(wechat="brand_new_id_77"))
    sql, params = next((s, p) for s, p in db.statements if s.startswith("UPDATE customer_lead"))
    assert params["wechat"] == "brand_new_id_77"
    assert "wechat = :wechat" in sql


# --------------------------------------------------------------------------- #
# 会员更新护栏（service 级，scope/scope_params 走 ALL 档等价谓词）
# --------------------------------------------------------------------------- #
def _member_row() -> dict:
    return {
        "id": 9, "nickname": "小宣", "phone": PHONE, "gender": 1, "status": 1,
        "created_at": NOW, "updated_at": NOW, "matchmaker_id": 7,
        "vip_end_at": None, "is_vip": 0,
    }


@pytest.mark.asyncio
async def test_member_update_drops_masked_wechat_echo() -> None:
    # 掩码字段被丢弃后只剩守卫读、审计与结尾回读三次查询
    db = StubDB([StubResult(first=_member_row()) for _ in range(3)])
    await update_member(
        db, 9, MatchmakerMemberUpdate(wechat="zha***"), 1,
        scope="1 = 1", scope_params={},
    )
    for sql, params in db.statements:
        assert "wechat" not in params
        assert "wechat = :wechat" not in sql
    assert "INSERT INTO user_profile" not in db.sql()
    assert db.committed is True


@pytest.mark.asyncio
async def test_member_update_writes_real_new_wechat() -> None:
    db = StubDB([
        StubResult(first=_member_row()),
        StubResult(rows=[("wechat",)]),   # information_schema: user_profile 有 wechat 列
        StubResult(),                     # INSERT user_profile
        StubResult(),                     # 审计
        StubResult(first=_member_row()),  # 回读
    ])
    await update_member(
        db, 9, MatchmakerMemberUpdate(wechat="brand_new_id_77"), 1,
        scope="1 = 1", scope_params={},
    )
    profile_stmt = next((s, p) for s, p in db.statements if "INSERT INTO user_profile" in s)
    assert profile_stmt[1]["wechat"] == "brand_new_id_77"
    assert db.committed is True

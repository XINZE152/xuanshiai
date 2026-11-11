"""A12 补列 login_num 回归测试（2026-10-24）。

覆盖「users.login_num 累计登录次数」补列项的四处行为：
1. 排序降级：迁移未执行（available 不含 login_num）时按 login_num 排序回落默认排序，
   迁移后走 COALESCE 表达式，且 order 仍优先（沿用既有互斥语义）。
2. 登录自增：touch_login 探测列存在与否，存在则自增、缺列静默跳过，绝不抛 Unknown column。
3. Schema 出参：MemberListItem/MemberDetail 的 login_num 可选，缺列降级为 None。
4. 迁移三件套 + 旧库幂等补齐镜像存在且语句正确。

假会话沿用项目 StubDB/StubResult 范式，只断言 SQL 形态，无需真实数据库。
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from app.api.routes import matchmaker_crm_admin
from app.schemas.matchmaker_crm_admin import MemberDetail, MemberListItem
from app.services.auth import touch_login
from app.services.member_crm_filter import SORT_SQL, member_sort_sql

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_DIR = REPO_ROOT / "migrations" / "user_login_num"


class _Result:
    def __init__(self, *, first=None):
        self._first = first

    def first(self):
        return self._first


class _StubDB:
    def __init__(self, results=None):
        self.statements: list[tuple[str, dict]] = []
        self._results = list(results or [])

    async def execute(self, statement, params=None):
        self.statements.append((str(statement), dict(params or {})))
        if self._results:
            return self._results.pop(0)
        return _Result(first=None)


# --------------------------------------------------------------------------- #
# 排序降级
# --------------------------------------------------------------------------- #


def test_login_num_sort_falls_back_when_column_missing() -> None:
    """缺列（迁移未执行）时按 login_num 排序必须回落默认排序，不能拼 u.login_num。"""
    sql = member_sort_sql("login_num", None, available=frozenset())
    assert "login_num" not in sql
    assert sql == SORT_SQL["created_at"]


def test_login_num_sort_applies_when_column_present() -> None:
    sql = member_sort_sql("login_num", None, available={("users", "login_num")})
    assert "COALESCE(u.login_num, 0) DESC" in sql


def test_order_still_wins_over_sort_by() -> None:
    """order 与 sort_by 互斥、order 优先的既有语义不受补列影响。"""
    assert member_sort_sql("login_num", "earliest", available={("users", "login_num")}) == "u.id ASC"


def test_legacy_call_without_available_is_backward_compatible() -> None:
    """不传 available 的旧调用签名保持可用（不降级、不报错）。"""
    assert "COALESCE(u.login_num, 0) DESC" in member_sort_sql("login_num", None)


# --------------------------------------------------------------------------- #
# 登录自增
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_touch_login_increments_when_column_present() -> None:
    db = _StubDB([_Result(first=(1,))])  # information_schema 探测到列
    await touch_login(db, 42)
    # 第一条是探测，第二条是 UPDATE。
    assert "information_schema.COLUMNS" in db.statements[0][0]
    update_sql, update_params = db.statements[1]
    assert "login_num = login_num + 1" in update_sql
    assert "last_login_at = UTC_TIMESTAMP()" in update_sql
    assert update_params == {"id": 42}


@pytest.mark.asyncio
async def test_touch_login_skips_increment_when_column_missing() -> None:
    db = _StubDB([_Result(first=None)])  # 缺列：迁移未执行
    await touch_login(db, 7)
    update_sql = db.statements[1][0]
    assert "login_num" not in update_sql
    assert "last_login_at = UTC_TIMESTAMP()" in update_sql


@pytest.mark.asyncio
async def test_touch_login_preserves_extra_set_and_increments() -> None:
    db = _StubDB([_Result(first=(1,))])
    await touch_login(db, 9, extra_set="nickname = COALESCE(:nickname, nickname)")
    update_sql = db.statements[1][0]
    assert "login_num = login_num + 1" in update_sql
    assert "nickname = COALESCE(:nickname, nickname)" in update_sql
    assert "last_login_at = UTC_TIMESTAMP()" in update_sql


# --------------------------------------------------------------------------- #
# Schema 出参
# --------------------------------------------------------------------------- #


def test_login_num_is_optional_and_defaults_none() -> None:
    assert MemberListItem.model_fields["login_num"].default is None
    # MemberDetail 继承列表字段，同样带 login_num。
    assert "login_num" in MemberDetail.model_fields


def test_members_routes_project_login_num() -> None:
    source = inspect.getsource(matchmaker_crm_admin)
    assert "{user_expr('login_num')} AS login_num" in source
    # sort_by 白名单已放开 login_num（以及 A12-钱包 balance）。按字段集合断言：
    # 原先是 `"assigned_at|fetched_at|login_num|balance|id" in source`，白名单中间
    # 插入 points / integral 之后连续子串就不成立了（白名单本身没坏）。
    assert {"assigned_at", "fetched_at", "login_num", "balance", "id"} <= set(
        matchmaker_crm_admin.MEMBER_SORT_FIELDS
    )


# --------------------------------------------------------------------------- #
# 迁移三件套 + 旧库幂等补齐镜像
# --------------------------------------------------------------------------- #


def test_login_num_migration_triplet_exists() -> None:
    up = (MIGRATION_DIR / "20261024_01_user_login_num_up.sql").read_text(encoding="utf-8")
    down = (MIGRATION_DIR / "20261024_01_user_login_num_down.sql").read_text(encoding="utf-8")
    verify = (MIGRATION_DIR / "20261024_01_user_login_num_verify.sql").read_text(encoding="utf-8")
    assert "ADD COLUMN" in up and "login_num" in up and "ALTER TABLE `users`" in up
    assert "DROP COLUMN `login_num`" in down
    assert "login_num" in verify

"""审计第 2a 项回归：三类普通权限账号必须能编辑本人范围内会员。

历史缺陷：路由把 ``_member_scope(current, {})`` 的一次性空字典丢掉，``_member`` 谓词里的
``:scope_*`` 占位符没有绑定参数，SELF / STORE / ORGANIZATION 三档在 SQL 层直接报错，
只有 ALL 档可用。这里走 **route → service → SQL 文本** 真链路，断言：
- 谓词里出现的每个 ``:占位符`` 都在同一次 execute 的绑定参数里；
- 非 ALL 档绝不退化为 ``1 = 1``，未绑定时 fail-closed 为 ``1 = 0``。
"""

from __future__ import annotations

import re
from datetime import datetime

import pytest

import app.api.routes.matchmaker_member_admin as routes
from app.api.dependencies import CurrentMatchmakerAdmin
from app.schemas.matchmaker_admin import MatchmakerAdminAccount
from app.schemas.matchmaker_member_admin import MatchmakerMemberUpdate

PLACEHOLDER = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")
NOW = datetime(2026, 10, 1, 12, 0, 0)


class StubResult:
    def __init__(self, *, rows=None, scalar=None, first=None):
        self._rows = list(rows or [])
        self._scalar = scalar
        self._first = first

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

    async def commit(self):
        self.committed = True


def _admin(data_scope: str, *, organization_id: int | None = 10, matchmaker_user_id: int | None = 7) -> CurrentMatchmakerAdmin:
    return CurrentMatchmakerAdmin(
        account=MatchmakerAdminAccount(
            id=1, username="ops", display_name="运营", matchmaker_user_id=matchmaker_user_id,
            data_scope=data_scope, organization_id=organization_id, status=1, last_login_at=None,
        ),
        session_id=1,
        permissions=frozenset({"matchmaker.member.manage"}),
    )


def _member_row() -> dict:
    return {
        "id": 9, "nickname": "小宣", "phone": "13800000000", "gender": 1, "status": 1,
        "created_at": NOW, "updated_at": NOW, "matchmaker_id": 7,
        "vip_end_at": None, "is_vip": 0,
    }


def _guard_sql_and_params(db: StubDB) -> tuple[str, dict]:
    sql, params = db.statements[0]
    assert "WHERE u.id = :id" in sql, "第一条语句应是 _member 守卫查询"
    return sql, params


@pytest.mark.asyncio
async def test_every_scope_tier_binds_all_placeholders() -> None:
    """三档普通账号 + ALL 档：守卫 SQL 的占位符必须全部有绑定值。"""
    for data_scope in ("SELF", "STORE", "ORGANIZATION", "ALL"):
        db = StubDB([
            StubResult(first=_member_row()),   # _member 守卫
            StubResult(),                      # UPDATE users
            StubResult(),                      # 审计
            StubResult(first=_member_row()),   # 结尾回读
        ])
        item = await routes.update(
            member_id=9,
            body=MatchmakerMemberUpdate(nickname="新昵称"),
            current=_admin(data_scope),
            db=db,
        )
        assert item.id == 9
        sql, params = _guard_sql_and_params(db)
        placeholders = set(PLACEHOLDER.findall(sql))
        assert placeholders <= set(params), f"{data_scope} 档占位符缺绑定: {placeholders - set(params)}"
        # 谓词必须成对生成，不得把判定结果裸传（AND () 为空）
        assert "AND ()" not in sql
        if data_scope == "ALL":
            assert "1 = 1" in sql
        else:
            assert "AND (1 = 1)" not in sql


@pytest.mark.asyncio
async def test_self_tier_binds_matchmaker_user_id() -> None:
    db = StubDB([StubResult(first=_member_row()), StubResult(), StubResult(), StubResult(first=_member_row())])
    await routes.update(
        member_id=9, body=MatchmakerMemberUpdate(nickname="新昵称"),
        current=_admin("SELF", matchmaker_user_id=42), db=db,
    )
    sql, params = _guard_sql_and_params(db)
    assert ":scope_matchmaker_user_id" in sql
    assert params["scope_matchmaker_user_id"] == 42


@pytest.mark.asyncio
async def test_store_tier_uses_store_org_binding() -> None:
    db = StubDB([StubResult(first=_member_row()), StubResult(), StubResult(), StubResult(first=_member_row())])
    await routes.update(
        member_id=9, body=MatchmakerMemberUpdate(nickname="新昵称"),
        current=_admin("STORE", organization_id=88), db=db,
    )
    sql, params = _guard_sql_and_params(db)
    assert "scope_assignment.organization_id" in sql
    assert params["scope_store_id"] == 88


@pytest.mark.asyncio
async def test_unbound_self_account_fails_closed_not_open() -> None:
    """SELF 档账号未绑定红娘用户：谓词必须是 1 = 0，绝不能退化为 1 = 1。"""
    db = StubDB([StubResult(first=None)])
    with pytest.raises(Exception) as exc:
        await routes.update(
            member_id=9, body=MatchmakerMemberUpdate(nickname="新昵称"),
            current=_admin("SELF", matchmaker_user_id=None), db=db,
        )
    assert exc.value.status_code == 404
    sql, _ = _guard_sql_and_params(db)
    assert "1 = 0" in sql
    assert "1 = 1" not in sql
    assert db.committed is False


def test_routes_no_longer_pass_disposable_empty_scope_params() -> None:
    """静态护栏：路由不得再出现 ``_member_scope(current, {})`` 式的一次性空字典写法。"""
    source = open(routes.__file__, encoding="utf-8").read()
    assert "_member_scope(" not in source
    assert "_member_guard(current)" in source
    # 守卫返回的 (scope, params) 必须成对透传到 service
    assert source.count("scope_params=scope_params") >= 4

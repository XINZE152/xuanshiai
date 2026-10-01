"""审计第 1b 项回归：其他组织的认证资料不可读。

历史缺陷：``certification_detail`` 读 ``users`` / ``user_auth`` / 认证材料时完全不带范围
谓词，``_member`` 只在 ``if not row`` 分支里当「不存在兜底」调用 —— 等于让越权请求先完整
读到别人的学历、房产、婚姻认证材料与证件图 URL，只在缺行时才想起鉴权。

判据：守卫在取数之前；越权 → 404 且**从未发出**认证材料查询；绑定缺失 fail-closed。
"""

from __future__ import annotations

import inspect
import re
from datetime import datetime

import pytest
from fastapi import HTTPException

import app.services.matchmaker_member_admin as service
from app.api.dependencies import CurrentMatchmakerAdmin
from app.schemas.matchmaker_admin import MatchmakerAdminAccount

PLACEHOLDER = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")
_MATCHMAKER_USER_ID = 7
_ORGANIZATION_ID = 10
_NOW = datetime(2026, 10, 4, 12, 0, 0)

# _member 会用整行构造 MatchmakerMemberAdminItem，守卫行的字段必须给全
_MEMBER_ROW = {
    "id": 555, "nickname": "小王", "phone": "13800000000", "gender": 1, "status": 1,
    "created_at": _NOW, "updated_at": _NOW, "matchmaker_id": _MATCHMAKER_USER_ID,
    "vip_end_at": None, "is_vip": 0,
}

MATERIAL_URL = "https://oss.example.com/cert/555.jpg"


class StubResult:
    def __init__(self, *, first=None, rows=None):
        self._first = first
        self._rows = list(rows or [])

    def mappings(self):
        return self

    def first(self):
        return self._first

    def all(self):
        return list(self._rows)


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

    def sql_text(self) -> str:
        return "\n".join(text for text, _ in self.statements)


def _admin(scope: str, *, matchmaker_user_id: int | None = _MATCHMAKER_USER_ID,
           organization_id: int | None = _ORGANIZATION_ID) -> CurrentMatchmakerAdmin:
    return CurrentMatchmakerAdmin(
        account=MatchmakerAdminAccount(
            id=1, username="admin", display_name="Admin", matchmaker_user_id=matchmaker_user_id,
            data_scope=scope, organization_id=organization_id, status=1, last_login_at=None,
        ),
        session_id=1,
        permissions=frozenset({"matchmaker.member.manage"}),
    )


def _scope(admin: CurrentMatchmakerAdmin) -> tuple[str, dict[str, object]]:
    params: dict[str, object] = {}
    return admin.scope_exists_clause(params, correlation="scope_assignment.user_id = u.id"), params


@pytest.mark.asyncio
async def test_out_of_scope_certification_is_404_and_never_reads_materials() -> None:
    """越权请求必须在守卫处停下：一条 user_auth 查询都不许发出去。"""
    db = StubDB([StubResult(first=None)])  # 守卫：该会员不在本账号范围内
    admin = _admin("ORGANIZATION", organization_id=999)
    scope, scope_params = _scope(admin)
    with pytest.raises(HTTPException) as exc:
        await service.certification_detail(db, 555, "education", scope=scope, scope_params=scope_params)
    assert exc.value.status_code == 404
    assert len(db.statements) == 1
    assert "user_auth" not in db.sql_text()
    assert "scope_assignment.organization_id" in db.statements[0][0]
    assert db.statements[0][1]["scope_organization_id"] == 999


@pytest.mark.asyncio
async def test_unbound_scope_fails_closed_and_binds_every_placeholder() -> None:
    """未绑定红娘/组织的账号必须 fail-closed，且占位符与绑参成对（否则 SQL 直接报错）。"""
    db = StubDB([StubResult(first=None)])
    admin = _admin("SELF", matchmaker_user_id=None)
    scope, scope_params = _scope(admin)
    with pytest.raises(HTTPException) as exc:
        await service.certification_detail(db, 555, "education", scope=scope, scope_params=scope_params)
    assert exc.value.status_code == 404
    sql, params = db.statements[0]
    assert "1 = 0" in sql
    assert "1 = 1" not in sql
    assert set(PLACEHOLDER.findall(sql)) <= set(params)


@pytest.mark.asyncio
async def test_in_scope_guard_precedes_material_read() -> None:
    """范围内读取仍要正常返回材料，但守卫语句必须先于认证表查询。"""
    db = StubDB([
        StubResult(first=_MEMBER_ROW),
        StubResult(first={
            "user_id": 555, "status": 2, "value": "北京大学", "material": MATERIAL_URL,
            "fail_reason": None, "submitted_at": _NOW, "reviewed_at": _NOW,
        }),
    ])
    admin = _admin("STORE")
    scope, scope_params = _scope(admin)
    detail = await service.certification_detail(
        db, 555, "education", scope=scope, scope_params=scope_params
    )
    assert detail.status == 2
    assert [item.url for item in detail.material_urls] == [MATERIAL_URL]
    assert "resource_assignment" in db.statements[0][0]
    assert "user_auth" in db.statements[1][0]
    assert db.statements[0][1]["scope_store_id"] == _ORGANIZATION_ID


@pytest.mark.asyncio
async def test_marriage_branch_without_user_row_is_404_not_unscoped_fallback() -> None:
    """marriage 分支缺行同样 404，不得回退成「按 users 行自己拼一份」。"""
    db = StubDB([StubResult(first=_MEMBER_ROW), StubResult(first=None)])
    admin = _admin("ALL")
    scope, scope_params = _scope(admin)
    with pytest.raises(HTTPException) as exc:
        await service.certification_detail(db, 555, "marriage", scope=scope, scope_params=scope_params)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_unknown_kind_is_rejected_after_guard() -> None:
    db = StubDB([StubResult(first=_MEMBER_ROW)])
    admin = _admin("ALL")
    scope, scope_params = _scope(admin)
    with pytest.raises(HTTPException) as exc:
        await service.certification_detail(db, 555, "income", scope=scope, scope_params=scope_params)
    assert exc.value.status_code == 422


def test_guard_is_called_before_any_material_select() -> None:
    """静态兜底：守卫语义一旦被改回「缺行才鉴权」，顺序断言立刻失败。"""
    source = inspect.getsource(service.certification_detail)
    guard_at = source.index("await _member(db, member_id, scope, scope_params)")
    assert guard_at < source.index("FROM user_auth")
    assert guard_at < source.index("FROM users WHERE id = :id")
    # 旧的兜底写法：先取数、查不到才想起鉴权
    assert "if not row:\n        await _member(" not in source


def test_route_passes_paired_scope_and_params() -> None:
    """路由必须把 scope 与 scope_params 成对透传，否则普通权限账号直接参数缺失。"""
    from app.api.routes import matchmaker_member_admin as routes

    source = inspect.getsource(routes)
    assert source.count("scope_params=scope_params") >= 4
    assert "_member_scope(current, {})" not in source

"""A11 / A12 本轮「无补列/无新表、复用现有表列」项的回归测试（2026-10-24）。

覆盖范围严格对齐本轮 CONTINUE 项，挂起项（补列 / 新表 / 模拟登录）不在本文件：

- A11 光荣榜 ``admin_home.honor`` / 统计卡增量 ``admin_home.monthly_delta``：只读聚合，
  牵线 / 约见按 D-16 作用域收口，活动总数无归属列故不收口（近似口径）。
- A12 平台用户登录日志三接口 ``list_login_logs`` / ``delete_login_log`` / ``clear_login_logs``：
  复用 ``user_login_log``，删除 / 清空为敏感操作，越权 / 不存在统一 404。
- A12 会员列表补 ``points``：复用 ``user_points`` 最新一条余额。

假会话沿用 ``test_admin_data_scope_d16`` 的 ``StubDB`` / ``StubResult`` 范式，
仅断言 SQL 形态、作用域参数与返回结构，无需真实数据库。
"""

from __future__ import annotations

import inspect
from datetime import datetime

import pytest
from fastapi import HTTPException

from app.api.dependencies import CurrentMatchmakerAdmin
from app.api.routes import matchmaker_crm_admin, member_vip_admin
from app.schemas.admin_home import DashboardHonor, DashboardMonthlyDelta
from app.schemas.matchmaker_admin import MatchmakerAdminAccount
from app.schemas.matchmaker_crm_admin import MemberListItem
from app.schemas.member_vip_admin import LoginLogDeleteResult
from app.services import admin_home, member_vip_admin as member_vip_service

SELF_MATCHMAKER_USER_ID = 7
ORGANIZATION_ID = 10


def _admin(
    scope: str,
    *,
    permissions: set[str] | None = None,
    matchmaker_user_id: int | None = SELF_MATCHMAKER_USER_ID,
    organization_id: int | None = ORGANIZATION_ID,
) -> CurrentMatchmakerAdmin:
    return CurrentMatchmakerAdmin(
        account=MatchmakerAdminAccount(
            id=1,
            username="admin",
            display_name="Admin",
            matchmaker_user_id=matchmaker_user_id,
            data_scope=scope,
            organization_id=organization_id,
            status=1,
            last_login_at=None,
        ),
        session_id=1,
        permissions=frozenset(permissions or set()),
    )


class StubResult:
    def __init__(self, *, rows=None, scalar=None, one=None, rowcount=0):
        self._rows = list(rows or [])
        self._scalar = scalar
        self._one = one
        self.rowcount = rowcount

    def all(self):
        return list(self._rows)

    def scalar(self):
        return self._scalar

    def one(self):
        if self._one is None:
            raise AssertionError("one() 未预期被调用")
        return self._one

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

    def sql(self) -> str:
        return "\n".join(text for text, _ in self.statements)


# --------------------------------------------------------------------------- #
# A11 光荣榜 honor
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_honor_scopes_match_and_meeting_but_not_activity() -> None:
    """牵线 / 约见按作用域收口，活动总数无归属列不收口；脱单数用 UNION 去重。"""
    db = StubDB([StubResult(one={key: 0 for key in (
        "line_total", "line_success", "no_single", "meeting_arranged", "activity_held",
    )})])
    result = await admin_home.honor(db, _admin("SELF"))
    assert isinstance(result, DashboardHonor)
    sql = db.statements[0][0]
    # 牵线 / 约见各出现一次作用域 EXISTS，且绑定当前红娘。
    assert sql.count("EXISTS (SELECT 1 FROM resource_assignment") >= 2
    assert "ma.from_user_id" in sql and "mr.organizer_id" in sql
    # 活动总数子查询不带任何作用域谓词（近似口径，与原站 activities total 一致）。
    assert "FROM offline_activity)" in sql
    # 脱单人数近似：成功牵线双方 UNION ALL 去重。
    assert "UNION ALL" in sql and "ma.to_user_id" in sql
    assert db.statements[0][1]["scope_matchmaker_id"] == SELF_MATCHMAKER_USER_ID


@pytest.mark.asyncio
async def test_honor_all_scope_is_not_restricted_and_maps_zeroes() -> None:
    """ALL 作用域退化为 1 = 1，不绑定红娘；空结果各字段为 0。"""
    db = StubDB([StubResult(one={key: None for key in (
        "line_total", "line_success", "no_single", "meeting_arranged", "activity_held",
    )})])
    result = await admin_home.honor(db, _admin("ALL"))
    assert result.line_total == 0 and result.no_single == 0
    assert "scope_matchmaker_id" not in db.statements[0][1]


# --------------------------------------------------------------------------- #
# A11 统计卡本月增量 monthly_delta
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_monthly_delta_projects_scoped_counters_and_online_offline_split() -> None:
    """月增量按自然月计数；online_vip_delta 取 vip - offline 且非负；platform_user == member。"""
    db = StubDB([StubResult(one={
        "member_delta": 5, "wechat_fan_delta": 2, "lead_delta": 3,
        "vip_delta": 10, "offline_vip_delta": 4, "matchmaker_delta": 1,
    })])
    result = await admin_home.monthly_delta(db, _admin("ALL"))
    assert isinstance(result, DashboardMonthlyDelta)
    assert result.month == datetime.now().strftime("%Y-%m")
    assert result.member_delta == 5
    assert result.platform_user_delta == result.member_delta  # 同源于 users
    assert result.vip_delta == 10
    assert result.offline_vip_delta == 4
    assert result.online_vip_delta == 6  # 10 - 4
    assert result.matchmaker_delta == 1
    params = db.statements[0][1]
    assert "month_start" in params and "next_month" in params


@pytest.mark.asyncio
async def test_monthly_delta_online_vip_never_negative() -> None:
    """offline 因近似口径可能大于 vip 计数，online 增量钳制为 0 不出负数。"""
    db = StubDB([StubResult(one={
        "member_delta": 0, "wechat_fan_delta": 0, "lead_delta": 0,
        "vip_delta": 1, "offline_vip_delta": 5, "matchmaker_delta": 0,
    })])
    result = await admin_home.monthly_delta(db, _admin("ALL"))
    assert result.online_vip_delta == 0


# --------------------------------------------------------------------------- #
# A12 平台用户登录日志列表
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_list_login_logs_applies_filters_and_scope() -> None:
    """列表复用 user_login_log，投影 login_type / phone，筛选与作用域进入 WHERE。"""
    row = {
        "id": 10, "user_id": 123, "nickname": "小林", "phone": "138****0000",
        "login_type": 2, "login_status": 1, "ip": "1.2.3.4", "device_id": None,
        "platform": "ios", "failure_reason": None,
        "created_at": datetime(2026, 10, 20, 9, 0, 0),
    }
    db = StubDB([StubResult(rows=[row]), StubResult(scalar=1)])
    scope, scope_params = member_vip_admin._scope(_admin("SELF"), member_vip_admin._USER_CORRELATION)
    page = await member_vip_service.list_login_logs(
        db, 1, 20, search="小林", login_status=1, login_type=2,
        start_date="2026-10-01", end_date="2026-10-31",
        scope=scope or None, scope_params=scope_params,
    )
    assert page.total == 1 and page.items[0].login_type == 2 and page.items[0].phone == "138****0000"
    sql, params = db.statements[0]
    assert "FROM user_login_log l JOIN users u ON u.id = l.user_id" in sql
    assert "l.login_type" in sql and "u.phone" in sql
    assert "scope_assignment.user_id = u.id" in sql
    assert "l.login_type = :login_type" in sql and "l.created_at < DATE_ADD(:end_date" in sql
    assert params["scope_matchmaker_user_id"] == SELF_MATCHMAKER_USER_ID


# --------------------------------------------------------------------------- #
# A12 单条删除 / 一键清空
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_delete_login_log_missing_raises_404_and_not_commits() -> None:
    db = StubDB([StubResult(rowcount=0)])
    scope, scope_params = member_vip_admin._scope(_admin("SELF"), member_vip_admin._USER_CORRELATION)
    with pytest.raises(HTTPException) as exc:
        await member_vip_service.delete_login_log(
            db, 1, 99, scope=scope or None, scope_params=scope_params
        )
    assert exc.value.status_code == 404
    assert db.committed is False


@pytest.mark.asyncio
async def test_delete_login_log_writes_audit_and_commits() -> None:
    db = StubDB([StubResult(rowcount=1), StubResult()])
    deleted = await member_vip_service.delete_login_log(db, 1, 10)
    assert deleted == 1
    assert db.committed is True
    delete_sql = db.statements[0][0]
    # 只删一条：必须带 JOIN users，作用域谓词才挂得到 users 上。
    # 这里统一转小写再比：原先写的是 `delete_sql.upper().startswith("DELETE l FROM user_login_log")`，
    # 左边被大写、右边是混合大小写，这条断言从写下那天起就不可能成立。
    delete_sql = delete_sql.strip().lower()
    assert delete_sql.startswith("delete l from user_login_log")
    assert "join users u" in delete_sql and "l.id = :id" in delete_sql
    audit_sql = db.statements[1][0]
    assert "member.login_log.delete" in audit_sql and "user_login_log" in audit_sql


@pytest.mark.asyncio
async def test_clear_login_logs_resource_id_null_and_summary_reason() -> None:
    """清空为整片删除，审计 resource_id 为 NULL，原因记删除条数。"""
    db = StubDB([StubResult(rowcount=7), StubResult()])
    deleted = await member_vip_service.clear_login_logs(db, 1, login_status=2)
    assert deleted == 7
    assert db.committed is True
    clear_sql = db.statements[0][0]
    assert "DELETE l FROM user_login_log l JOIN users u" in clear_sql
    assert "l.login_status = :login_status" in clear_sql
    audit_sql, audit_params = db.statements[1]
    assert "member.login_log.clear" in audit_sql
    assert "resource_id" in audit_sql and "NULL" in audit_sql
    assert "7" in audit_params["reason"]


def test_login_log_results_are_non_negative_int() -> None:
    assert LoginLogDeleteResult(deleted=0).deleted == 0
    with pytest.raises(Exception):
        LoginLogDeleteResult(deleted=-1)


# --------------------------------------------------------------------------- #
# A12 会员列表补 points（复用 user_points 最新余额）
# --------------------------------------------------------------------------- #


def test_member_list_item_points_defaults_to_zero() -> None:
    assert MemberListItem.model_fields["points"].default == 0


def test_members_route_projects_points_subquery() -> None:
    """members 列表 / 详情的 SELECT 需带 user_points 最新余额子查询。"""
    source = inspect.getsource(matchmaker_crm_admin)
    assert "FROM user_points up WHERE up.user_id = u.id ORDER BY up.id DESC LIMIT 1" in source


# --------------------------------------------------------------------------- #
# 路由注册（静态路径先于 /{member_id}，dashboard 新端点存在）
# --------------------------------------------------------------------------- #


def test_login_log_routes_registered_on_member_prefix() -> None:
    # 同一路径会挂多个方法（GET /login-logs + DELETE /login-logs），必须按路径合并
    # methods，不能写成 {r.path: set(r.methods)} 的字典推导——后一条会把前一条覆盖掉，
    # 于是「GET 也在」这条断言永远失败（路由本身是对的）。
    paths: dict[str, set[str]] = {}
    for route in member_vip_admin.router.routes:
        paths.setdefault(route.path, set()).update(route.methods or set())
    # 兼容 APIRouter.prefix 是否已并入 route.path 两种形态，只断言尾段与工具。
    assert any(p.endswith("/login-logs") for p in paths)
    assert any(p.endswith("/login-logs/{log_id}") for p in paths)
    list_key = next((p for p in paths if p.endswith("/login-logs")), None)
    assert list_key is not None
    assert {"GET", "DELETE"} <= paths[list_key]
    # 静态 /login-logs 必须先于 /{member_id} 注册，否则被 int 路径抢匹配。
    order = [r.path for r in member_vip_admin.router.routes]
    idx_login = next(i for i, p in enumerate(order) if p.endswith("/login-logs"))
    idx_member = next(i for i, p in enumerate(order) if p.endswith("/{member_id}/vip"))
    assert idx_login < idx_member


def test_dashboard_honor_routes_registered() -> None:
    from app.api.routes import matchmaker_dashboard_admin

    paths = {r.path for r in matchmaker_dashboard_admin.router.routes}
    assert any(p.endswith("/honor") for p in paths)
    assert any(p.endswith("/monthly-delta") for p in paths)

"""后台「预览前台」按被查看红娘的身份取数，且与小程序红娘管理中心同口径。

后台红娘管理每行要能预览该红娘的小程序首页（红娘管理中心）。这条链路必须钉住四件事：
1. 取数以「被查看的红娘 user_id」为准，绝不掺入管理员自己的身份；
2. 先看数据范围（get_staff），越权红娘在取业务数据之前就被挡掉；
3. 工作台未开通时按会员侧同一口径 403，不给后台编一个空壳首页；
4. 「约见申请」计数与小程序工作台的「待处理」一致：申请仍在跟进且尚未安排约见，
   已安排约见的只计入「待见面」，不重复计数。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services import matchmaker_staff_admin, matchmaker_workspace

ROLE_OK = "SELECT 1 FROM user_role WHERE user_id = :user_id AND role_code = :role_code"
WORKSPACE_PROFILE = "FROM matchmaker_workspace_profile"


def run(coroutine: object) -> object:
    return asyncio.run(coroutine)  # type: ignore[arg-type]


class FakeResult:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> "FakeResult":
        return self

    def first(self) -> dict | None:
        return self._rows[0] if self._rows else None

    def one(self) -> dict:
        return self._rows[0]


class FakeDB:
    """记录每条 SQL 与绑定参数；scalar/execute 的返回值由 handler 决定。"""

    def __init__(self, handler=None) -> None:
        self.calls: list[tuple[str, dict | None]] = []
        self.handler = handler or (lambda sql, params: None)

    def _record(self, statement: object, params: dict | None) -> str:
        sql = " ".join(str(statement).split())
        self.calls.append((sql, params))
        return sql

    async def execute(self, statement: object, params: dict | None = None) -> FakeResult:
        sql = self._record(statement, params)
        return FakeResult(self.handler(sql, params))

    async def scalar(self, statement: object, params: dict | None = None) -> object:
        sql = self._record(statement, params)
        return self.handler(sql, params)

    def sqls(self) -> str:
        return "\n".join(sql for sql, _ in self.calls)

    def params_for(self, fragment: str) -> dict | None:
        for sql, params in self.calls:
            if fragment in sql:
                return params
        return None


def actor_db(avatar: str | None = "/storage/uploads/avatar-test.png"):
    """默认让「工作台已开通」这条链路走通，按需覆盖单个查询结果。"""

    def handler(sql: str, params: dict | None) -> object:
        if ROLE_OK in sql:
            return 1
        if WORKSPACE_PROFILE in sql and "SELECT level, organization_id" in sql:
            return [{"level": "NORMAL", "organization_id": None}]
        if "COALESCE(p.display_name, u.nickname" in sql:
            return [
                {
                    "display_name": "芸希老师",
                    "level": "NORMAL",
                    "organization_id": None,
                    "avatar": avatar,
                }
            ]
        return overrides.get(sql, None)

    overrides: dict[str, object] = {}
    return FakeDB(handler)


def test_require_workspace_for_user_reads_target_user_not_session() -> None:
    db = actor_db()
    actor = run(matchmaker_workspace.require_workspace_for_user(db, 22))

    assert actor.user_id == 22
    assert actor.display_name == "芸希老师"
    assert actor.scope == "SELF"
    # 欢迎卡头像取 users.avatar，与红娘主页同一列。
    assert actor.avatar == "/storage/uploads/avatar-test.png"
    assert db.params_for(ROLE_OK) == {"user_id": 22, "role_code": "service_matchmaker"}


def test_require_workspace_for_user_tolerates_missing_avatar() -> None:
    db = actor_db(None)
    actor = run(matchmaker_workspace.require_workspace_for_user(db, 22))

    assert actor.avatar is None


def test_member_require_workspace_still_uses_session_user_id() -> None:
    db = actor_db()
    actor = run(matchmaker_workspace.require_workspace(db, SimpleNamespace(id=9)))

    assert actor.user_id == 9
    assert db.params_for(ROLE_OK)["user_id"] == 9


def test_front_preview_checks_staff_scope_before_any_business_query() -> None:
    calls: list[tuple[int, object]] = []

    async def fake_get_staff(db, matchmaker_id, admin):
        calls.append((matchmaker_id, admin))
        return SimpleNamespace(id=matchmaker_id)

    original = matchmaker_staff_admin.get_staff
    matchmaker_staff_admin.get_staff = fake_get_staff
    try:
        db = actor_db()
        admin = SimpleNamespace(account=SimpleNamespace(id=1))
        preview = run(matchmaker_staff_admin.front_preview(db, 22, admin))
    finally:
        matchmaker_staff_admin.get_staff = original

    assert calls == [(22, admin)]
    assert preview.matchmaker_id == 22
    assert preview.workspace_level == "NORMAL"
    assert preview.workspace_scope == "SELF"
    assert preview.dashboard.identity.name == "芸希老师"
    assert preview.dashboard.identity.avatar == "/storage/uploads/avatar-test.png"
    assert [metric.key for metric in preview.dashboard.store_metrics] == [
        "pending_review",
        "served_members",
        "online_commission",
        "offline_performance",
        "pending_introduction",
        "introduction_succeeded",
        "meeting_requests",
        "pending_meeting",
    ]


def test_front_preview_denies_inactive_workspace_with_member_message() -> None:
    async def fake_get_staff(db, matchmaker_id, admin):
        return SimpleNamespace(id=matchmaker_id)

    original = matchmaker_staff_admin.get_staff
    matchmaker_staff_admin.get_staff = fake_get_staff
    try:
        db = FakeDB(lambda sql, params: None if ROLE_OK in sql else 1)
        with pytest.raises(HTTPException) as raised:
            run(matchmaker_staff_admin.front_preview(db, 22, SimpleNamespace()))
    finally:
        matchmaker_staff_admin.get_staff = original

    assert raised.value.status_code == 403
    assert raised.value.detail == "服务红娘申请审核通过并激活后可进入管理中心"
    # 未开通时不再继续查业务表，避免把空壳首页当真实数据展示。
    assert "FROM meeting_request" not in db.sqls()


def dashboard_db() -> FakeDB:
    def handler(sql: str, params: dict | None) -> object:
        if "COUNT(*) FROM users u WHERE" in sql and "matchmaker_member_review" not in sql:
            return 7
        if "COALESCE(mr.status, 'PENDING') = 'PENDING'" in sql:
            return 21
        if "mi.status = 'PENDING'" in sql:
            return 3
        if "mi.status = 'SUCCEEDED'" in sql:
            return 10
        if "COUNT(DISTINCT ra.user_id)" in sql:
            return 2
        if "FROM meeting_request mr" in sql:
            return 14
        if "FROM meeting_record m" in sql:
            return 8
        if "FROM commission_entry" in sql:
            return 509
        return 0

    return FakeDB(handler)


def test_dashboard_meeting_requests_use_workspace_processing_scope() -> None:
    db = dashboard_db()
    metrics = run(
        matchmaker_workspace.workspace_dashboard(
            db, matchmaker_workspace.WorkspaceActor(22, "芸希老师", "NORMAL", None)
        )
    ).store_metrics
    by_key = {metric.key: metric.value for metric in metrics}

    meeting_sql = next(sql for sql, _ in db.calls if "FROM meeting_request mr" in sql)
    # 与小程序工作台的 phase == 'PROCESSING' 一致：三种在途状态，且尚未安排约见。
    assert "mr.status IN ('SUBMITTED', 'CONTACTED', 'ACCEPTED')" in meeting_sql
    assert "NOT EXISTS (SELECT 1 FROM meeting_record m WHERE m.request_id = mr.id)" in meeting_sql
    assert by_key["meeting_requests"] == "14"
    assert by_key["pending_meeting"] == "8"
    assert by_key["pending_review"] == "21"
    assert by_key["pending_introduction"] == "3"
    assert by_key["introduction_succeeded"] == "10"
    assert by_key["served_members"] == "7"


def test_dashboard_super_actor_scopes_by_organization_not_person() -> None:
    db = dashboard_db()
    run(
        matchmaker_workspace.workspace_dashboard(
            db, matchmaker_workspace.WorkspaceActor(22, "总店超级红娘", "SUPER", 3)
        )
    )

    meeting_params = db.params_for("FROM meeting_request mr")
    assert meeting_params == {"scope_organization_id": 3}
    commission_params = db.params_for("FROM commission_entry")
    # 分成是「本人」收益，即使超级红娘看整个门店，这一项仍按被查看红娘本人结算。
    assert commission_params == {"user_id": 22}

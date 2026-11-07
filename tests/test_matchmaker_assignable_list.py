"""「修改红娘」可选名单以后台建档为闸门。

小程序改派名单的三个取数来源（`matchmaker_workspace_profile`、手上还有客源的
`customer_lead.matchmaker_id`、全库生效的 `resource_assignment.matchmaker_id`）过去只认角色行，
于是 2026-09-06 那批「有 `user_role` + 工作台档案、后台从没建过档」的种子号会一直挂在名单里，
和后台「红娘管理」对不上。钉住新契约：
1. 三个来源都按后台 `SELECT_STAFF` 同口径过滤（申请生效 + 档案未软删）；
2. 普通红娘仍只返回自己，不查库；总店超级红娘（不挂门店）仍不按 organization_id 收窄；
3. 写路径同口径：把会员/客源改派给只在角色表里存在的人 → 422 / 404。
"""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime
from typing import Any

import pytest
from fastapi import HTTPException

from app.schemas.matchmaker_workspace import (
    WorkspaceAssignRequest,
    WorkspaceMemberMatchmakerUpdate,
)
from app.services.matchmaker_workspace import (
    WorkspaceActor,
    list_member_matchmakers,
    list_workspace_colleagues,
)
from app.services import matchmaker_workspace

HEADQUARTERS = WorkspaceActor(user_id=2, display_name="红娘测试账号", level="SUPER", organization_id=None)
STORE_SUPER = WorkspaceActor(user_id=22, display_name="周舒窈", level="SUPER", organization_id=1)
NORMAL = WorkspaceActor(user_id=4, display_name="依依", level="NORMAL", organization_id=1)

COLLEAGUE_SELECT = "FROM matchmaker_workspace_profile p"
ASSIGNMENT_SELECT = "SELECT DISTINCT ra.matchmaker_id"
LEAD_SELECT = "FROM customer_lead l LEFT JOIN"


def gate_for(user_id_sql: str) -> str:
    return (
        f"AND EXISTS (SELECT 1 FROM user_matchmaker_apply ma "
        f"LEFT JOIN matchmaker_profile mp ON mp.user_id = ma.user_id "
        f"WHERE ma.user_id = {user_id_sql} AND ma.application_type = 'service_matchmaker' "
        f"AND ma.status = 1 AND mp.deleted_at IS NULL)"
    )


def lead_row(matchmaker_id: int) -> dict:
    return {
        "id": 7,
        "name": "线索甲",
        "phone": None,
        "wechat": None,
        "source": "manual",
        "intention_level": 2,
        "status": "INTENDED",
        "next_follow_at": None,
        "created_at": datetime(2026, 10, 1),
        "updated_at": datetime(2026, 10, 1),
        "matchmaker_id": matchmaker_id,
        "matchmaker_name": None,
        "remark": None,
        "last_follow_up_at": None,
        "converted_user_id": None,
        "photo": None,
        "gender": None,
        "birthday": None,
    }


class FakeResult:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self) -> "FakeResult":
        return self

    def all(self) -> list[dict]:
        return self._rows

    def first(self) -> dict | None:
        return self._rows[0] if self._rows else None


class FakeDB:
    """按 SQL 片段路由行；scalar() 走队列，用来区分同一函数里的多次校验。"""

    def __init__(
        self,
        rows: list[dict] | None = None,
        scalars: list[Any] | None = None,
        routes: dict[str, list[dict]] | None = None,
    ) -> None:
        self.calls: list[str] = []
        self.rows = [{"user_id": 2, "display_name": "红娘测试账号"}] if rows is None else rows
        self.routes = routes or {}
        self.scalars = deque(scalars if scalars is not None else [1])
        self.commits = 0

    async def execute(self, statement: Any, params: dict | None = None) -> FakeResult:
        sql = " ".join(str(statement).split())
        self.calls.append(sql)
        for fragment, rows in self.routes.items():
            if fragment in sql:
                return FakeResult(rows)
        return FakeResult(self.rows)

    async def scalar(self, statement: Any, params: dict | None = None) -> Any:
        self.calls.append(" ".join(str(statement).split()))
        return self.scalars.popleft() if self.scalars else None

    async def commit(self) -> None:
        self.commits += 1


def sql_containing(db: FakeDB, fragment: str) -> str:
    matches = [sql for sql in db.calls if fragment in sql]
    assert matches, f"未捕获到包含 {fragment!r} 的 SQL：{db.calls}"
    return matches[0]


def test_headquarters_colleague_list_gates_both_sources() -> None:
    db = FakeDB()
    asyncio.run(list_workspace_colleagues(db, HEADQUARTERS))

    colleagues = sql_containing(db, COLLEAGUE_SELECT)
    # 工作台档案与手上客源两个来源都要过闸门，且总店超级红娘不按 organization_id 收窄。
    assert gate_for("p.user_id") in colleagues
    assert gate_for("m.matchmaker_id") in colleagues
    assert "p.organization_id =" not in colleagues
    assert "m.organization_id =" not in colleagues


def test_store_super_colleague_list_keeps_organization_filter() -> None:
    db = FakeDB()
    asyncio.run(list_workspace_colleagues(db, STORE_SUPER))
    colleagues = sql_containing(db, COLLEAGUE_SELECT)
    assert gate_for("p.user_id") in colleagues
    assert "p.organization_id = :organization_id" in colleagues


def test_normal_matchmaker_still_sees_only_self() -> None:
    db = FakeDB()
    result = asyncio.run(list_workspace_colleagues(db, NORMAL))
    assert db.calls == []
    assert [(item.user_id, item.is_self) for item in result] == [(4, True)]


def test_member_matchmakers_gates_assignment_source() -> None:
    db = FakeDB(scalars=[1, None], routes={ASSIGNMENT_SELECT: []})
    asyncio.run(list_member_matchmakers(db, HEADQUARTERS, 30))
    assert gate_for("ra.matchmaker_id") in sql_containing(db, ASSIGNMENT_SELECT)


def test_member_matchmakers_keeps_current_assignee_visible() -> None:
    """闸门只过滤「可选的人」，当前跟进人即使没建档也要显示，否则改派弹窗看不出归属在谁手上。"""
    db = FakeDB(
        rows=[],
        scalars=[1, 5],
        routes={
            COLLEAGUE_SELECT: [{"user_id": 2, "display_name": "红娘测试账号"}],
            ASSIGNMENT_SELECT: [],
            "FROM users WHERE id = :id": [{"display_name": "本地演示运营"}],
        },
    )
    result = asyncio.run(list_member_matchmakers(db, HEADQUARTERS, 30))
    assert [(item.user_id, item.display_name) for item in result] == [
        (2, "红娘测试账号"),
        (5, "本地演示运营"),
    ]


def test_assign_member_rejects_role_only_matchmaker() -> None:
    # 第一次 scalar = 会员在范围内；第二次 = 后台建档闸门查无此人（只有 user_role 行）。
    db = FakeDB(scalars=[1, None])
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            matchmaker_workspace.assign_workspace_member_matchmaker(
                db, HEADQUARTERS, 30, WorkspaceMemberMatchmakerUpdate(matchmaker_id=5)
            )
        )
    assert error.value.status_code == 422
    assert gate_for("u.id") in sql_containing(db, "FROM users u WHERE u.id = :id")
    assert db.commits == 0


def test_assign_lead_rejects_role_only_matchmaker() -> None:
    db = FakeDB(rows=[], routes={LEAD_SELECT: [lead_row(2)]})
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            matchmaker_workspace.assign_workspace_lead(
                db, HEADQUARTERS, 7, WorkspaceAssignRequest(matchmaker_id=5)
            )
        )
    assert error.value.status_code == 404
    assert gate_for("p.user_id") in sql_containing(db, COLLEAGUE_SELECT)

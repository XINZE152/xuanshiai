"""资料待审「牵线记录」读路径契约。

口径（2026-10-27 定）：小程序资料待审菜单里的「牵线(N)」和牵线记录子页读的是
matchmaker_introduction（红娘手工录入 + 会员「申请认识」同步的同一张表），
不再是只覆盖会员自助申请的 match_apply，否则红娘自己牵的线一律显示 0。
权限只按会员归属判定：会员在服务范围内就能看到 TA 名下全部牵线记录。
全部用 StubDB 按 SQL 文本分派，不依赖 MySQL。
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import HTTPException

from app.services.matchmaker_workspace import WorkspaceActor, list_match_history

MEMBER_ID = 53
COUNTERPART_ID = 8
CREATED_AT = datetime(2026, 10, 26, 3, 0, 0)


class StubResult:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> "StubResult":
        return self

    def all(self) -> list[dict[str, object]]:
        return self._rows


class StubDB:
    def __init__(self, *, rows: list[dict[str, object]], total: int, member_in_scope: bool) -> None:
        self.rows = rows
        self.total = total
        self.member_in_scope = member_in_scope
        self.reads: list[str] = []

    async def scalar(self, sql: object, params: dict | None = None) -> object:
        text = str(sql)
        self.reads.append(text)
        if "FROM users u" in text:
            return 1 if self.member_in_scope else None
        if "SELECT COUNT(*) FROM matchmaker_introduction" in text:
            return self.total
        raise AssertionError(f"未预期的读查询：{text}")

    async def execute(self, sql: object, params: dict | None = None) -> StubResult:
        text = str(sql)
        self.reads.append(text)
        if "FROM matchmaker_introduction mi" in text:
            self.list_sql = text
            self.list_params = dict(params or {})
            return StubResult(self.rows)
        raise AssertionError(f"未预期的语句：{text}")


def _actor() -> WorkspaceActor:
    return WorkspaceActor(user_id=2, display_name="总店红娘", level="SUPER", organization_id=None)


def _row(introduction_id: int, status: str, direction_source: int) -> dict[str, object]:
    """direction_source 为 from_user_id：等于会员即是「本会员被牵出去」（OUTGOING）。"""
    is_from = direction_source == MEMBER_ID
    return {
        "id": introduction_id,
        "counterpart_user_id": COUNTERPART_ID if is_from else 49,
        "counterpart_nickname": "顾言澄（演示）" if is_from else "程砚舟（示例）",
        "status": status,
        "direction": "OUTGOING" if is_from else "INCOMING",
        "created_at": CREATED_AT,
        "updated_at": CREATED_AT,
    }


@pytest.mark.asyncio
async def test_member_introduction_history_comes_from_the_matchmaker_table() -> None:
    db = StubDB(
        rows=[_row(39, "SUCCEEDED", MEMBER_ID), _row(38, "IN_PROGRESS", COUNTERPART_ID)],
        total=2,
        member_in_scope=True,
    )
    page = await list_match_history(db, _actor(), MEMBER_ID, 1, 100)

    assert "matchmaker_introduction" in db.list_sql
    assert "match_apply" not in db.list_sql
    assert db.list_params["member_id"] == MEMBER_ID
    assert db.list_params["limit"] == 100
    assert page.total == 2
    assert page.has_more is False
    assert [item.direction for item in page.items] == ["OUTGOING", "INCOMING"]
    assert [item.status for item in page.items] == ["SUCCEEDED", "IN_PROGRESS"]
    assert page.items[0].counterpart_nickname == "顾言澄（演示）"
    assert page.items[0].updated_at == CREATED_AT


@pytest.mark.asyncio
async def test_history_rejects_members_outside_the_matchmakers_scope() -> None:
    db = StubDB(rows=[], total=0, member_in_scope=False)
    with pytest.raises(HTTPException) as excinfo:
        await list_match_history(db, _actor(), MEMBER_ID, 1, 100)
    assert excinfo.value.status_code == 404
    assert not any("matchmaker_introduction" in text for text in db.reads)

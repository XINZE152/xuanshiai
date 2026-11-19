"""红娘工作台「捞取客源」的归属改写契约（2026-10-28 口径）。

业务口径：弃海池里的客源被谁捞取，就归谁——`customer_lead.matchmaker_id` 与
`customer_lead.created_by` 一起改成捞取人，所以卡片上的「录入人」会换人；
原登记人仍留在 `customer_lead_abandonment.abandoned_by` 和审计日志里，不丢历史。
全部用 StubDB 按 SQL 文本分派，不依赖 MySQL。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.schemas.matchmaker_workspace import WorkspaceAbandonRequest
from app.services.matchmaker_workspace import WorkspaceActor, restore_workspace_lead

CREATED_AT = datetime(2026, 10, 20, 1, 0, 0)


def lead_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": 26,
        "name": "周女士",
        "real_name": None,
        "phone": "13800000000",
        "wechat": None,
        "source": "线下活动",
        "intention_level": 1,
        "status": "LOST",
        "next_follow_at": None,
        "created_at": CREATED_AT,
        "updated_at": CREATED_AT,
        "remark": None,
        "matchmaker_id": None,
        "matchmaker_name": None,
        "creator_name": "原登记红娘",
        "last_follow_up_at": None,
        "abandon_reason": "多次联系不上",
        "abandoned_at": CREATED_AT,
        "converted_user_id": None,
        "photo": None,
        "gender": 2,
        "birthday": None,
    }
    row.update(overrides)
    return row


class StubResult:
    def __init__(self, rows: list[dict[str, object]], one: dict[str, object] | None = None) -> None:
        self._rows = rows
        self._one = one

    def mappings(self) -> "StubResult":
        return self

    def all(self) -> list[dict[str, object]]:
        return self._rows

    def one(self) -> dict[str, object]:
        assert self._one is not None
        return self._one

    def first(self) -> dict[str, object] | None:
        return self._one


class StubDB:
    def __init__(self, final_row: dict[str, object]) -> None:
        self.final_row = final_row
        self.statements: list[tuple[str, dict[str, object]]] = []
        self.committed = False

    async def scalar(self, sql: object, params: dict | None = None) -> object:
        text = str(sql)
        self.statements.append((text, dict(params or {})))
        if "SELECT a.id FROM customer_lead_abandonment" in text:
            return 501
        if "SELECT id FROM customer_lead WHERE status NOT IN" in text:
            return None
        raise AssertionError(f"未预期的读查询：{text}")

    async def execute(self, sql: object, params: dict | None = None) -> StubResult:
        text = str(sql)
        parameters = dict(params or {})
        self.statements.append((text, parameters))
        if "SELECT COLUMN_NAME FROM information_schema.COLUMNS" in text:
            return StubResult([])
        if "SELECT phone, wechat FROM customer_lead" in text:
            return StubResult([], one={"phone": "13800000000", "wechat": None})
        if text.startswith("SELECT l.id, l.name"):
            return StubResult([], one=self.final_row)
        if text.startswith("UPDATE ") or text.startswith("INSERT "):
            return StubResult([])
        raise AssertionError(f"未预期的语句：{text}")

    async def commit(self) -> None:
        self.committed = True

    def find(self, keyword: str) -> tuple[str, dict[str, object]]:
        for text, params in self.statements:
            if keyword in text:
                return text, params
        raise AssertionError(f"没有执行包含「{keyword}」的语句")


@pytest.mark.asyncio
async def test_restore_takes_ownership_of_the_lead_and_renames_the_recorder() -> None:
    """捞取即换人：归属红娘与录入人一起改成捞取人，弃海流水上的原登记人不动。"""
    db = StubDB(lead_row(status="NEW", matchmaker_id=21, creator_name="捞取红娘"))
    actor = WorkspaceActor(user_id=21, display_name="捞取红娘", level="NORMAL", organization_id=3)

    lead = await restore_workspace_lead(db, actor, 26, WorkspaceAbandonRequest(reason="重新跟进"))

    update_sql, update_params = db.find("UPDATE customer_lead SET")
    assert "created_by = :actor_id" in update_sql
    assert update_params["actor_id"] == 21
    assert update_params["matchmaker_id"] == 21
    assert update_params["organization_id"] == 3
    assert lead.creator_name == "捞取红娘"
    assert lead.matchmaker_id == 21
    assert db.committed

    restore_sql, restore_params = db.find("UPDATE customer_lead_abandonment")
    assert "restored_by = :actor_id" in restore_sql
    assert "abandoned_by" not in restore_sql
    assert "SET reason" not in restore_sql
    assert restore_params["abandonment_id"] == 501

"""后台「牵线记录」→ matchmaker_introduction 的同步契约（2026-10-27 打通）。

口径：后台仍然照原样读写 `match_apply`（不改后台契约），新增和改状态时同步一条
`matchmaker_introduction`，让红娘工作台牵线中心和小程序资料待审的「牵线记录」看到同一批记录。
- note = `admin-entry:{match_apply id}`，按 note 查重（会员改派后归属红娘会变，按联合键会重复插）；
- 跟进人先按发起方会员的生效归属解析，解析不到再按被牵方，双方都没有且未配置承接门店时不落库；
- 状态映射 0→PENDING、1→SUCCEEDED、2/3→FAILED，成功不保留失败理由。
全部用 StubDB 按 SQL 文本分派，不依赖 MySQL。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.core.config import settings
from app.services.matchmaker_workspace import ADMIN_MATCH_RECORD_NOTE_PREFIX, sync_admin_match_record_introduction

RECORD_ID = 777
FROM_ID = 4300
TO_ID = 4301
CREATED_AT = datetime(2026, 10, 20, 9, 30, 0)
NOTE = f"{ADMIN_MATCH_RECORD_NOTE_PREFIX}:{RECORD_ID}"
CATCH_ALL_STORE = 9


class StubResult:
    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> "StubResult":
        return self

    def first(self) -> dict[str, object] | None:
        return self._rows[0] if self._rows else None


class StubDB:
    def __init__(
        self,
        *,
        owners: dict[int, tuple[int | None, int | None]] | None = None,
        staff: set[int] | None = None,
        existing_introduction_id: int | None = None,
    ) -> None:
        self.owners = owners or {}
        self.staff = staff or set()
        self.existing_introduction_id = existing_introduction_id
        self.writes: list[tuple[str, dict]] = []

    async def scalar(self, sql: object, params: dict | None = None) -> object:
        text = str(sql)
        if "FROM user_role" in text:
            return 1 if int((params or {})["user_id"]) in self.staff else None
        if text.startswith("SELECT id FROM matchmaker_introduction WHERE note"):
            return self.existing_introduction_id
        raise AssertionError(f"未预期的读查询：{text}")

    async def execute(self, sql: object, params: dict | None = None) -> StubResult:
        text = str(sql)
        if text.startswith("SELECT matchmaker_id, organization_id FROM resource_assignment"):
            owner = self.owners.get(int((params or {})["user_id"]))
            return StubResult([] if owner is None else [
                {"matchmaker_id": owner[0], "organization_id": owner[1]}
            ])
        if "INTO matchmaker_introduction" in text or "UPDATE matchmaker_introduction" in text:
            self.writes.append((text, dict(params or {})))
            return StubResult()
        raise AssertionError(f"未预期的语句：{text}")

    @property
    def only_write(self) -> tuple[str, dict]:
        assert len(self.writes) == 1, self.writes
        return self.writes[0]

    @property
    def insert_params(self) -> dict:
        statement, params = self.only_write
        assert "INTO matchmaker_introduction" in statement
        return params


@pytest.fixture(autouse=True)
def _catch_all_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "member_auto_assign_organization_id", CATCH_ALL_STORE)


@pytest.mark.asyncio
async def test_admin_success_record_lands_in_the_introduction_table() -> None:
    db = StubDB(owners={FROM_ID: (5, 7)})
    await sync_admin_match_record_introduction(
        db, RECORD_ID, FROM_ID, TO_ID, 1, created_at=CREATED_AT
    )
    statement, params = db.only_write
    assert "INTO matchmaker_introduction" in statement
    assert params == {
        "f": FROM_ID, "t": TO_ID, "m": 5, "org": 7,
        "status": "SUCCEEDED", "reason": None, "note": NOTE, "created_at": CREATED_AT,
    }


@pytest.mark.asyncio
async def test_failed_record_keeps_the_admin_reason_and_status_mapping() -> None:
    for status, expected in ((0, "PENDING"), (2, "FAILED"), (3, "FAILED")):
        db = StubDB(owners={FROM_ID: (5, 7)})
        await sync_admin_match_record_introduction(
            db, RECORD_ID, FROM_ID, TO_ID, status, failure_reason="双方时间没对上"
        )
        assert db.insert_params["status"] == expected
        assert db.insert_params["reason"] == (None if expected == "PENDING" else "双方时间没对上")


@pytest.mark.asyncio
async def test_owner_falls_back_to_the_counterpart_member() -> None:
    """发起方只有门店公海（0 占位）、被牵方有具体归属红娘时，挂给被牵方的红娘，不落公海没人跟。"""
    db = StubDB(owners={FROM_ID: (None, 8), TO_ID: (12, 3)})
    await sync_admin_match_record_introduction(db, RECORD_ID, FROM_ID, TO_ID, 1)
    assert db.insert_params["m"] == 12
    assert db.insert_params["org"] == 3


@pytest.mark.asyncio
async def test_initiator_matchmaker_wins_over_the_counterpart() -> None:
    db = StubDB(owners={FROM_ID: (5, 7), TO_ID: (12, 3)})
    await sync_admin_match_record_introduction(db, RECORD_ID, FROM_ID, TO_ID, 1)
    assert db.insert_params["m"] == 5
    assert db.insert_params["org"] == 7


@pytest.mark.asyncio
async def test_store_only_record_uses_the_placeholder_and_catch_all_store() -> None:
    db = StubDB(owners={FROM_ID: (None, 8)})
    await sync_admin_match_record_introduction(db, RECORD_ID, FROM_ID, TO_ID, 1)
    assert db.insert_params["m"] == 0
    assert db.insert_params["org"] == 8


@pytest.mark.asyncio
async def test_unassigned_pair_is_not_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    """双方都没有生效归属、也没配置承接门店时，落库也没有任何红娘看得见，直接不落。"""
    monkeypatch.setattr(settings, "member_auto_assign_organization_id", None)
    db = StubDB(owners={FROM_ID: (None, None)})
    await sync_admin_match_record_introduction(db, RECORD_ID, FROM_ID, TO_ID, 1)
    assert db.writes == []


@pytest.mark.asyncio
async def test_status_update_reuses_the_existing_row_instead_of_inserting_again() -> None:
    """改派会换 matchmaker_id，按 note 命中已有行更新，不能重复插一条。"""
    db = StubDB(owners={FROM_ID: (5, 7)}, existing_introduction_id=4242)
    await sync_admin_match_record_introduction(
        db, RECORD_ID, FROM_ID, TO_ID, 2, failure_reason="对方不接受"
    )
    statement, params = db.only_write
    assert "UPDATE matchmaker_introduction" in statement
    assert params == {"status": "FAILED", "reason": "对方不接受", "id": 4242}


@pytest.mark.asyncio
async def test_overlong_admin_reason_is_truncated_to_the_column_width() -> None:
    db = StubDB(owners={FROM_ID: (5, 7)})
    await sync_admin_match_record_introduction(
        db, RECORD_ID, FROM_ID, TO_ID, 2, failure_reason="长" * 600
    )
    assert len(db.insert_params["reason"]) == 500

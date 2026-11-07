"""会员「申请认识」↔ 红娘牵线管理的同步契约。

口径（2026-10-23 定）：
- 谁发起申请就挂谁的服务红娘；申请人只归属到门店、没指定具体红娘时挂承接门店（matchmaker_id 用 0 占位）；
- 申请落库即「待牵线」，被认识方同意=牵线成功、拒绝=待牵线，超时未回应仍待跟进；
- 只同步对应申请ID，避免改动同一对会员的其他记录。
全部用 StubDB 按 SQL 文本分派，不依赖 MySQL。
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.services import discovery
from app.services.matchmaker_workspace import (
    UNASSIGNED_MATCHMAKER_ID,
    record_member_application_introduction,
    sync_member_application_introduction,
)

APPLICATION_ID = 501
APPLICANT_ID = 4200
TARGET_ID = 4201
CATCH_ALL_STORE = 9


class StubResult:
    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> "StubResult":
        return self

    def first(self) -> dict[str, object] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict[str, object]]:
        return self._rows


class StubDB:
    def __init__(
        self,
        *,
        staff: bool = False,
        matchmaker_id: int | None = None,
        organization_id: int | None = None,
        assigned: bool = False,
        note_exists: bool = False,
    ) -> None:
        self.staff = staff
        self.matchmaker_id = matchmaker_id
        self.organization_id = organization_id
        self.assigned = assigned
        self.note_exists = note_exists
        self.writes: list[tuple[str, dict]] = []

    async def scalar(self, sql: object, params: dict | None = None) -> object:
        text = str(sql)
        if "FROM user_role" in text:
            return 1 if self.staff else None
        if "FROM matchmaker_introduction" in text:
            return 1 if self.note_exists else None
        raise AssertionError(f"未预期的读查询：{text}")

    async def execute(self, sql: object, params: dict | None = None) -> StubResult:
        text = str(sql)
        if text.startswith("SELECT matchmaker_id, organization_id FROM resource_assignment"):
            if not self.assigned:
                return StubResult()
            return StubResult([{"matchmaker_id": self.matchmaker_id, "organization_id": self.organization_id}])
        if "INTO matchmaker_introduction" in text or "UPDATE matchmaker_introduction" in text or "UPDATE match_apply" in text or "INTO business_audit_log" in text:
            self.writes.append((text, dict(params or {})))
            return StubResult()
        raise AssertionError(f"未预期的语句：{text}")

    def write_for(self, marker: str) -> tuple[str, dict]:
        for statement, params in self.writes:
            if marker in statement:
                return statement, params
        raise AssertionError(f"缺少写入 {marker}")

    @property
    def insert_params(self) -> dict:
        return self.write_for("INTO matchmaker_introduction")[1]


@pytest.fixture(autouse=True)
def _catch_all_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "member_auto_assign_organization_id", CATCH_ALL_STORE)


@pytest.mark.asyncio
async def test_applicant_service_matchmaker_follows_her_own_member() -> None:
    db = StubDB(assigned=True, matchmaker_id=5, organization_id=7)
    await record_member_application_introduction(db, APPLICATION_ID, APPLICANT_ID, TARGET_ID)
    statement, _ = db.write_for("INTO matchmaker_introduction")
    assert "'PENDING'" in statement
    assert db.insert_params == {
        "f": APPLICANT_ID, "t": TARGET_ID, "m": 5, "org": 7, "note": f"discovery-apply:{APPLICATION_ID}",
    }


@pytest.mark.asyncio
async def test_store_only_applicant_lands_in_the_store_sea() -> None:
    """没指定具体红娘时用 0 占位（列是 NOT NULL），靠 organization_id 让该门店超级红娘看到。"""
    db = StubDB(assigned=True, matchmaker_id=None, organization_id=7)
    await record_member_application_introduction(db, APPLICATION_ID, APPLICANT_ID, TARGET_ID)
    assert db.insert_params["m"] == UNASSIGNED_MATCHMAKER_ID
    assert db.insert_params["org"] == 7


@pytest.mark.asyncio
async def test_unassigned_applicant_falls_back_to_the_catch_all_store() -> None:
    db = StubDB()
    await record_member_application_introduction(db, APPLICATION_ID, APPLICANT_ID, TARGET_ID)
    assert db.insert_params["m"] == UNASSIGNED_MATCHMAKER_ID
    assert db.insert_params["org"] == CATCH_ALL_STORE


@pytest.mark.asyncio
async def test_nobody_would_see_it_so_nothing_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "member_auto_assign_organization_id", None)
    db = StubDB()
    await record_member_application_introduction(db, APPLICATION_ID, APPLICANT_ID, TARGET_ID)
    assert db.writes == []


@pytest.mark.asyncio
async def test_service_matchmaker_applicant_is_not_a_member() -> None:
    db = StubDB(staff=True)
    await record_member_application_introduction(db, APPLICATION_ID, APPLICANT_ID, TARGET_ID)
    assert db.writes == []


@pytest.mark.asyncio
async def test_same_application_never_produces_a_second_row() -> None:
    db = StubDB(assigned=True, matchmaker_id=5, note_exists=True)
    await record_member_application_introduction(db, APPLICATION_ID, APPLICANT_ID, TARGET_ID)
    assert db.writes == []


@pytest.mark.asyncio
async def test_acceptance_marks_the_introduction_succeeded() -> None:
    db = StubDB()
    await sync_member_application_introduction(db, APPLICANT_ID, TARGET_ID, application_id=APPLICATION_ID, succeeded=True)
    statement, params = db.write_for("UPDATE matchmaker_introduction")
    assert params == {"status": "SUCCEEDED", "reason": None, "f": APPLICANT_ID, "t": TARGET_ID, "note": f"discovery-apply:{APPLICATION_ID}", "admin_note": f"admin-entry:{APPLICATION_ID}"}
    assert "note IN (:note, :admin_note)" in statement
    assert "status IN ('PENDING', 'IN_PROGRESS', 'FAILED')" in statement


@pytest.mark.asyncio
async def test_rejection_returns_pending_with_response_reason() -> None:
    db = StubDB()
    await sync_member_application_introduction(
        db, APPLICANT_ID, TARGET_ID, application_id=APPLICATION_ID, succeeded=False, failure_reason="暂不考虑"
    )
    _, params = db.write_for("UPDATE matchmaker_introduction")
    assert params["status"] == "PENDING"
    assert params["reason"] == "暂不考虑"


@pytest.mark.asyncio
async def test_blank_reject_reason_falls_back_to_the_member_facing_copy() -> None:
    db = StubDB()
    await sync_member_application_introduction(db, APPLICANT_ID, TARGET_ID, application_id=APPLICATION_ID, succeeded=False, failure_reason=None)
    _, params = db.write_for("UPDATE matchmaker_introduction")
    assert params["reason"] == "对方暂时婉拒了你的申请"


@pytest.mark.asyncio
async def test_overlong_reject_reason_is_truncated_to_the_column_width() -> None:
    db = StubDB()
    await sync_member_application_introduction(
        db, APPLICANT_ID, TARGET_ID, application_id=APPLICATION_ID, succeeded=False, failure_reason="长" * 600
    )
    _, params = db.write_for("UPDATE matchmaker_introduction")
    assert len(params["reason"]) == 500


@pytest.mark.asyncio
async def test_timeout_leaves_introductions_for_matchmaker_follow_up() -> None:
    db = StubDB()
    await discovery._expire_pending_applications(db)
    sweep, _ = db.write_for("UPDATE match_apply")
    assert "SET status = 3" in sweep
    assert not any("matchmaker_introduction" in sql for sql, _ in db.writes)

"""后台建档/审核通过必须同步移动端工作台档案。

后台「红娘管理」读 user_matchmaker_apply，小程序「修改红娘」的可选名单只读
matchmaker_workspace_profile，而后台建档与申请审核过去都不写后一张表，于是建好的红娘
在小程序里选不到。钉住新契约：
1. `sync_workspace_profile` 以后台权威数据补行：名称取 users.nickname，
   门店取 organization_member 的 store_matchmaker 有效绑定，无绑定时为 NULL；
2. overwrite=True（后台建档/改档）同步名称与门店；overwrite=False（申请审核通过）只补行，
   不覆盖红娘在工作台自设过的名称与门店；
3. level 只跟随后台「角色」下拉：建档/编辑显式传 level（总店-超级红娘=SUPER，总店-普通红娘=NORMAL），
   申请审核通过不传，红娘的级别不会被审核动作升降级；
4. 只改电话等与身份无关的字段时不触发同步。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.schemas.auth import MatchmakerReviewRequest
from app.schemas.matchmaker_staff_admin import MatchmakerStaffCreate, MatchmakerStaffUpdate
from app.services import identity, matchmaker_staff_admin, matchmaker_workspace

WORKSPACE_INSERT = "INSERT INTO matchmaker_workspace_profile"
AUTHORITATIVE_READ = "FROM users u WHERE u.id = :user_id"
STAFF_ROW = {"id": 7, "nickname": "新红娘", "phone": "13800138000"}
APPLY_ROW = {
    "id": 11,
    "user_id": 21,
    "application_type": "service_matchmaker",
    "status": 0,
    "real_name": "钱多多",
    "phone": "13800138000",
    "intro": "介绍",
    "cert_images": [],
    "application_details": {},
    "fail_reason": None,
    "created_at": "2026-10-26T10:00:00",
    "reviewed_at": None,
}


class FakeResult:
    def __init__(self, rows: list[dict] | None = None, scalar_value: object = None) -> None:
        self._rows = rows or []
        self._scalar = scalar_value

    def mappings(self) -> "FakeResult":
        return self

    def all(self) -> list[dict]:
        return self._rows

    def first(self) -> dict | None:
        return self._rows[0] if self._rows else None

    def one(self) -> dict:
        return self._rows[0]

    def scalar(self) -> object:
        return self._scalar


class FakeDB:
    def __init__(self, handler=None) -> None:
        self.calls: list[tuple[str, dict | None]] = []
        self.handler = handler or (lambda sql, params: FakeResult())
        self.commits = 0

    async def execute(self, statement: object, params: dict | None = None) -> FakeResult:
        sql = " ".join(str(statement).split())
        self.calls.append((sql, params))
        return self.handler(sql, params)

    async def commit(self) -> None:
        self.commits += 1

    def workspace_statements(self) -> list[tuple[str, dict | None]]:
        return [(sql, params) for sql, params in self.calls if WORKSPACE_INSERT in sql]


def run(coroutine: object) -> None:
    asyncio.run(coroutine)  # type: ignore[arg-type]


def test_sync_profile_overwrite_true_writes_name_and_store() -> None:
    def handler(sql: str, params: dict | None) -> FakeResult:
        if AUTHORITATIVE_READ in sql:
            return FakeResult(rows=[{"display_name": "张三", "organization_id": 3}])
        return FakeResult()

    db = FakeDB(handler)
    run(matchmaker_workspace.sync_workspace_profile(db, 7, overwrite=True))

    sql, params = db.calls[-1]
    assert params == {"user_id": 7, "display_name": "张三", "level": None, "organization_id": 3}
    assert "display_name = VALUES(display_name)" in sql
    assert "organization_id = VALUES(organization_id)" in sql
    assert "status = 1" in sql


def test_sync_profile_overwrite_false_keeps_self_set_name_and_store() -> None:
    def handler(sql: str, params: dict | None) -> FakeResult:
        if AUTHORITATIVE_READ in sql:
            return FakeResult(rows=[{"display_name": "钱多多", "organization_id": None}])
        return FakeResult()

    db = FakeDB(handler)
    run(matchmaker_workspace.sync_workspace_profile(db, 21, overwrite=False))

    sql, params = db.calls[-1]
    assert params["user_id"] == 21
    assert "display_name = VALUES(display_name)" not in sql
    assert "organization_id = VALUES(organization_id)" not in sql
    assert "status = 1" in sql


def test_sync_profile_without_level_keeps_existing_level() -> None:
    def handler(sql: str, params: dict | None) -> FakeResult:
        if AUTHORITATIVE_READ in sql:
            return FakeResult(rows=[{"display_name": "张三", "organization_id": 3}])
        return FakeResult()

    for overwrite in (True, False):
        db = FakeDB(handler)
        run(matchmaker_workspace.sync_workspace_profile(db, 7, overwrite=overwrite))
        sql, params = db.calls[-1]
        # 不传 level（申请审核通过）时，新行按 NORMAL 建，已有行的 level 不动。
        assert params["level"] is None
        assert "COALESCE(:level, 'NORMAL')" in sql
        assert "level = VALUES(level)" not in sql


def test_sync_profile_with_level_follows_admin_role() -> None:
    def handler(sql: str, params: dict | None) -> FakeResult:
        if AUTHORITATIVE_READ in sql:
            return FakeResult(rows=[{"display_name": "张三", "organization_id": None}])
        return FakeResult()

    db = FakeDB(handler)
    run(matchmaker_workspace.sync_workspace_profile(db, 7, overwrite=True, level="SUPER"))

    sql, params = db.calls[-1]
    # 后台选「总店-超级红娘」= 工作台 SUPER；不挂门店时 SUPER 的范围就是平台全部。
    assert params["level"] == "SUPER"
    assert "level = VALUES(level)" in sql


def test_sync_profile_skips_when_user_row_is_gone() -> None:
    db = FakeDB()
    run(matchmaker_workspace.sync_workspace_profile(db, 404, overwrite=True))
    assert db.workspace_statements() == []


def test_sync_profile_truncates_nickname_to_column_width() -> None:
    def handler(sql: str, params: dict | None) -> FakeResult:
        if AUTHORITATIVE_READ in sql:
            return FakeResult(rows=[{"display_name": "长" * 80, "organization_id": None}])
        return FakeResult()

    db = FakeDB(handler)
    run(matchmaker_workspace.sync_workspace_profile(db, 7, overwrite=True))
    assert len(db.calls[-1][1]["display_name"]) == 64


def test_create_staff_writes_workspace_profile() -> None:
    def handler(sql: str, params: dict | None) -> FakeResult:
        if "SELECT 1 FROM organization WHERE id=:id" in sql:
            return FakeResult(scalar_value=1)
        if "SELECT id FROM users WHERE id=:id AND status=1 FOR UPDATE" in sql:
            return FakeResult(rows=[{"id": 7}])
        if AUTHORITATIVE_READ in sql:
            return FakeResult(rows=[{"display_name": "新红娘", "organization_id": 3}])
        if "FROM users u JOIN user_matchmaker_apply ma" in sql:
            return FakeResult(rows=[STAFF_ROW])
        return FakeResult()

    db = FakeDB(handler)
    admin = SimpleNamespace(account=SimpleNamespace(id=9))
    body = MatchmakerStaffCreate(
        user_id=7, display_name="新红娘", phone="13800138000", store_id=3
    )
    run(matchmaker_staff_admin.create_staff(db, admin, body))

    inserts = db.workspace_statements()
    assert len(inserts) == 1
    assert inserts[0][1] == {"user_id": 7, "display_name": "新红娘", "level": "NORMAL", "organization_id": 3}
    assert db.commits == 1


def test_create_staff_super_role_upgrades_workspace_level() -> None:
    def handler(sql: str, params: dict | None) -> FakeResult:
        if "SELECT 1 FROM organization WHERE id=:id" in sql:
            return FakeResult(scalar_value=1)
        if "SELECT id FROM users WHERE id=:id AND status=1 FOR UPDATE" in sql:
            return FakeResult(rows=[{"id": 7}])
        if AUTHORITATIVE_READ in sql:
            return FakeResult(rows=[{"display_name": "总店红娘", "organization_id": None}])
        if "FROM users u JOIN user_matchmaker_apply ma" in sql:
            return FakeResult(rows=[STAFF_ROW])
        return FakeResult()

    db = FakeDB(handler)
    admin = SimpleNamespace(account=SimpleNamespace(id=9))
    body = MatchmakerStaffCreate(user_id=7, display_name="总店红娘", phone="13800138000", role_tag="super")
    run(matchmaker_staff_admin.create_staff(db, admin, body))

    sql, params = db.workspace_statements()[0]
    # 「总店-超级红娘」不选门店：工作台按平台全部范围取数。
    assert params == {"user_id": 7, "display_name": "总店红娘", "level": "SUPER", "organization_id": None}
    assert "level = VALUES(level)" in sql


def test_update_staff_role_tag_syncs_workspace_level() -> None:
    def handler(sql: str, params: dict | None) -> FakeResult:
        if AUTHORITATIVE_READ in sql:
            return FakeResult(rows=[{"display_name": "总店红娘", "organization_id": None}])
        if "FROM users u JOIN user_matchmaker_apply ma" in sql:
            return FakeResult(rows=[STAFF_ROW])
        return FakeResult()

    db = FakeDB(handler)
    run(matchmaker_staff_admin.update_staff(db=db, matchmaker_id=7, body=MatchmakerStaffUpdate(role_tag="normal")))

    inserts = db.workspace_statements()
    assert len(inserts) == 1
    assert inserts[0][1]["level"] == "NORMAL"


def test_update_staff_syncs_only_name_or_store_changes() -> None:
    def handler(sql: str, params: dict | None) -> FakeResult:
        if "SELECT 1 FROM organization WHERE id=:id" in sql:
            return FakeResult(scalar_value=1)
        if AUTHORITATIVE_READ in sql:
            return FakeResult(rows=[{"display_name": "改名后", "organization_id": 5}])
        if "FROM users u JOIN user_matchmaker_apply ma" in sql:
            return FakeResult(rows=[STAFF_ROW])
        return FakeResult()

    renamed = FakeDB(handler)
    run(matchmaker_staff_admin.update_staff(db=renamed, matchmaker_id=7, body=MatchmakerStaffUpdate(display_name="改名后")))
    assert len(renamed.workspace_statements()) == 1

    store_changed = FakeDB(handler)
    run(matchmaker_staff_admin.update_staff(db=store_changed, matchmaker_id=7, body=MatchmakerStaffUpdate(store_id=5)))
    assert store_changed.workspace_statements()[0][1]["organization_id"] == 5

    # 只改电话不该顺手覆盖工作台名称与门店范围。
    phone_only = FakeDB(handler)
    run(matchmaker_staff_admin.update_staff(db=phone_only, matchmaker_id=7, body=MatchmakerStaffUpdate(phone="13800139999")))
    assert phone_only.workspace_statements() == []


def _review_db(status: int, application_type: str) -> FakeDB:
    def handler(sql: str, params: dict | None) -> FakeResult:
        if "SELECT * FROM user_matchmaker_apply WHERE id = :id FOR UPDATE" in sql:
            return FakeResult(rows=[{**APPLY_ROW, "application_type": application_type}])
        if AUTHORITATIVE_READ in sql:
            return FakeResult(rows=[{"display_name": "钱多多", "organization_id": None}])
        if "SELECT id, application_type, status, real_name, phone, intro" in sql:
            return FakeResult(rows=[APPLY_ROW])
        return FakeResult()

    db = FakeDB(handler)
    run(
        identity.review_matchmaker_application(
            db, 9, 11, MatchmakerReviewRequest(status=status, fail_reason=None if status == 1 else "材料不全")
        )
    )
    return db


def test_approved_service_matchmaker_gets_workspace_profile() -> None:
    db = _review_db(1, "service_matchmaker")
    inserts = db.workspace_statements()
    assert len(inserts) == 1
    sql, params = inserts[0]
    assert params["user_id"] == 21
    # 审核通过不覆盖红娘在工作台自设的名称与门店，也不给升降级。
    assert "display_name = VALUES(display_name)" not in sql
    assert params["level"] is None


def test_rejected_application_does_not_create_workspace_profile() -> None:
    assert _review_db(2, "service_matchmaker").workspace_statements() == []


def test_other_role_approval_does_not_create_workspace_profile() -> None:
    assert _review_db(1, "promoter").workspace_statements() == []

"""D-8 / 第 3 项回归：活动报名软删除 + 审计 + 用户端状态与名额一致。

前半段静态核对「不再物理删除、留快照、已支付/已签到拦截」；后半段用 StubDB 走
完整 route / service → SQL 文本，核对后台删除后用户端的报名状态、剩余名额、
我的活动列表与再次报名四者口径一致。
"""

from __future__ import annotations

import pathlib

import pytest
from fastapi import HTTPException

from app.api.dependencies import CurrentMatchmakerAdmin
from app.api.routes import activity_admin
from app.schemas.matchmaker_admin import MatchmakerAdminAccount
from app.services import community

ROOT = pathlib.Path(__file__).resolve().parents[1]
ROUTE_SOURCE = (ROOT / "app/api/routes/activity_admin.py").read_text(encoding="utf-8")
MIGRATION_DIR = ROOT / "migrations/m4_m7"


def test_signup_delete_no_longer_hard_deletes() -> None:
    assert 'DELETE FROM activity_signup WHERE id = :id' not in ROUTE_SOURCE
    assert "deleted_at = UTC_TIMESTAMP(), deleted_by = :actor" in ROUTE_SOURCE


def test_signup_delete_writes_audit_snapshot() -> None:
    assert "'activity_signup.delete'" in ROUTE_SOURCE
    assert "before_json" in ROUTE_SOURCE
    assert "_snapshot_json(snapshot)" in ROUTE_SOURCE


def test_signup_delete_blocks_paid_and_checked_in() -> None:
    assert 'str(snapshot.get("pay_status") or "") == "paid"' in ROUTE_SOURCE
    assert 'int(snapshot.get("checked_in") or 0) == 1' in ROUTE_SOURCE
    assert "该报名已支付，不能删除，请走退款流程" in ROUTE_SOURCE
    assert "该报名已签到，不能删除" in ROUTE_SOURCE


def test_signup_queries_exclude_soft_deleted_rows() -> None:
    """列表 / 统计 / 导出 / 详情 / 修改都必须过滤已软删除记录。"""
    assert 'where = ["1=1", "s.deleted_at IS NULL"]' in ROUTE_SOURCE
    assert 'where = ["s.activity_id = :activity_id", "s.deleted_at IS NULL"]' in ROUTE_SOURCE
    assert 'where = "WHERE 1=1 AND deleted_at IS NULL"' in ROUTE_SOURCE
    assert "WHERE s.id = :id AND s.deleted_at IS NULL" in ROUTE_SOURCE
    assert "WHERE id = :id AND deleted_at IS NULL" in ROUTE_SOURCE


def test_activity_delete_is_blocked_when_paid_signups_exist() -> None:
    assert "pay_status = 'paid' OR COALESCE(checked_in, 0) = 1" in ROUTE_SOURCE
    assert "不能删除，请先处理退款或撤销签到" in ROUTE_SOURCE


def test_soft_delete_columns_are_ensured_and_migrated() -> None:
    setup_source = (ROOT / "database_setup_marriage.py").read_text(encoding="utf-8")
    assert '"deleted_at": "datetime DEFAULT NULL COMMENT' in setup_source
    assert '"deleted_by": "bigint unsigned DEFAULT NULL COMMENT' in setup_source
    assert 'idx_activity_signup_deleted' in setup_source

    up = (MIGRATION_DIR / "20261004_01_activity_signup_soft_delete_up.sql").read_text(encoding="utf-8")
    down = (MIGRATION_DIR / "20261004_01_activity_signup_soft_delete_down.sql").read_text(
        encoding="utf-8"
    )
    assert "`deleted_at` datetime DEFAULT NULL" in up
    assert "`deleted_by` bigint unsigned DEFAULT NULL" in up
    assert "idx_activity_signup_deleted" in up
    assert "DROP INDEX `idx_activity_signup_deleted`" in down
    assert "AS ord, 'deleted_at' AS column_name" in down
    assert "UNION ALL SELECT 2, 'deleted_by'" in down


# --------------------------------------------------------------------------- #
# 第 3 项：后台删除 → 用户端「我的报名状态 / 剩余名额 / 我的活动 / 再次报名」四者一致
#
# 手法沿用 tests/test_admin_data_scope_d4.py 的 StubDB：记录每条 (SQL, 绑定参数)
# 与 BEGIN/COMMIT 时机，走完整 route / service → SQL 文本，不 mock 服务层自身。
# --------------------------------------------------------------------------- #

ACTIVITY_ID = 7
SIGNUP_ID = 99
USER_ID = 42
ADMIN_ACCOUNT_ID = 5


def _admin() -> CurrentMatchmakerAdmin:
    return CurrentMatchmakerAdmin(
        account=MatchmakerAdminAccount(
            id=ADMIN_ACCOUNT_ID,
            username="ops",
            display_name="Ops",
            matchmaker_user_id=None,
            data_scope="ALL",
            organization_id=None,
            status=1,
            last_login_at=None,
        ),
        session_id=1,
        permissions=frozenset({"*"}),
    )


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


class StubTransaction:
    """模拟 `async with db.begin()`：正常退出提交，异常退出回滚。"""

    def __init__(self, db: "StubDB") -> None:
        self._db = db

    async def __aenter__(self) -> "StubTransaction":
        self._db.mark(StubDB.BEGIN)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        self._db.mark(StubDB.COMMIT if exc_type is None else StubDB.ROLLBACK)
        if exc_type is None:
            self._db.committed = True
        return False


class StubDB:
    """记录 SQL 与事务时机的假会话；BEGIN/COMMIT 与 SQL 共用同一条时间线。"""

    BEGIN = "--BEGIN--"
    COMMIT = "--COMMIT--"
    ROLLBACK = "--ROLLBACK--"

    def __init__(self, results=None):
        self.calls: list[tuple[str, dict[str, object]]] = []
        self._results = list(results or [])
        self.committed = False

    def mark(self, event: str) -> None:
        self.calls.append((event, {}))

    async def execute(self, statement, params=None):
        self.calls.append((" ".join(str(statement).split()), dict(params or {})))
        if self._results:
            return self._results.pop(0)
        return StubResult()

    def begin(self) -> StubTransaction:
        return StubTransaction(self)

    async def commit(self) -> None:
        self.mark(StubDB.COMMIT)
        self.committed = True

    def indexes(self, *needles: str) -> list[int]:
        return [i for i, (sql, _) in enumerate(self.calls) if all(n in sql for n in needles)]

    def call(self, *needles: str) -> tuple[str, dict[str, object]]:
        matched = [self.calls[i] for i in self.indexes(*needles)]
        if not matched:
            raise AssertionError(
                f"未执行同时包含 {needles} 的 SQL：\n" + "\n".join(sql for sql, _ in self.calls)
            )
        assert len(matched) == 1, f"{needles} 命中 {len(matched)} 条 SQL，断言失去唯一性"
        return matched[0]

    def event_index(self, event: str) -> int:
        found = self.indexes(event)
        assert len(found) == 1, f"{event} 出现 {len(found)} 次"
        return found[0]

    def sql(self) -> str:
        return "\n".join(sql for sql, _ in self.calls)


def _signup_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": SIGNUP_ID,
        "activity_id": ACTIVITY_ID,
        "user_id": USER_ID,
        "real_name": "张三",
        "phone": "13800000000",
        "remark": None,
        "status": 1,
        "pay_status": "unpaid",
        "checked_in": 0,
        "amount": 0,
        "deleted_at": None,
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_admin_delete_recycles_seat_and_audits_in_one_transaction() -> None:
    """已通过报名被后台删除：软删 + current_people 减 1 + 审计，必须在同一次提交里。"""
    db = StubDB([StubResult(first=_signup_row(status=1))])

    await activity_admin.delete_signup(
        signup_id=SIGNUP_ID, reason="误报名，后台代撤", current=_admin(), db=db
    )

    soft_sql, soft_params = db.call("UPDATE activity_signup SET deleted_at")
    assert "deleted_by = :actor" in soft_sql
    assert "AND deleted_at IS NULL" in soft_sql  # 并发重复删除不会二次扣名额
    assert soft_params == {"actor": ADMIN_ACCOUNT_ID, "id": SIGNUP_ID}

    seat_sql, seat_params = db.call("UPDATE offline_activity SET current_people")
    assert "current_people > 0 THEN current_people - 1 ELSE 0 END" in seat_sql  # 不会变负
    assert "status = 2 AND (max_people <= 0 OR current_people < max_people)" in seat_sql  # 满员自动回开放
    assert seat_params == {"activity_id": ACTIVITY_ID}

    audit_sql, audit_params = db.call("INSERT INTO business_audit_log")
    assert "'activity_signup.delete'" in audit_sql
    assert audit_params["actor"] == ADMIN_ACCOUNT_ID
    assert audit_params["id"] == SIGNUP_ID
    assert '"status": 1' in str(audit_params["before_json"])  # 删除前快照可追溯

    order = [
        db.indexes("SELECT * FROM activity_signup WHERE id")[0],
        db.indexes("UPDATE activity_signup")[0],
        db.indexes("UPDATE offline_activity")[0],
        db.indexes("INSERT INTO business_audit_log")[0],
    ]
    assert order == sorted(order)
    assert order[-1] < db.event_index(StubDB.COMMIT) == len(db.calls) - 1  # 只提交一次，在最后
    assert db.committed


@pytest.mark.asyncio
async def test_admin_delete_pending_signup_also_recycles_seat() -> None:
    """待审核（status=0）同样已占用名额，删除后必须回收，否则剩余名额长期偏小。"""
    db = StubDB([StubResult(first=_signup_row(status=0))])

    await activity_admin.delete_signup(signup_id=SIGNUP_ID, reason=None, current=_admin(), db=db)

    assert db.indexes("UPDATE offline_activity SET current_people")
    assert db.committed


@pytest.mark.asyncio
async def test_admin_delete_cancelled_signup_does_not_touch_seat() -> None:
    """已取消/已拒绝（status 2/3）本就没占名额，删除不得再把名额多减一次。"""
    for status in (2, 3):
        db = StubDB([StubResult(first=_signup_row(status=status))])

        await activity_admin.delete_signup(signup_id=SIGNUP_ID, reason=None, current=_admin(), db=db)

        assert not db.indexes("UPDATE offline_activity"), f"status={status} 不应回收名额"
        assert db.indexes("UPDATE activity_signup SET deleted_at")


@pytest.mark.asyncio
async def test_admin_delete_blocked_before_any_write_for_paid_and_checked_in() -> None:
    """409 拦截必须发生在任何写之前：不能出现半途提交（与第 1c 项同源风险）。"""
    for blocked in (_signup_row(pay_status="paid"), _signup_row(checked_in=1)):
        db = StubDB([StubResult(first=blocked)])

        with pytest.raises(HTTPException) as exc:
            await activity_admin.delete_signup(
                signup_id=SIGNUP_ID, reason=None, current=_admin(), db=db
            )

        assert exc.value.status_code == 409
        assert not db.indexes("UPDATE", "INSERT"), "409 之前不得产生任何写入"
        assert not db.committed


@pytest.mark.asyncio
async def test_admin_delete_missing_signup_is_404_without_writes() -> None:
    db = StubDB([StubResult(first=None)])

    with pytest.raises(HTTPException) as exc:
        await activity_admin.delete_signup(signup_id=SIGNUP_ID, reason=None, current=_admin(), db=db)

    assert exc.value.status_code == 404
    assert not db.indexes("UPDATE", "INSERT")
    assert not db.committed


@pytest.mark.asyncio
async def test_user_side_activity_reads_ignore_soft_deleted_signups() -> None:
    """活动列表 / 详情 / 我的活动：后台删除后用户端不得再看到「已报名」。"""
    db = StubDB()
    await community.list_activities(db, USER_ID, filter_key="all")
    await community.list_activities(db, USER_ID, filter_key="mine")
    with pytest.raises(HTTPException):  # 查不到行 → 404，但 SQL 已按口径拼出
        await community.get_activity(db, USER_ID, ACTIVITY_ID)
    await community.list_my_activities(db, USER_ID, filter_key="all")
    await community.list_my_activities(db, USER_ID, filter_key="joined")

    joined = [sql for sql, _ in db.calls if "activity_signup" in sql]
    assert joined, "用户端活动查询未涉及 activity_signup，用例已失效"
    stale = [sql for sql in joined if "s.deleted_at IS NULL" not in sql]
    assert not stale, "以下用户端查询未过滤软删除报名：\n" + "\n".join(stale)


@pytest.mark.asyncio
async def test_resignup_revives_soft_deleted_row_inside_one_transaction() -> None:
    """再次报名：唯一键冲突时复活原行（deleted_at 归零）并与名额加 1 同事务提交。"""
    db = StubDB(
        [
            StubResult(first={"status": 1, "signup_deadline": None, "max_people": 10, "my_status": None}),
            StubResult(scalar=0),  # 有效报名数（已过滤软删除）
            StubResult(first={"real_name": "张三", "phone": "13800000000"}),
        ]
    )

    result = await community.signup_activity(db, USER_ID, ACTIVITY_ID)

    assert result.success and result.my_status == 0
    lock_sql, lock_params = db.call("LEFT JOIN activity_signup", "FOR UPDATE")
    assert "s.deleted_at IS NULL" in lock_sql  # 已删除报名不再算「已报名」，否则用户再也报不上
    assert lock_params == {"activity_id": ACTIVITY_ID, "user_id": USER_ID}

    count_sql, _ = db.call("SELECT COUNT(*) FROM activity_signup")
    assert "deleted_at IS NULL" in count_sql and "status IN (0, 1)" in count_sql

    insert_sql, insert_params = db.call("INSERT INTO activity_signup")
    assert "ON DUPLICATE KEY UPDATE" in insert_sql
    assert "deleted_at = NULL" in insert_sql and "deleted_by = NULL" in insert_sql
    assert insert_params["user_id"] == USER_ID and insert_params["activity_id"] == ACTIVITY_ID

    seat_sql, seat_params = db.call("UPDATE offline_activity SET current_people = :current_people")
    assert seat_params == {"activity_id": ACTIVITY_ID, "current_people": 1}  # 只按有效报名数累加
    assert "WHEN max_people > 0 AND :current_people >= max_people THEN 2" in seat_sql

    begin = db.event_index(StubDB.BEGIN)
    commit = db.event_index(StubDB.COMMIT)
    for idx in (db.indexes("INSERT INTO activity_signup")[0], db.indexes("UPDATE offline_activity")[0]):
        assert begin < idx < commit, "复活行与名额变更必须在同一事务内"
    assert db.committed


@pytest.mark.asyncio
async def test_resignup_rolls_back_without_writing_when_seats_full() -> None:
    """名额已满按有效报名判定；拒绝时必须整体回滚，不能留下复活行。"""
    db = StubDB(
        [
            StubResult(first={"status": 2, "signup_deadline": None, "max_people": 2, "my_status": None}),
            StubResult(scalar=2),
        ]
    )

    with pytest.raises(HTTPException) as exc:
        await community.signup_activity(db, USER_ID, ACTIVITY_ID)

    assert exc.value.status_code == 422
    assert "名额已满" in str(exc.value.detail)
    assert not db.indexes("INSERT INTO activity_signup", "UPDATE offline_activity")
    assert db.event_index(StubDB.ROLLBACK) > db.event_index(StubDB.BEGIN)
    assert not db.committed


@pytest.mark.asyncio
async def test_signup_with_live_signup_does_not_rewrite_row() -> None:
    """仍有有效报名时重复提交只回「已报名」，不再触发 INSERT（避免覆盖审核状态）。"""
    db = StubDB(
        [StubResult(first={"status": 1, "signup_deadline": None, "max_people": 10, "my_status": 1})]
    )

    result = await community.signup_activity(db, USER_ID, ACTIVITY_ID)

    assert result.message == "已报名，无需重复提交"
    assert not db.indexes("INSERT INTO activity_signup", "UPDATE offline_activity")
    assert db.event_index(StubDB.COMMIT) == len(db.calls) - 1  # 事务收口，但块内零写入


"""会员填完基础信息后自动建归属（auto_assign_new_member）回归。

修复点：自然注册的会员填完资料后既不写 matchmaker_member_review，也不写 resource_assignment，
而红娘工作台的可见范围由 resource_assignment 决定，所以这类会员在任何红娘的待审列表里都不存在。
口径（2026-10-21 改）：不再看资料完整度分数，基础信息那一栏十项填齐即触发。
钉住新契约：
1. 门店 id 未配置或基础信息未填齐 → 直接返回 False，一条 SQL 都不发；
2. 十项齐即触发，缺任意一项都不触发（其余卡片的项目不算门槛）；
3. 已有生效归属（后台分派 / 线索入库 / 红娘录入）不覆盖；
4. 服务红娘自己填资料不会被推进会员待审池；
5. 配置的门店不存在或已停用时跳过，不阻断资料保存；
6. 命中时写三张表：resource_assignment（只给门店，matchmaker_id / assigned_by 为空，
   source=self_registered）、matchmaker_member_review PENDING、business_audit_log（系统 actor）。
全部用 StubDB 按 SQL 文本分派，不依赖 MySQL。
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.services.organization import auto_assign_new_member
from app.services.profile import BASIC_PROFILE_COMPLETION_KEYS, basic_profile_completed

USER_ID = 4200
STORE_ID = 9


class StubResult:
    def __init__(self, lastrowid: int | None = None) -> None:
        self.lastrowid = lastrowid


class StubDB:
    def __init__(self, *, staff: bool = False, assigned: bool = False, store: bool = True, lastrowid: int = 777) -> None:
        self.staff = staff
        self.assigned = assigned
        self.store = store
        self.lastrowid = lastrowid
        self.reads: list[str] = []
        self.writes: list[tuple[str, dict]] = []

    async def scalar(self, sql: object, params: dict | None = None) -> object:
        text = str(sql)
        self.reads.append(text)
        if "FROM user_role" in text:
            return 1 if self.staff else None
        if "FROM resource_assignment" in text:
            return 1 if self.assigned else None
        if "FROM organization" in text:
            return 1 if self.store else None
        raise AssertionError(f"未预期的读查询：{text}")

    async def execute(self, sql: object, params: dict | None = None) -> StubResult:
        self.writes.append((str(sql), dict(params or {})))
        return StubResult(self.lastrowid)

    def write_for(self, marker: str) -> tuple[str, dict]:
        for statement, params in self.writes:
            if marker in statement:
                return statement, params
        raise AssertionError(f"缺少写入 {marker}")


@pytest.fixture(autouse=True)
def _enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "member_auto_assign_organization_id", STORE_ID)


def _all_basic_fields() -> dict[str, bool]:
    return {key: True for key in BASIC_PROFILE_COMPLETION_KEYS}


def test_basic_profile_field_set_is_pinned() -> None:
    """「基础信息填完」= 这十项，是会员端基础信息那一栏的必选项；改口径必须同步改这里。"""
    assert BASIC_PROFILE_COMPLETION_KEYS == (
        "gender", "birthday", "location", "marriage", "occupation",
        "education", "income", "height", "weight", "hometown",
    )


def test_missing_any_basic_field_does_not_trigger() -> None:
    for key in BASIC_PROFILE_COMPLETION_KEYS:
        incomplete = _all_basic_fields()
        incomplete[key] = False
        assert basic_profile_completed(incomplete) is False, f"{key} 没填不该自动归属"


def test_other_sections_are_not_gate() -> None:
    """头像 / 相册 / 自我介绍属于别的卡片：填了降低完整度，但不是进入资料待审的门槛。"""
    assert basic_profile_completed({**_all_basic_fields(), "avatar": True, "album": True, "intro": True}) is True
    assert basic_profile_completed({"avatar": True, "album": True, "intro": True, "preference": True}) is False


@pytest.mark.asyncio
async def test_incomplete_basic_profile_does_not_touch_database() -> None:
    db = StubDB()
    assert await auto_assign_new_member(db, USER_ID, False) is False
    assert db.reads == [] and db.writes == []


@pytest.mark.asyncio
async def test_skips_when_organization_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "member_auto_assign_organization_id", None)
    db = StubDB()
    assert await auto_assign_new_member(db, USER_ID, True) is False
    assert db.reads == [] and db.writes == []


@pytest.mark.asyncio
async def test_complete_basic_profile_triggers() -> None:
    db = StubDB()
    assert await auto_assign_new_member(db, USER_ID, True) is True
    assert db.write_for("INSERT INTO resource_assignment")[1] == {"user_id": USER_ID, "organization_id": STORE_ID}


@pytest.mark.asyncio
async def test_creates_store_sea_assignment_and_pending_review() -> None:
    db = StubDB()
    assert await auto_assign_new_member(db, USER_ID, True) is True
    assignment, _ = db.write_for("INSERT INTO resource_assignment")
    assert "NULL, 'self_registered', 1, NULL" in assignment
    review, review_params = db.write_for("matchmaker_member_review")
    assert review.startswith("INSERT IGNORE INTO matchmaker_member_review")
    assert "'PENDING', NULL" in review
    assert review_params == {"user_id": USER_ID}
    audit, audit_params = db.write_for("INSERT INTO business_audit_log")
    assert "VALUES (NULL," in audit
    assert audit_params["assignment_id"] == 777
    assert audit_params["reason"] == f"基础信息填写完成，自动分派门店 {STORE_ID}"


@pytest.mark.asyncio
async def test_existing_active_assignment_is_never_overwritten() -> None:
    db = StubDB(assigned=True)
    assert await auto_assign_new_member(db, USER_ID, True) is False
    assert db.writes == []
    assert not any("FROM organization" in text for text in db.reads)


@pytest.mark.asyncio
async def test_service_matchmaker_is_not_pushed_into_member_review() -> None:
    db = StubDB(staff=True)
    assert await auto_assign_new_member(db, USER_ID, True) is False
    assert db.writes == []
    assert not any("FROM resource_assignment" in text for text in db.reads)


@pytest.mark.asyncio
async def test_missing_or_disabled_store_skips_without_blocking_the_save() -> None:
    db = StubDB(store=False)
    assert await auto_assign_new_member(db, USER_ID, True) is False
    assert db.writes == []


@pytest.mark.asyncio
async def test_second_profile_update_after_assignment_stays_quiet() -> None:
    """会员陆续补资料会多次触发本函数：第二次必须因为已有归属而什么都不写。"""
    assert await auto_assign_new_member(StubDB(), USER_ID, True) is True
    again = StubDB(assigned=True)
    assert await auto_assign_new_member(again, USER_ID, True) is False
    assert again.writes == []

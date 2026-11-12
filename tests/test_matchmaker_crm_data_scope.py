"""D-4 回归：红娘后台 CRM 会员读写接口的数据范围下沉。

覆盖方案清单 D-4 第 1 组（会员列表/详情/统计）与第 2 组（会员状态/改派写操作），
并额外覆盖同文件的批量改状态、会员实名列表、牵线记录三个同风险面。

验收口径（见 docs/红娘社区消息与AI军师直播详细修改方案清单-2026-10-04.md D-4）：
1. A 组织账号访问 B 组织数据时必须返回 0 条或 403/404；
2. 无 data_scope 绑定的账号访问业务数据必须返回 0 条（fail-closed，不得退化为 1=1）；
3. 作用域逻辑只在 dependencies.py 一处实现。
"""

from __future__ import annotations

import inspect

import pytest
from fastapi import HTTPException

from app.api.dependencies import CurrentMatchmakerAdmin
from app.api.routes import matchmaker_crm_admin as crm
from app.schemas.matchmaker_admin import MatchmakerAdminAccount

SELF_MATCHMAKER_USER_ID = 7
ORGANIZATION_ID = 10

# `members()` 在 2026-10-11 补齐后台筛选参数后新增的 query 参数。单元测试直接调用路由
# 函数（不经 FastAPI 解析），未显式传值的参数拿到的是 `Query` 默认对象而不是 None，
# 因此这里显式补齐；新增参数时同步补充本字典。
NEW_MEMBER_FILTER_DEFAULTS: dict[str, object] = {
    "search_type": None, "source": None, "region": None, "xiangqin_status": None,
    "realname": None, "cert_status": None, "organization_id": None, "matchmaker_id": None,
    "promoter_id": None, "age_min": None, "age_max": None, "height_min": None,
    "height_max": None, "marriage": None, "education": None, "occupation": None,
    "income": None, "income_min": None, "income_max": None,
    "residence": None, "hometown": None, "hukou": None, "religion": None,
    "car": None, "level": None, "tags": None, "offline_vip": None, "promoted": None,
    "newcomer": None, "activity_signup": None, "wechat_added": None, "behavior_type": None,
    "created_start": None, "created_end": None, "assigned_start": None, "assigned_end": None,
    "order": None,
    # 2026-10-16/17 新增：列表可见但无查询参数的列，以及收入上下限。
    "intention_level": None, "house": None, "ethnicity": None,
    "constellation": None, "zodiac": None,
    # A9/B3：弃海筛选（true仅弃海 / false排除弃海）。
    "abandoned": None,
    "profile_review_status": None, "source__is_null": None, "activity_id": None,
    "weight_min": None, "weight_max": None, "next_follow_start": None, "next_follow_end": None,
}


def _admin(scope: str, *, permissions: set[str] | None = None, matchmaker_user_id: int | None = SELF_MATCHMAKER_USER_ID, organization_id: int | None = ORGANIZATION_ID) -> CurrentMatchmakerAdmin:
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
    """最小可用结果对象，按需返回行 / 标量。"""

    def __init__(self, *, rows=None, scalar=None, first=None, one=None, rowcount=0, lastrowid=1):
        self._rows = list(rows or [])
        self._scalar = scalar
        self._first = first
        self._one = one
        self.rowcount = rowcount
        self.lastrowid = lastrowid

    def all(self):
        return list(self._rows)

    def scalar(self):
        return self._scalar

    def first(self):
        return self._first

    def one(self):
        if self._one is None:
            raise AssertionError("one() 未预期被调用")
        return self._one

    def mappings(self):
        return self


class StubDB:
    """记录全部 SQL 的假会话。"""

    def __init__(self, results=None):
        self.statements: list[tuple[str, dict]] = []
        self._results = list(results or [])
        self.committed = False
        self.rolled_back = False

    async def execute(self, statement, params=None):
        self.statements.append((str(statement), dict(params or {})))
        if self._results:
            return self._results.pop(0)
        return StubResult()

    async def scalar(self, statement, params=None):
        result = await self.execute(statement, params)
        return result.scalar()

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    def sql(self) -> str:
        return "\n".join(text for text, _ in self.statements)


# D-9：改派 / 批量改状态已纳入统一幂等，直调服务层须显式传 idempotency_key。
IDEMPOTENCY_KEY = "crm-scope-test-key"


# --------------------------------------------------------------------------- #
# 公共工具：scope_exists_clause 的四档语义
# --------------------------------------------------------------------------- #


def test_scope_exists_clause_all_tier_is_unrestricted() -> None:
    params: dict[str, object] = {}
    assert _admin("ALL").scope_exists_clause(params, correlation="scope_assignment.user_id = u.id") == "1 = 1"
    assert params == {}
    star_params: dict[str, object] = {}
    assert _admin("SELF", permissions={"*"}).scope_exists_clause(star_params, correlation="scope_assignment.user_id = u.id") == "1 = 1"


def test_scope_exists_clause_self_tier_binds_matchmaker() -> None:
    params: dict[str, object] = {}
    clause = _admin("SELF").scope_exists_clause(params, correlation="scope_assignment.user_id = u.id")
    assert "EXISTS (SELECT 1 FROM resource_assignment scope_assignment" in clause
    assert "scope_assignment.status = 1" in clause
    assert "scope_assignment.matchmaker_id = :scope_matchmaker_user_id" in clause
    assert "AND scope_assignment.user_id = u.id" in clause
    assert params == {"scope_matchmaker_user_id": SELF_MATCHMAKER_USER_ID}


def test_scope_exists_clause_store_tier_is_narrower_than_organization() -> None:
    store = _admin("STORE").scope_exists_clause({}, correlation="scope_assignment.user_id = u.id")
    organization = _admin("ORGANIZATION").scope_exists_clause({}, correlation="scope_assignment.user_id = u.id")
    assert "org_type = 'store'" in store
    assert "parent_id" not in store
    assert "parent_id" in organization


def test_scope_exists_clause_is_fail_closed_when_unbound() -> None:
    unbound = _admin("ORGANIZATION", matchmaker_user_id=None, organization_id=None)
    for scope in ("SELF", "STORE", "ORGANIZATION"):
        params: dict[str, object] = {}
        clause = unbound.scope_exists_clause(params, correlation="scope_assignment.user_id = u.id")
        assert "1 = 0" in clause
        assert "1 = 1" not in clause
        assert params == {}


def test_scope_helpers_are_declared_once_in_dependencies() -> None:
    """作用域逻辑必须只存在于 dependencies.py，路由层不得自建四档判定。"""
    source = inspect.getsource(crm)
    assert "data_scope" not in source, "路由层不得直接分支 data_scope，必须走统一工具"
    assert "org_type" not in source, "门店判定不得在路由层重写"
    assert "parent_id" not in source, "组织层级判定不得在路由层重写"
    assert hasattr(CurrentMatchmakerAdmin, "scope_clause")
    assert hasattr(CurrentMatchmakerAdmin, "scope_exists_clause")
    # 每个会员读写端点都必须显式传入作用域谓词。
    for endpoint in (
        crm.members,
        crm.member_statistics,
        crm.member_detail,
        crm.member_assignment,
        crm.member_status,
        crm.batch_member_status,
        crm.member_auth_list,
        crm.match_records,
    ):
        assert "scope_exists_clause" in inspect.getsource(endpoint), endpoint.__name__


# --------------------------------------------------------------------------- #
# 第 1 组：会员列表 / 统计 / 详情
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_member_list_applies_scope_predicate() -> None:
    db = StubDB([StubResult(rows=[]), StubResult(rows=[]), StubResult(scalar=0)])
    page = await crm.members(
        page=1,
        page_size=20,
        gender=None,
        status=None,
        vip=None,
        auth_status=None,
        assigned=None,
        follow_state=None,
        search=None,
        sort_by="created_at",
        current=_admin("SELF"),
        db=db,
        **NEW_MEMBER_FILTER_DEFAULTS,
    )
    assert page.total == 0
    assert "scope_assignment.matchmaker_id = :scope_matchmaker_user_id" in db.sql()
    assert db.statements[1][1]["scope_matchmaker_user_id"] == SELF_MATCHMAKER_USER_ID


@pytest.mark.asyncio
async def test_member_list_filters_by_organization_for_org_account() -> None:
    db = StubDB([StubResult(rows=[]), StubResult(rows=[]), StubResult(scalar=0)])
    await crm.members(
        page=1,
        page_size=20,
        gender=None,
        status=None,
        vip=None,
        auth_status=None,
        assigned=None,
        follow_state=None,
        search=None,
        sort_by="created_at",
        current=_admin("ORGANIZATION"),
        db=db,
        **NEW_MEMBER_FILTER_DEFAULTS,
    )
    assert "scope_assignment.organization_id IN (SELECT id FROM organization" in db.sql()
    assert db.statements[1][1]["scope_organization_id"] == ORGANIZATION_ID


@pytest.mark.asyncio
async def test_member_list_returns_nothing_for_unbound_account() -> None:
    db = StubDB([StubResult(rows=[]), StubResult(rows=[]), StubResult(scalar=0)])
    page = await crm.members(
        page=1,
        page_size=20,
        gender=None,
        status=None,
        vip=None,
        auth_status=None,
        assigned=None,
        follow_state=None,
        search=None,
        sort_by="created_at",
        current=_admin("ORGANIZATION", matchmaker_user_id=None, organization_id=None),
        db=db,
        **NEW_MEMBER_FILTER_DEFAULTS,
    )
    assert page.total == 0
    assert "1 = 0" in db.sql()


@pytest.mark.asyncio
async def test_member_statistics_scopes_vip_subquery() -> None:
    keys = ("total", "male", "female", "vip", "active", "unassigned", "never_followed", "follow_due_today", "abandoned")
    # 首条先被 _available_columns 吃掉（deleted_at / source 都是补列项，先探测再拼 SQL）。
    db = StubDB([StubResult(rows=[]), StubResult(one={key: 0 for key in keys})])
    stats = await crm.member_statistics(current=_admin("STORE"), db=db)
    assert stats.total == 0
    assert stats.abandoned == 0
    assert db.statements[1][1].get("scope_store_id") == ORGANIZATION_ID
    # VIP 子查询必须使用独立别名同口径收口，禁止统计全平台 VIP。
    assert "vip_scope_assignment.user_id = vm.user_id" in db.sql()
    # 弃海计数同样必须收口到账号数据范围（A9/B3）。
    assert "abandoned_scope_assignment.user_id = au.id" in db.sql()
    assert "org_type = 'store'" in db.sql()


@pytest.mark.asyncio
async def test_member_detail_out_of_scope_is_reported_as_not_found() -> None:
    db = StubDB([StubResult(rows=[]), StubResult(first=None)])
    with pytest.raises(HTTPException) as exc:
        await crm.member_detail(member_id=99, current=_admin("SELF"), db=db)
    assert exc.value.status_code == 404
    assert "scope_assignment.matchmaker_id = :scope_matchmaker_user_id" in db.statements[1][0]
    assert db.statements[1][1] == {"id": 99, "scope_matchmaker_user_id": SELF_MATCHMAKER_USER_ID}


# --------------------------------------------------------------------------- #
# 第 2 组：会员状态 / 改派（写操作）
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_member_status_out_of_scope_never_writes() -> None:
    db = StubDB([StubResult(scalar=0)])
    with pytest.raises(HTTPException) as exc:
        await crm.member_status(
            member_id=99,
            body=crm.MemberStatusUpdate(status=2, reason="越权测试"),
            current=_admin("SELF"),
            db=db,
        )
    assert exc.value.status_code == 404
    assert len(db.statements) == 1
    assert "scope_assignment.matchmaker_id = :scope_matchmaker_user_id" in db.statements[0][0]
    assert db.committed is False


@pytest.mark.asyncio
async def test_member_status_in_scope_locks_row_after_scope_check() -> None:
    db = StubDB([StubResult(scalar=1), StubResult(scalar=1), StubResult(scalar=1), StubResult(scalar=1)])
    result = await crm.member_status(
        member_id=99,
        body=crm.MemberStatusUpdate(status=2, reason="正常改状态"),
        current=_admin("SELF"),
        db=db,
    )
    assert result.id == 99
    assert "scope_assignment.matchmaker_id" in db.statements[0][0]
    # 行锁语句不得再包含派生表（MySQL 不允许对派生表加锁）。
    assert db.statements[1][0] == "SELECT id FROM users WHERE id = :id FOR UPDATE"
    assert db.committed is True


@pytest.mark.asyncio
async def test_member_assignment_out_of_scope_is_rejected() -> None:
    db = StubDB([StubResult(scalar=0)])
    with pytest.raises(HTTPException) as exc:
        await crm.member_assignment(
            member_id=99,
            body=crm.MemberAssignmentUpdate(matchmaker_id=5),
            idempotency_key=IDEMPOTENCY_KEY,
            current=_admin("ORGANIZATION"),
            db=db,
        )
    assert exc.value.status_code == 404
    assert "scope_assignment.organization_id" in db.statements[0][0]
    assert db.committed is False
    # 越权在幂等预占之前拦截，不留幂等残留行。
    assert not any("api_idempotency_record" in s for s, _ in db.statements)


@pytest.mark.asyncio
async def test_batch_member_status_update_is_scoped_for_unbound_account() -> None:
    db = StubDB([StubResult(lastrowid=1), StubResult(rows=[]), StubResult(rowcount=0), StubResult(rowcount=1)])
    result = await crm.batch_member_status(
        body=crm.MemberBatchStatus(member_ids=[1, 2, 3], status=2, reason="批量越权测试"),
        idempotency_key=IDEMPOTENCY_KEY,
        current=_admin("ORGANIZATION", matchmaker_user_id=None, organization_id=None),
        db=db,
    )
    assert result["updated"] == 0
    assert result["rejected_ids"] == [1, 2, 3]
    assert "1 = 0" in db.sql()


@pytest.mark.asyncio
async def test_batch_member_status_allows_platform_account() -> None:
    db = StubDB([StubResult(lastrowid=1), StubResult(rows=[(1,)]), StubResult(rowcount=1), StubResult(rowcount=1)])
    await crm.batch_member_status(
        body=crm.MemberBatchStatus(member_ids=[1], status=2, reason="平台批量"),
        idempotency_key=IDEMPOTENCY_KEY,
        current=_admin("ALL"),
        db=db,
    )
    assert "AND (1 = 1)" in db.sql()


@pytest.mark.asyncio
async def test_batch_member_status_uses_idempotency_operation() -> None:
    """D-9：批量改状态必须落统一幂等预占（operation + idempotency_key）。"""
    db = StubDB([StubResult(lastrowid=1), StubResult(rows=[(1,)]), StubResult(rowcount=1), StubResult(rowcount=1)])
    await crm.batch_member_status(
        body=crm.MemberBatchStatus(member_ids=[1], status=2, reason="幂等校验"),
        idempotency_key=IDEMPOTENCY_KEY,
        current=_admin("ALL"),
        db=db,
    )
    reserve_sql, reserve_params = db.statements[0]
    assert "INSERT INTO api_idempotency_record" in reserve_sql
    assert reserve_params["operation"] == "matchmaker.member.batch_status"
    assert reserve_params["idempotency_key"] == IDEMPOTENCY_KEY


# --------------------------------------------------------------------------- #
# 同风险面：会员实名列表 / 牵线记录
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_member_auth_list_is_scoped() -> None:
    db = StubDB([StubResult(rows=[]), StubResult(scalar=0)])
    payload = await crm.member_auth_list(
        page=1,
        page_size=20,
        auth_status=None,
        search=None,
        current=_admin("STORE"),
        db=db,
    )
    assert payload["total"] == 0
    assert "scope_store_id" in db.statements[0][1]
    assert "scope_assignment.organization_id IN (SELECT id FROM organization" in db.sql()


@pytest.mark.asyncio
async def test_match_records_are_scoped_by_from_user() -> None:
    db = StubDB([StubResult(rows=[]), StubResult(scalar=0)])
    page = await crm.match_records(
        page=1, page_size=20, search=None, date_from=None, date_to=None,
        matchmaker_id=None, status_group=None, current=_admin("SELF"), db=db,
    )
    assert page.total == 0
    assert "scope_assignment.user_id = a.from_user_id" in db.statements[0][0]
    assert "scope_matchmaker_user_id" in db.statements[1][0]


@pytest.mark.asyncio
async def test_match_records_filter_by_date_and_matchmaker() -> None:
    """牵线记录页的日期区间与红娘筛选必须落到真实列上。

    回归点：`ra`（归属红娘派生表）原先只加在 SELECT 段，COUNT 查询没有它，
    一旦按红娘筛选，计数 SQL 就会因缺 join 直接报错。
    """
    db = StubDB([StubResult(rows=[]), StubResult(scalar=0)])
    await crm.match_records(
        page=1, page_size=20, search=None,
        date_from="2026-01-01", date_to="2026-12-31", matchmaker_id=5, status_group=None,
        current=_admin("SELF"), db=db,
    )
    rows_sql, rows_params = db.statements[0]
    count_sql, count_params = db.statements[1]
    assert "a.created_at >= :date_from" in rows_sql
    # 结束日按「含当日」处理，补到次日 00:00 前
    assert "a.created_at < DATE_ADD(:date_to, INTERVAL 1 DAY)" in rows_sql
    assert "COALESCE(NULLIF(a.matchmaker_id, 0), ra.matchmaker_id) = :matchmaker_id" in rows_sql
    assert rows_params["date_from"] == "2026-01-01" and rows_params["matchmaker_id"] == 5
    # COUNT 必须同样带 join，否则按红娘筛选时计数报错
    assert "resource_assignment" in count_sql
    assert "COALESCE(NULLIF(a.matchmaker_id, 0), ra.matchmaker_id) = :matchmaker_id" in count_sql
    assert count_params["matchmaker_id"] == 5

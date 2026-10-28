"""2026-10-24 接口缺口清单（B 类字段/参数 + A18 批量弃海）回归。

覆盖范围：
- B6：会员列表 ``auth_status`` 支持英文逗号分隔多值，单值向后兼容；
- B5：``source__is_null`` 空值筛选 + ``/statistics`` 的 ``source_distribution``；
- B9：``weight_min/weight_max`` 区间，缺列时静默跳过；
- B0：``activity_id`` 按具体活动报名筛选；
- A15：``next_follow_start/next_follow_end`` 预约跟进日期区间；
- B8：会员列表补 real_name/weight/tags/last_login_at/ip_location 展示列；
- B10：会员详情 tags 放宽类型，脏数据不再 500；
- A18：批量弃海路由注册、请求校验与逐条成功/失败归类。
"""

from __future__ import annotations

import inspect

import pytest

from app.api.routes import matchmaker_crm_admin as crm_routes
from app.api.routes import member_abandonment_admin as abandon_routes
from app.schemas.matchmaker_crm_admin import MemberDetail, MemberListItem, MemberStatistics
from app.schemas.member_abandonment_admin import MemberBatchAbandonRequest
from app.services.member_abandonment_admin import batch_abandon_members
from app.services.member_crm_filter import build_member_filter

# 「迁移已执行」列集合，覆盖本次新增筛选依赖的列。
FULL_COLUMNS: frozenset[tuple[str, str]] = frozenset(
    {
        ("users", "source"), ("users", "region"), ("users", "level"),
        ("user_profile", "weight"), ("user_profile", "house"), ("user_profile", "tags"),
        ("user_auth", "auth_status"),
    }
)


# --------------------------------------------------------------------------- #
# B6 auth_status 多值
# --------------------------------------------------------------------------- #


def test_auth_status_multi_value_expands_to_in() -> None:
    clause, params = build_member_filter({"auth_status": "1,2,3,4"}, FULL_COLUMNS)
    assert "COALESCE(ua.auth_status, 0) IN (" in clause
    assert params == {"auth_status_0": 1, "auth_status_1": 2, "auth_status_2": 3, "auth_status_3": 4}
    # 取值只出现在 params，不拼进 SQL 文本。
    stripped = clause.replace("1=1 AND ", "")
    assert "1,2,3,4" not in stripped


def test_auth_status_single_value_backward_compatible() -> None:
    clause, params = build_member_filter({"auth_status": "2"}, FULL_COLUMNS)
    assert "COALESCE(ua.auth_status, 0) IN (:auth_status_0)" in clause
    assert params["auth_status_0"] == 2


def test_auth_status_empty_string_is_noop() -> None:
    clause, params = build_member_filter({"auth_status": ""}, FULL_COLUMNS)
    assert "auth_status" not in clause
    assert params == {}


# --------------------------------------------------------------------------- #
# B5 source 空值筛选
# --------------------------------------------------------------------------- #


def test_source_is_null_true_and_false() -> None:
    clause, _ = build_member_filter({"source__is_null": True}, FULL_COLUMNS)
    assert "(u.source IS NULL OR u.source = '')" in clause
    clause2, _ = build_member_filter({"source__is_null": False}, FULL_COLUMNS)
    assert "(u.source IS NOT NULL AND u.source <> '')" in clause2


def test_source_is_null_skips_when_column_missing() -> None:
    clause, params = build_member_filter({"source__is_null": True}, frozenset())
    assert "u.source" not in clause
    assert params == {}


# --------------------------------------------------------------------------- #
# B9 weight 区间
# --------------------------------------------------------------------------- #


def test_weight_range_applies_when_column_present() -> None:
    clause, params = build_member_filter({"weight_min": 50, "weight_max": 90}, FULL_COLUMNS)
    assert "p.weight >= :weight_min" in clause
    assert "p.weight <= :weight_max" in clause
    assert params == {"weight_min": 50, "weight_max": 90}


def test_weight_range_skips_when_column_missing() -> None:
    clause, params = build_member_filter({"weight_min": 50}, frozenset())
    assert "p.weight" not in clause
    assert params == {}


# --------------------------------------------------------------------------- #
# B0 activity_id + A15 next_follow 区间
# --------------------------------------------------------------------------- #


def test_activity_id_exists_predicate() -> None:
    clause, params = build_member_filter({"activity_id": 7}, FULL_COLUMNS)
    assert "EXISTS (SELECT 1 FROM activity_signup asg" in clause
    assert "asg.activity_id = :activity_id" in clause
    assert params["activity_id"] == 7
    # 用户输入不拼进 SQL
    assert ":activity_id" in clause and "7" not in clause


def test_next_follow_range_bounds_inclusive() -> None:
    clause, params = build_member_filter(
        {"next_follow_start": "2026-10-01", "next_follow_end": "2026-10-31"}, FULL_COLUMNS,
    )
    assert "f.next_follow_at >= :next_follow_start" in clause
    assert "f.next_follow_at < DATE_ADD(:next_follow_end, INTERVAL 1 DAY)" in clause
    assert params["next_follow_start"] == "2026-10-01"


# --------------------------------------------------------------------------- #
# B8 / B10 schema 出参与类型
# --------------------------------------------------------------------------- #


def test_member_list_item_declares_new_display_fields() -> None:
    fields = set(MemberListItem.model_fields)
    for name in ("real_name", "weight", "tags", "last_login_at", "ip_location"):
        assert name in fields, name


def test_member_detail_tags_accepts_dirty_nested_values() -> None:
    # 交接脏数据：custom_categories 是空对象而非 list[str]，旧类型会 500。
    dirty = {"personality": ["外向"], "custom_categories": {}}
    detail = MemberDetail(
        id=1, nickname=None, phone=None, gender=1, status=1, is_vip=False,
        vip_end_at=None, matchmaker_id=None, created_at="2026-01-01T00:00:00", tags=dirty,
        # MemberDetail 的 avatar/birthday/is_married/residence_city_code 为无默认必填（响应模型），
        # 手工构造需显式传入（与 tags 容错断言无关）。
        avatar=None, birthday=None, is_married=None, residence_city_code=None,
    )
    assert detail.tags == dirty


def test_member_statistics_has_source_distribution() -> None:
    assert "source_distribution" in MemberStatistics.model_fields


# --------------------------------------------------------------------------- #
# 路由参数注册（防回归漏挂）
# --------------------------------------------------------------------------- #


def _query_params(func) -> set[str]:
    return {
        name
        for name, param in inspect.signature(func).parameters.items()
        if hasattr(param.default, "pattern") or param.default.__class__.__name__ == "Query"
    }


def test_members_route_exposes_gap_fill_params() -> None:
    names = _query_params(crm_routes.members)
    for name in ("auth_status", "source__is_null", "activity_id", "weight_min",
                 "weight_max", "next_follow_start", "next_follow_end"):
        assert name in names, name


def test_batch_abandon_route_registered_before_dynamic_abandon() -> None:
    paths = [route.path for route in abandon_routes.router.routes]
    assert "/admin/members/batch-abandon" in paths
    assert paths.index("/admin/members/batch-abandon") < paths.index("/admin/members/{member_id}/abandon")


def test_batch_abandon_request_bounds() -> None:
    MemberBatchAbandonRequest(member_ids=[1], reason="测试")
    with pytest.raises(Exception):
        MemberBatchAbandonRequest(member_ids=[], reason="空")
    with pytest.raises(Exception):
        MemberBatchAbandonRequest(member_ids=list(range(1, 202)), reason="超限")


class _Result:
    def __init__(self, *, scalars=None, lastrowid=None):
        self._scalars = list(scalars or [])

    def scalars(self):
        return self

    def all(self):
        return list(self._scalars)


class _BatchDB:
    """按序返回预设结果，记录写库语句，用于批量弃海服务逻辑断言。"""

    def __init__(self, results):
        self._results = list(results)
        self.writes: list[tuple[str, dict]] = []
        self.committed = False

    async def execute(self, statement, params=None):
        sql = str(statement)
        if self._results:
            return self._results.pop(0)
        if sql.startswith("INSERT"):
            self.writes.append((sql, dict(params or {})))
        return _Result(scalars=[])

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_batch_abandon_classifies_success_and_failures() -> None:
    from app.schemas.member_abandonment_admin import MemberBatchAbandonRequest as Req

    # 1 有效可弃海；2 已在弃海池；3 越权/不存在（不在锁定结果里）。
    db = _BatchDB([
        _Result(scalars=[1, 2]),          # 作用域内锁定行
        _Result(scalars=[2]),             # 已有效弃海记录
    ])
    body = Req(member_ids=[1, 2, 3], reason="批量测试", abandon_type="manual")
    result = await batch_abandon_members(db, actor_id=99, body=body, scope="1=1", scope_params={})

    assert result.succeeded == [1]
    errors = {f.id: f.error for f in result.failed}
    assert errors[2] == "该会员已在弃海池"
    assert errors[3] == "会员不存在"
    assert db.committed is True
    # 只写入成功项 + 一份汇总审计
    inserts = [sql for sql, _ in db.writes]
    assert sum("member_abandonment" in s for s in inserts) == 1
    assert sum("business_audit_log" in s for s in inserts) == 1

"""后台筛选参数补全回归（2026-10-11）。

覆盖 `docs/后端筛选参数缺口清单.md` 落地后的四条不变量：

1. 多选参数必须走命名占位符绑定，不允许把用户输入拼进 SQL；
2. 依赖新增列的筛选在「列不存在」时必须静默跳过（先发版后迁移不能 500）；
3. `search_type` / `sort_by` / `service_status` 等枚举只能映射到服务端写死的 SQL 片段；
4. 路由层确实暴露了清单要求的参数（防回归漏挂）。
"""

from __future__ import annotations

import inspect
import re

import pytest
from fastapi import HTTPException

from app.api.routes import (
    admin_content as admin_content_routes,
    customer_leads_admin as lead_routes,
    matchmaker_admin_account as account_routes,
    matchmaker_crm_admin as crm_routes,
    member_media_admin as media_routes,
    merchant_admin as merchant_routes,
    offline_vip_admin as vip_routes,
    short_video_admin as video_routes,
)
from app.services import (
    admin_content,
    customer_lead_admin as leads,
    member_media_admin as media,
    merchant_admin,
    offline_vip_admin as vip,
    short_video_admin as video,
)
from app.services.member_crm_filter import (
    MEMBER_CODE_EXPR,
    ORDER_SQL,
    SEARCH_TYPE_SQL,
    SORT_SQL,
    build_member_filter,
    member_sort_sql,
    split_values,
)

# 迁移补出来的全部列（(表, 列)），用于模拟「迁移已执行」。
ALL_COLUMNS: frozenset[tuple[str, str]] = frozenset(
    {
        ("users", "source"), ("users", "region"), ("users", "cert_status"),
        ("users", "wechat_added"), ("users", "promoted"), ("users", "newcomer"),
        ("users", "activity_signup"), ("users", "level"), ("users", "fetched_at"),
        ("user_profile", "car"), ("user_profile", "household"), ("user_profile", "religion"),
        ("user_profile", "intention_level"), ("user_profile", "house"),
        ("user_profile", "ethnicity"), ("user_profile", "constellation"),
        ("user_profile", "zodiac"),
    }
)


class StubResult:
    """最小可用结果对象，按需返回行 / 标量。"""

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


class StubDB:
    """记录全部 SQL 的假会话，按入队顺序返回结果。"""

    def __init__(self, results=None):
        self.statements: list[tuple[str, dict]] = []
        self._results = list(results or [])

    async def execute(self, statement, params=None):
        self.statements.append((str(statement), dict(params or {})))
        if self._results:
            return self._results.pop(0)
        return StubResult()

    async def scalar(self, statement, params=None):
        result = await self.execute(statement, params)
        return result.scalar()

    async def commit(self):
        return None

    def sql(self) -> str:
        return "\n".join(text for text, _ in self.statements)

    def clauses(self) -> str:
        """只取 WHERE 之后的片段，避免断言命中 SELECT 列表。"""
        joined = self.sql()
        marker = " WHERE "
        return joined.split(marker, 1)[1] if marker in joined else joined


# --------------------------------------------------------------------------- #
# 1. 多选参数走绑定占位符
# --------------------------------------------------------------------------- #


def test_split_values_dedupes_and_limits() -> None:
    assert split_values("1,2,1, ,3") == ["1", "2", "3"]
    assert split_values(None) == []
    assert split_values("") == []
    assert len(split_values(",".join(str(i) for i in range(80)))) == 50


def test_member_filter_multi_value_uses_named_placeholders() -> None:
    clause, params = build_member_filter(
        {"marriage": "1,2", "education": "本科", "occupation": "教师,医生", "income": "10-20万"},
        ALL_COLUMNS,
    )
    assert "u.is_married IN (:marriage_0, :marriage_1)" in clause
    assert "ua.education IN (:education_0)" in clause
    assert "ua.job IN (:occupation_0, :occupation_1)" in clause
    assert "p.income IN (:income_0)" in clause
    # 取值只出现在 params，不出现在 SQL 文本里。
    assert params == {
        "marriage_0": "1", "marriage_1": "2",
        "education_0": "本科",
        "occupation_0": "教师", "occupation_1": "医生",
        "income_0": "10-20万",
    }
    # 去掉占位符与恒真前缀后不得残留任何用户取值（防止字符串拼接）。
    stripped = re.sub(r":\w+", "", clause).replace("1=1 AND ", "")
    for value in params.values():
        assert value not in stripped, value


def test_member_filter_tags_use_json_contains_with_binding() -> None:
    clause, params = build_member_filter({"tags": "优质,活跃"}, ALL_COLUMNS)
    assert "JSON_CONTAINS(p.tags, :tag_0) OR JSON_CONTAINS(p.tags, :tag_1)" in clause
    assert params == {"tag_0": '"优质"', "tag_1": '"活跃"'}


@pytest.mark.asyncio
async def test_lead_filter_multi_value_uses_named_placeholders() -> None:
    db = StubDB([
        StubResult(rows=[("age",), ("height",), ("marriage",), ("education",), ("occupation",),
                         ("residence",), ("hometown",), ("xiangqin_status",), ("bind_mode",),
                         ("wechat_added",), ("assigned_at",)]),
        StubResult(rows=[]),
        StubResult(scalar=0),
    ])
    await leads.list_leads(
        db, 1, 20, None, None, None, None,
        marriage="1,2", education="本科",
    )
    clause = db.clauses()
    assert "marriage IN (:marriage_0, :marriage_1)" in clause
    assert "education IN (:education_0)" in clause
    params = db.statements[1][1]
    assert params["marriage_0"] == "1" and params["marriage_1"] == "2"


# --------------------------------------------------------------------------- #
# 2. 缺列降级：先发版后迁移不能 500
# --------------------------------------------------------------------------- #


def test_member_filter_skips_missing_columns() -> None:
    values = {
        "source": "推广", "region": "mainland", "cert_status": 1,
        "wechat_added": 1, "promoted": True, "newcomer": False,
        "activity_signup": True, "level": "gold", "hukou": "上海",
        "car": "已购", "religion": "无",
    }
    clause, params = build_member_filter(values, frozenset())
    for token in ("u.source", "u.region", "u.cert_status", "u.wechat_added", "u.promoted",
                  "u.newcomer", "u.activity_signup", "u.level", "p.household", "p.car", "p.religion"):
        assert token not in clause, token
    assert params == {}


def test_member_filter_applies_columns_when_available() -> None:
    values = {
        "source": "推广", "region": "mainland", "cert_status": 1,
        "wechat_added": 1, "promoted": True, "newcomer": False,
        "activity_signup": True, "level": "gold", "hukou": "上海",
        "car": "已购", "religion": "无",
    }
    clause, params = build_member_filter(values, ALL_COLUMNS)
    assert "u.source = :source" in clause
    assert "u.region = :region" in clause
    assert "u.cert_status = :cert_status" in clause
    assert "u.wechat_added = :wechat_added" in clause
    assert "u.promoted = 1" in clause
    assert "u.newcomer = 0" in clause
    assert "u.activity_signup = 1" in clause
    assert "u.level IN (:level_0)" in clause
    assert "p.household LIKE" in clause
    assert "p.car IN (:car_0)" in clause
    assert "p.religion = :religion" in clause
    assert params["source"] == "推广" and params["region"] == "mainland"


def test_member_filter_region_rejects_unknown_value() -> None:
    clause, params = build_member_filter({"region": "火星"}, ALL_COLUMNS)
    assert "u.region" not in clause
    assert params == {}


@pytest.mark.parametrize(
    ("key", "column"),
    [("level", "u.level"), ("house", "p.house"), ("ethnicity", "p.ethnicity"), ("car", "p.car")],
)
def test_member_filter_multi_value_dims_expand(key: str, column: str) -> None:
    """界面上标「（多选）」的四列必须真的按多值展开，而不是只认第一个值。"""
    clause, params = build_member_filter({key: "甲,乙"}, ALL_COLUMNS)
    assert f"{column} IN (:{key}_0, :{key}_1)" in clause
    assert params[f"{key}_0"] == "甲" and params[f"{key}_1"] == "乙"


def test_member_filter_single_value_dims_match_exactly() -> None:
    clause, params = build_member_filter(
        {"intention_level": 3, "constellation": "天秤座", "zodiac": "龙"}, ALL_COLUMNS,
    )
    assert "p.intention_level = :intention_level" in clause
    assert "p.constellation = :constellation" in clause
    assert "p.zodiac = :zodiac" in clause
    assert params["intention_level"] == 3


def test_member_update_flags_are_writable() -> None:
    """超级管理的「置顶推荐 / 新人推荐」开关要真的能写，否则只是本地 state。"""
    from app.schemas.matchmaker_member_admin import MatchmakerMemberUpdate

    body = MatchmakerMemberUpdate(promoted=True, newcomer=False)
    assert body.promoted is True and body.newcomer is False
    # 未传时为 None，保持「不改这一列」的语义
    assert MatchmakerMemberUpdate(nickname="x").promoted is None


def test_member_filter_income_range_expands_to_bounds() -> None:
    """界面把收入做成区间下拉，必须落到上下限两个参数上，精确匹配选不中任何值。"""
    clause, params = build_member_filter({"income_min": 3000, "income_max": 8000}, ALL_COLUMNS)
    assert "p.income >= :income_min" in clause
    assert "p.income <= :income_max" in clause
    assert params == {"income_min": 3000, "income_max": 8000}

    # 区间只给一端时，另一端不应凭空产生条件
    clause, params = build_member_filter({"income_max": 3000}, ALL_COLUMNS)
    assert "p.income <= :income_max" in clause
    assert "income_min" not in params


def test_member_filter_new_dims_skip_missing_columns() -> None:
    values = {"intention_level": 1, "house": "有房", "ethnicity": "汉族",
              "constellation": "天秤座", "zodiac": "龙"}
    clause, params = build_member_filter(values, frozenset())
    for token in ("p.intention_level", "p.house", "p.ethnicity", "p.constellation", "p.zodiac"):
        assert token not in clause, token
    assert params == {}


@pytest.mark.asyncio
async def test_lead_filter_skips_missing_columns() -> None:
    db = StubDB([StubResult(rows=[]), StubResult(rows=[]), StubResult(scalar=0)])
    await leads.list_leads(
        db, 1, 20, None, None, None, None,
        xiangqin_status=2, bind_mode="bind", wechat_added=1,
        age_min=20, age_max=30, height_min=160, height_max=180,
        marriage="1", residence="上海", hometown="安徽",
        assign_start="2026-10-01", assign_end="2026-10-11",
    )
    clause = db.clauses()
    for token in ("xiangqin_status", "bind_mode", "wechat_added", "age_min", "height_max",
                  "residence", "hometown", "assign_start"):
        assert token not in clause, token


@pytest.mark.asyncio
async def test_lead_filter_applies_columns_when_available() -> None:
    db = StubDB([
        StubResult(rows=[("age",), ("height",), ("marriage",), ("education",), ("occupation",),
                         ("residence",), ("hometown",), ("xiangqin_status",), ("bind_mode",),
                         ("wechat_added",), ("assigned_at",)]),
        StubResult(rows=[]),
        StubResult(scalar=0),
    ])
    await leads.list_leads(
        db, 1, 20, None, None, None, None,
        xiangqin_status=2, bind_mode="bind", wechat_added=1,
        age_min=20, height_max=180, residence="上海",
        assign_start="2026-10-01", assign_end="2026-10-11",
    )
    clause = db.clauses()
    assert "xiangqin_status = :xiangqin_status" in clause
    assert "bind_mode = :bind_mode" in clause
    assert "wechat_added = :wechat_added" in clause
    assert "age >= :age_min" in clause
    assert "height <= :height_max" in clause
    assert "residence LIKE CONCAT('%', :residence, '%')" in clause
    assert "assigned_at >= :assign_start" in clause
    assert "assigned_at < DATE_ADD(:assign_end, INTERVAL 1 DAY)" in clause


# --------------------------------------------------------------------------- #
# 3. 枚举只映射到服务端写死的 SQL 片段
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("search_type", sorted(SEARCH_TYPE_SQL))
def test_member_search_type_maps_to_fixed_sql(search_type: str) -> None:
    clause, params = build_member_filter({"search": "abc", "search_type": search_type}, ALL_COLUMNS)
    assert params["search"] == "abc"
    assert params["search_id"] == -1  # 非数字时不按 ID 匹配
    if search_type == "code":
        assert MEMBER_CODE_EXPR in clause
    assert " :search" in clause or "= :search" in clause or ":search_id" in clause


def test_member_search_numeric_binds_id() -> None:
    _, params = build_member_filter({"search": "88"}, ALL_COLUMNS)
    assert params["search_id"] == 88


def test_member_behavior_type_maps_to_exists_predicate() -> None:
    clause, _ = build_member_filter({"behavior_type": "gift_sent"}, ALL_COLUMNS)
    assert "FROM user_gift_record gr WHERE gr.user_id = u.id" in clause


def test_member_behavior_type_unknown_is_ignored() -> None:
    clause, params = build_member_filter({"behavior_type": "DROP TABLE"}, ALL_COLUMNS)
    assert "DROP TABLE" not in clause
    assert params == {}


def test_member_sort_order_wins_over_sort_by() -> None:
    assert member_sort_sql("created_at", "earliest") == ORDER_SQL["earliest"]
    assert member_sort_sql("fetched_at", "latest") == ORDER_SQL["latest"]
    assert member_sort_sql("created_at", None) == SORT_SQL["created_at"]


def test_member_sort_unknown_value_falls_back() -> None:
    assert member_sort_sql("nonexistent", None) == SORT_SQL["created_at"]
    assert member_sort_sql(None, "nonexistent") == SORT_SQL["created_at"]


def test_member_sort_whitelist_covers_new_keys() -> None:
    assert {"entered_at", "assigned_at", "fetched_at"} <= set(SORT_SQL)


@pytest.mark.asyncio
async def test_lead_order_whitelist() -> None:
    db = StubDB([StubResult(rows=[]), StubResult(rows=[]), StubResult(scalar=0)])
    await leads.list_leads(db, 1, 20, None, None, None, None, order="earliest")
    assert db.statements[1][0].endswith("ORDER BY id ASC LIMIT :limit OFFSET :offset")

    db2 = StubDB([StubResult(rows=[]), StubResult(rows=[]), StubResult(scalar=0)])
    await leads.list_leads(db2, 1, 20, None, None, None, None, order="DROP")
    assert db2.statements[1][0].endswith("ORDER BY id DESC LIMIT :limit OFFSET :offset")


@pytest.mark.asyncio
async def test_lead_hide_today_predicates() -> None:
    db = StubDB([StubResult(rows=[]), StubResult(rows=[]), StubResult(scalar=0)])
    await leads.list_leads(db, 1, 20, None, None, None, None,
                           hide_followed_today=True, hide_called_today=True)
    clause = db.clauses()
    assert "customer_lead_follow_up fu" in clause
    assert "customer_lead_call_note cn" in clause


def test_offline_vip_service_status_and_sort_are_whitelisted() -> None:
    assert set(vip._SERVICE_STATUS_SQL) == {"active", "expired", "no_refund_risk"}
    assert set(vip._SORT_BY_SQL) == {
        "member_last_paid", "member_history_paid", "monthly_sales",
        "yearly_sales", "monthly_champion",
    }


@pytest.mark.asyncio
async def test_offline_vip_sales_sort_adds_aggregate_join() -> None:
    db = StubDB([StubResult(rows=[]), StubResult(scalar=0)])
    await vip.list_vips(db, 1, 20, None, None, None, None, None, None, None,
                        service_status="active", sort_by="monthly_sales")
    sql = db.sql()
    assert "v.service_end >= CURDATE()" in sql
    assert "COALESCE(_month.amount, 0) DESC" in sql
    assert "_month" in sql  # 销售额维度需要带汇总 join


@pytest.mark.asyncio
async def test_offline_vip_default_sort_has_no_extra_join() -> None:
    db = StubDB([StubResult(rows=[]), StubResult(scalar=0)])
    await vip.list_vips(db, 1, 20, None, None, None, None, None, None, None)
    assert "_month" not in db.sql()


# --------------------------------------------------------------------------- #
# 4. search_type：各列表接口的匹配目标
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_merchant_order_search_type_selects_column() -> None:
    db = StubDB([StubResult(rows=[]), StubResult(scalar=0)])
    await merchant_admin.list_orders(db, 1, 20, None, None, None, "1380013", None, None, None, None, "phone")
    assert "u.phone LIKE CONCAT('%', :kw, '%')" in db.clauses()

    db2 = StubDB([StubResult(rows=[]), StubResult(scalar=0)])
    await merchant_admin.list_orders(db2, 1, 20, None, None, None, "鲜花", None, None, None, None, "product")
    assert "p.name LIKE CONCAT('%', :kw, '%')" in db2.clauses()

    db3 = StubDB([StubResult(rows=[]), StubResult(scalar=0)])
    await merchant_admin.list_orders(db3, 1, 20, None, None, None, "鲜花", None, None, None, None, "DROP")
    assert "p.name LIKE CONCAT('%', :kw, '%')" in db3.clauses()


@pytest.mark.asyncio
async def test_short_video_comment_search_type() -> None:
    db = StubDB([StubResult(rows=[]), StubResult(scalar=0)])
    await video.list_comments(db, 1, 20, None, "关键词", "user")
    assert "u.nickname LIKE CONCAT('%', :kw, '%')" in db.clauses()

    db2 = StubDB([StubResult(rows=[]), StubResult(scalar=0)])
    await video.list_comments(db2, 1, 20, None, "关键词", "video")
    assert "v.description LIKE CONCAT('%', :kw, '%')" in db2.clauses()

    db3 = StubDB([StubResult(rows=[]), StubResult(scalar=0)])
    await video.list_comments(db3, 1, 20, None, "关键词", None)
    assert "c.content LIKE CONCAT('%', :kw, '%')" in db3.clauses()


@pytest.mark.asyncio
async def test_short_video_packet_search_type() -> None:
    db = StubDB([StubResult(rows=[]), StubResult(scalar=0)])
    await video.list_packets(db, 1, 20, None, "关键词", "sender")
    assert "p.sender_label LIKE CONCAT('%', :kw, '%')" in db.clauses()

    db2 = StubDB([StubResult(rows=[]), StubResult(scalar=0)])
    await video.list_packets(db2, 1, 20, None, "关键词", None)
    assert "v.description LIKE CONCAT('%', :kw, '%')" in db2.clauses()


@pytest.mark.asyncio
async def test_short_video_tip_search_type_aliases_search_by() -> None:
    db = StubDB([StubResult(rows=[]), StubResult(first=(0, 0))])
    await video.list_tips(db, 1, 20, "关键词", None, "video")
    assert "v.description LIKE CONCAT('%', :kw, '%')" in db.clauses()

    db2 = StubDB([StubResult(rows=[]), StubResult(first=(0, 0))])
    await video.list_tips(db2, 1, 20, "关键词", "video")
    assert "v.description LIKE CONCAT('%', :kw, '%')" in db2.clauses()


@pytest.mark.asyncio
async def test_member_media_search_type() -> None:
    db = StubDB([StubResult(rows=[]), StubResult(scalar=0)])
    await media.list_media(db, 1, 20, "avatar", None, "G000088", None, "code")
    assert media._MEMBER_SEARCH_TYPE_SQL["code"] in db.clauses()
    assert "u.nickname LIKE" not in db.clauses()

    db2 = StubDB([StubResult(rows=[]), StubResult(scalar=0)])
    await media.list_media(db2, 1, 20, "avatar", None, "138", None, "phone")
    assert "u.phone LIKE CONCAT('%', :kw, '%')" in db2.clauses()

    db3 = StubDB([StubResult(rows=[]), StubResult(scalar=0)])
    await media.list_media(db3, 1, 20, "avatar", None, "小雅", None, None)
    assert media._MEMBER_SEARCH_TYPE_ALL in db3.clauses()


@pytest.mark.asyncio
async def test_member_intro_search_type() -> None:
    db = StubDB([StubResult(rows=[]), StubResult(scalar=0)])
    await media.list_intros(db, 1, 20, "138", None, False, False, False, "phone")
    assert "u.phone LIKE CONCAT('%', :kw, '%')" in db.clauses()
    assert "u.nickname LIKE" not in db.clauses()


# --------------------------------------------------------------------------- #
# 5. 通用内容项：extra_json 筛选与 search_type
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_content_extra_filters_use_json_extract() -> None:
    db = StubDB([StubResult(scalar=0), StubResult(rows=[])])
    await admin_content.list_items(db, "single_page", 1, 20, None, None, {"type": "page"})
    clause = db.clauses()
    assert "JSON_UNQUOTE(JSON_EXTRACT(extra_json, '$.type')) = :extra_type" in clause
    assert db.statements[0][1]["extra_type"] == "page"


@pytest.mark.asyncio
async def test_content_extra_filter_keys_are_whitelisted() -> None:
    db = StubDB([StubResult(scalar=0), StubResult(rows=[])])
    await admin_content.list_items(db, "single_page", 1, 20, None, None, {"evil": "1 OR 1=1"})
    assert "evil" not in db.sql()
    assert "1 OR 1=1" not in db.sql()


@pytest.mark.asyncio
async def test_good_news_filters_map_to_safe_extra_json_keys() -> None:
    db = StubDB([StubResult(scalar=0), StubResult(rows=[])])
    await admin_content.list_items(
        db,
        "good_news",
        1,
        20,
        None,
        None,
        {"good_news_status": "牵手成功", "matchmaker_id": "12"},
    )
    sql = db.sql()
    assert "$.status" in sql
    assert "$.matchmaker_id" in sql
    assert db.statements[0][1]["extra_good_news_status"] == "牵手成功"
    assert db.statements[0][1]["extra_matchmaker_id"] == "12"

    other_domain = StubDB([StubResult(scalar=0), StubResult(rows=[])])
    await admin_content.list_items(
        other_domain,
        "activity",
        1,
        20,
        None,
        None,
        {"good_news_status": "牵手成功", "matchmaker_id": "12"},
    )
    assert "extra_json" not in other_domain.sql()


@pytest.mark.asyncio
async def test_group_signup_filters_are_structured_and_nickname_is_partial() -> None:
    db = StubDB([StubResult(scalar=0), StubResult(rows=[])])
    await admin_content.list_items(
        db,
        "group_signup",
        1,
        20,
        None,
        None,
        {"group_id": "3", "pay": "paid", "nick": "小雅"},
    )
    sql = db.sql()
    assert "$.group_id" in sql and "$.pay" in sql and "$.nick" in sql
    assert "LIKE :extra_nick" in sql
    assert db.statements[0][1]["extra_nick"] == "%小雅%"


@pytest.mark.asyncio
async def test_content_search_type_maps_to_extra_key() -> None:
    db = StubDB([StubResult(scalar=0), StubResult(rows=[])])
    await admin_content.list_items(db, "free_pay_order", 1, 20, "FP20261011", None, None, "order_no")
    assert "JSON_UNQUOTE(JSON_EXTRACT(extra_json, '$.order_no')) LIKE :kw" in db.clauses()

    db2 = StubDB([StubResult(scalar=0), StubResult(rows=[])])
    await admin_content.list_items(db2, "gift_exchange", 1, 20, "小雅", None, None, "nickname")
    assert "JSON_UNQUOTE(JSON_EXTRACT(extra_json, '$.member_nickname')) LIKE :kw" in db2.clauses()


@pytest.mark.asyncio
async def test_content_search_type_undefined_domain_falls_back() -> None:
    db = StubDB([StubResult(scalar=0), StubResult(rows=[])])
    await admin_content.list_items(db, "activity", 1, 20, "关键词", None, None, "order_no")
    assert "(title LIKE :kw OR subtitle LIKE :kw OR extra_json LIKE :kw)" in db.clauses()
    assert "JSON_EXTRACT" not in db.sql()


@pytest.mark.asyncio
async def test_content_domain_is_whitelisted() -> None:
    with pytest.raises(HTTPException) as exc:
        await admin_content.list_items(StubDB(), "unknown_domain")
    assert exc.value.status_code == 404


# --------------------------------------------------------------------------- #
# 6. 路由层确实暴露了清单要求的参数（防回归漏挂）
# --------------------------------------------------------------------------- #


def _query_params(func) -> set[str]:
    return {
        name
        for name, param in inspect.signature(func).parameters.items()
        if hasattr(param.default, "pattern") or param.default.__class__.__name__ == "Query"
    }


def test_member_route_exposes_new_filter_params() -> None:
    names = _query_params(crm_routes.members)
    for name in ("search_type", "source", "region", "xiangqin_status", "realname", "cert_status",
                 "organization_id", "matchmaker_id", "promoter_id", "age_min", "age_max",
                 "height_min", "height_max", "marriage", "education", "occupation", "income",
                 "residence", "hometown", "hukou", "religion", "car", "level", "tags",
                 "offline_vip", "promoted", "newcomer", "activity_signup", "wechat_added",
                 "behavior_type", "created_start", "created_end", "assigned_start",
                 "assigned_end", "order",
                 # 2026-10-16：会员资料页「客户意向 / 房子 / 民族 / 星座 / 属相」五列
                 # 在列表里可见却无查询参数，补参数前进不了筛选。
                 # 2026-10-17：另补收入的上下限（界面是区间下拉，不是精确匹配）。
                 "intention_level", "house", "ethnicity", "constellation", "zodiac",
                 "income_min", "income_max",
                 # A9/B3：会员侧弃海筛选（true仅弃海 / false排除弃海）。
                 "abandoned"):
        assert name in names, name


def test_lead_route_exposes_new_filter_params() -> None:
    names = _query_params(lead_routes.lead_list)
    for name in ("intention_level", "start_date", "end_date", "assign_start", "assign_end",
                 "promoter_id", "xiangqin_status", "bind_mode", "wechat_added",
                 "age_min", "age_max", "height_min", "height_max", "marriage", "education",
                 "occupation", "residence", "hometown", "hide_followed_today",
                 "hide_called_today", "has_phone", "has_wechat", "order",
                 # B11③（2026-10-24）：待分派 / 审核状态 / 入库情况三个死筛选下沉为真参数。
                 "assigned", "audit_status", "stored"):
        assert name in names, name


def test_other_routes_expose_new_params() -> None:
    assert {"service_status", "sort_by"} <= _query_params(vip_routes.list_offline_vips)
    assert {"type", "qrcode_type", "xiangqin_status", "search_type", "gender", "landing_id", "source"} <= _query_params(admin_content_routes.content_list)
    assert "search_type" in _query_params(merchant_routes.order_list)
    assert "search_type" in _query_params(merchant_routes.order_export)
    assert "search_type" in _query_params(video_routes.comment_list)
    assert "search_type" in _query_params(video_routes.tip_list)
    assert "search_type" in _query_params(video_routes.packet_list)
    assert "search_type" in _query_params(media_routes.media_list)
    assert "search_type" in _query_params(media_routes.intro_list)
    assert "cancellation_status" in _query_params(account_routes.accounts)


# --------------------------------------------------------------------------- #
# 4. 会员筛选下拉字典（门店 / 推广红娘）
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_member_options_return_scoped_stores_and_promoters() -> None:
    """下拉字典只给当前账号数据范围内的门店，推广红娘只认清招牌应用记录。"""
    from types import SimpleNamespace

    from app.services.matchmaker_member_admin import member_options

    def scope_organization_clause(params, *, column):
        params["scope_org"] = 7
        return f"{column} IN (:scope_org)"

    admin = SimpleNamespace(scope_organization_clause=scope_organization_clause)
    db = StubDB([
        StubResult(rows=[{"id": 3, "label": "总店"}]),
        StubResult(rows=[{"id": 9, "label": "张三"}]),
    ])
    result = await member_options(db, admin=admin)

    assert [o.value for o in result.organizations] == ["3"]
    assert [o.label for o in result.organizations] == ["总店"]
    assert [o.value for o in result.promoters] == ["9"]
    # 门店查询必须真的带上数据范围谓词与其绑定参数，不能只取谓词丢掉参数。
    store_sql, store_params = db.statements[0]
    assert "o.org_type = 'store'" in store_sql
    assert "o.id IN (:scope_org)" in store_sql
    assert store_params["scope_org"] == 7
    assert "application_type = 'promoter'" in db.statements[1][0]


def test_member_options_route_is_registered_before_member_id() -> None:
    """静态路径 /options 必须排在 /{member_id} 之前，否则会被当成会员 ID 解析。"""
    from app.api.routes import matchmaker_member_admin as routes

    paths = [route.path for route in routes.router.routes]
    assert "/admin/matchmaker/members/options" in paths
    assert paths.index("/admin/matchmaker/members/options") < paths.index(
        "/admin/matchmaker/members/{member_id}"
    )


# --------------------------------------------------------------------------- #
# 7. B11（2026-10-24）：客源缺失筛选 + free_form extra 筛选下沉为正式参数
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_lead_assigned_audit_stored_filters_bind_params() -> None:
    """assigned=False→待分派(matchmaker_id IS NULL)；audit_status/stored 走绑定参数。"""
    db = StubDB([
        StubResult(rows=[("audit_status",)]),
        StubResult(rows=[]),
        StubResult(scalar=0),
    ])
    await leads.list_leads(
        db, 1, 20, None, None, None, None,
        assigned=False, audit_status="pending", stored=True,
    )
    clause = db.clauses()
    assert "matchmaker_id IS NULL" in clause
    assert "audit_status = :audit_status" in clause
    assert "converted_user_id IS NOT NULL" in clause
    # 取值只进绑定参数，不拼接入 SQL。
    assert db.statements[1][1]["audit_status"] == "pending"


@pytest.mark.asyncio
async def test_lead_assigned_true_filters_assigned_only() -> None:
    db = StubDB([
        StubResult(rows=[("audit_status",)]),
        StubResult(rows=[]),
        StubResult(scalar=0),
    ])
    await leads.list_leads(db, 1, 20, None, None, None, None, assigned=True, stored=False)
    clause = db.clauses()
    assert "matchmaker_id IS NOT NULL" in clause
    assert "converted_user_id IS NULL" in clause


@pytest.mark.asyncio
async def test_free_form_gender_landing_source_use_json_extract() -> None:
    db = StubDB([StubResult(scalar=0), StubResult(rows=[])])
    await admin_content.list_items(
        db, "free_form", 1, 20, None, None,
        {"gender": "1", "landing_id": "88", "source": "抖音"},
    )
    clause = db.clauses()
    assert "JSON_UNQUOTE(JSON_EXTRACT(extra_json, '$.gender')) = :extra_gender" in clause
    assert "JSON_UNQUOTE(JSON_EXTRACT(extra_json, '$.landing_id')) = :extra_landing_id" in clause
    assert "JSON_UNQUOTE(JSON_EXTRACT(extra_json, '$.source')) = :extra_source" in clause
    assert db.statements[0][1]["extra_source"] == "抖音"
    assert db.statements[0][1]["extra_landing_id"] == "88"


@pytest.mark.asyncio
async def test_free_form_extra_filters_ignored_on_other_domains() -> None:
    """gender/landing_id/source 只对 free_form 域生效，其他域忽略（防跨域误筛）。"""
    db = StubDB([StubResult(scalar=0), StubResult(rows=[])])
    await admin_content.list_items(
        db, "activity", 1, 20, None, None,
        {"gender": "1", "landing_id": "88", "source": "抖音"},
    )
    sql = db.sql()
    for token in ("extra_gender", "extra_landing_id", "extra_source"):
        assert token not in sql


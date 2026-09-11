"""M3-4 会员CRM 数据报表：路由注册、鉴权、意向目录与新增数据源的静态断言。

本用例不连数据库，只做 OpenAPI 路径断言、鉴权断言与源码/常量断言。
"""

from __future__ import annotations

import inspect
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.services import admin_home

client = TestClient(app)

ROOT = Path(__file__).resolve().parents[1]
BASE = "/api/v1/admin/member-statistics"


# ─── 路由注册与鉴权 ─────────────────────────────────────────────


def test_member_statistics_route_registered() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "get" in paths[BASE]


def test_member_statistics_requires_authentication() -> None:
    # 未登录（无红娘后台 Token）→ 401
    assert client.get(BASE).status_code == 401


# ─── 服务函数与意向目录 ─────────────────────────────────────────


def test_member_statistics_is_coroutine() -> None:
    assert inspect.iscoroutinefunction(admin_home.member_statistics)


def test_intention_catalog_is_fixed_nine() -> None:
    catalog = admin_home.MEMBER_INTENTION_CATALOG
    assert isinstance(catalog, list)
    assert len(catalog) == 9
    assert catalog[0] == "A类未接"
    assert catalog[-1] == "J类放弃资源"
    # 与前端「会员意向统计」固定行一致
    assert catalog == [
        "A类未接", "B类初步沟通", "C类深入沟通未缔结", "D类待确定到店时间",
        "E类已确定到店", "F类预约需二邀", "G类已到店未签约", "I类已签单", "J类放弃资源",
    ]


# ─── 新增数据源（择偶职业）静态断言 ─────────────────────────────


def test_preferred_occupation_column_declared() -> None:
    source = (ROOT / "database_setup_marriage.py").read_text(encoding="utf-8")
    # 建表语句
    assert "`preferred_occupation` varchar(128)" in source
    # 幂等补列（旧库升级）
    assert '"preferred_occupation": "`preferred_occupation`' in source


def test_preference_report_includes_occupation() -> None:
    source = (ROOT / "app" / "services" / "admin_home.py").read_text(encoding="utf-8")
    assert "preference.preferred_occupation" in source
    assert '"occupation": "职业"' in source


def test_growth_projects_gender_split_and_peaks() -> None:
    source = (ROOT / "app" / "services" / "admin_home.py").read_text(encoding="utf-8")
    # 会员增长：男/女拆分需在内层子查询选中并在外层投影
    assert "COALESCE(SUM(users.gender = 1), 0) male_count" in source
    assert "COALESCE(registered.male_count, 0) male_count" in source
    # 单日/单月新增峰值指标
    assert '"max_daily_members"' in source
    assert '"max_monthly_members"' in source

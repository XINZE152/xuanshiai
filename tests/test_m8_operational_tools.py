"""M8 运营工具 — admin_content 域白名单 + 配置域默认值 + 端点契约测试。

M8 涵盖 22 个运营工具页面，统一走 admin_content 通用 CRUD + tools_* 配置域。
本测试聚焦：
1. 新增的 8 个域在 ALLOWED_DOMAINS 中
2. 关键 tools_* 配置域已重写默认值
3. 通用端点对每个 M8 域都注册
"""

from __future__ import annotations

import ast
import inspect


def _m8_domains() -> list[str]:
    return [
        "good_news",
        "good_news_pennant",
        "community_group",
        "group_signup",
        "interactive_message",
        "tweet_task",
        "sms_broadcast",
        "sms_send_record",
    ]


def test_m8_domains_registered() -> None:
    """ALLOWED_DOMAINS 包含 M8 新增的 8 个域"""
    from app.services.admin_content import ALLOWED_DOMAINS

    domains = _m8_domains()
    missing = [d for d in domains if d not in ALLOWED_DOMAINS]
    assert not missing, f"M8 域缺失: {missing}"


def test_m8_config_domains_have_defaults() -> None:
    """M8-A 配置域 tools_* 在 DEFAULT_CONFIGS 中存在且已重写"""
    from app.services.admin_config import DEFAULT_CONFIGS

    expected = {
        "tools_good_news",
        "tools_column_config",
        "tools_interactive_function",
        "tools_interactive_content",
        "tools_sales_match",
    }
    missing = [d for d in expected if d not in DEFAULT_CONFIGS]
    assert not missing, f"M8 工具配置域缺失: {missing}"

    # 验证 tools_sales_match 含 nav_items / show_* 字段
    sales_match = DEFAULT_CONFIGS["tools_sales_match"]
    payload = sales_match[2]
    assert "nav_items" in payload, "tools_sales_match 必须有 nav_items 字段"
    assert isinstance(payload["nav_items"], list) and len(payload["nav_items"]) == 4
    for field in ("show_banner", "show_auth", "show_intro", "show_mate_req", "show_material", "show_person_intro"):
        assert field in payload, f"tools_sales_match 缺少字段 {field}"

    # 验证 tools_good_news 含 categories 与 blessings
    good_news = DEFAULT_CONFIGS["tools_good_news"]
    gn_payload = good_news[2]
    assert isinstance(gn_payload["categories"], list) and len(gn_payload["categories"]) >= 8
    assert isinstance(gn_payload["blessings"], list) and len(gn_payload["blessings"]) >= 3


def test_m8_routes_registered() -> None:
    """M8 域的 GET/POST/PATCH/DELETE 端点已注册到 api_router"""
    import app.api.router as r

    content_router = r.admin_content.router
    paths = [getattr(rt, "path", "") for rt in content_router.routes]

    # 通用模板：/admin/content/{domain} 和 /admin/content/{domain}/{item_id}
    assert any(p == "/admin/content/{domain}" for p in paths), "缺少通用 content 列表路径模板"
    assert any(p == "/admin/content/{domain}/{item_id}" for p in paths), "缺少通用 content 详情路径模板"
    # 4 个方法都注册
    method_counts = sum(len(getattr(rt, "methods", set())) for rt in content_router.routes)
    assert method_counts >= 4, "admin_content 端点方法不足"


def test_m8_database_setup_has_no_new_tables() -> None:
    """M8 不新增业务表，全部走 admin_content_item"""
    import database_setup_marriage as setup

    cls = next(
        (c for c in dir(setup) if c.endswith("Manager") and not c.startswith("_")),
        None,
    )
    assert cls is not None, "database_setup_marriage 找不到 Manager 类"
    source = inspect.getsource(getattr(setup, cls))

    # M8 应不引入新表
    forbidden = [
        "CREATE TABLE IF NOT EXISTS `good_news`",
        "CREATE TABLE IF NOT EXISTS `community_group`",
        "CREATE TABLE IF NOT EXISTS `lovecard_batch`",
        "CREATE TABLE IF NOT EXISTS `fan_qrcode`",
        "CREATE TABLE IF NOT EXISTS `sms_broadcast`",
    ]
    leaks = [t for t in forbidden if t in source]
    assert not leaks, f"M8 不应建专用表，实际新增了: {leaks}"


def test_m8_admin_content_extra_keyword_match() -> None:
    """admin_content.list_items 的 keyword 检索同时作用于 extra_json（喜讯/锦旗字段搜索）"""
    import inspect

    from app.services.admin_content import list_items

    source = inspect.getsource(list_items)
    assert "extra_json LIKE" in source, "admin_content 列表查询应支持 extra_json 模糊匹配"


def test_m8_syntax_all_files() -> None:
    """M8 修改/新增的所有 .py 文件语法检查"""
    files = [
        "app/services/admin_content.py",
        "app/services/admin_config.py",
        "app/api/routes/admin_content.py",
    ]
    for f in files:
        try:
            ast.parse(open(f, encoding="utf-8").read())
        except SyntaxError as e:
            raise AssertionError(f"{f} 语法错误: {e}")

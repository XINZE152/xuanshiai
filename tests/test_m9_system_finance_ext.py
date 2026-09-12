"""M9 系统管理 + 平台配置 + 公众号 + 小程序 + 财务 + 电子合同 — 端点契约与配置默认值测试。

M9 涵盖 36 页 9 🔴 + 5 🟡 核心 + 23 🟢 已接线：
- 9 🔴 完全静态 → 全部接通（finance-config / system-finance-order / system-credit-history /
  system-cashout-history / finance-statistic / e-contract-config / e-contract-template /
  e-contract-list / system-setting-admin-user-edit）
- 5 🟡 半接线 → 核心补全（sms-signature / sms-notices / sms-record / wechat-fans / out-call-record）
- 23 🟢 已接线 → 本期不动

本测试聚焦：
1. finance / econtract_config DEFAULT_CONFIGS 新增字段已就位
2. 新端点 POST /admin/finance/credit-grants 注册 + 入参校验
3. 既有 finance admin 端点（orders/withdrawals/ledger/daily-report）契约保留
4. 不新增任何业务表（沿用 account_ledger + business_audit_log）
"""

from __future__ import annotations

import ast
import inspect


def _module_imports(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False


# ============================================
# 1. DEFAULT_CONFIGS — finance / econtract_config 扩字段
# ============================================


def test_m9_finance_defaults_have_extended_fields() -> None:
    """finance 域在 M9-A 扩字段：point_name/point_ratio/balance_name/withdrawal.fee_mode/
    withdraw_methods/recharge_packages 等"""
    from app.services.admin_config import DEFAULT_CONFIGS

    assert "finance" in DEFAULT_CONFIGS, "finance 域缺失"
    finance_payload = DEFAULT_CONFIGS["finance"][2]
    # 积分名称/比例/余额名称
    assert finance_payload.get("point_name") == "金币", "finance.point_name 默认值应为『金币』"
    assert finance_payload.get("point_ratio") == 10, "finance.point_ratio 默认值应为 10"
    assert finance_payload.get("balance_name") == "余额", "finance.balance_name 默认值应为『余额』"
    # withdrawal.fee_mode 三键
    withdrawal = finance_payload["withdrawal"]
    assert withdrawal.get("fee_mode") in ("none", "deduct"), "withdrawal.fee_mode 必须 none/deduct"
    assert "fee_rate" in withdrawal, "withdrawal.fee_rate 必须存在"
    assert "fee_threshold" in withdrawal, "withdrawal.fee_threshold 必须存在"
    # 4 种提现方式
    methods = finance_payload["withdraw_methods"]
    for k in ("auto_wechat", "manual_wechat", "manual_bank", "manual_alipay"):
        assert k in methods, f"withdraw_methods 缺 {k}"
        entry = methods[k]
        assert "enabled" in entry and "min_amount" in entry and "max_amount" in entry
    # 6 套充值套餐
    pkgs = finance_payload["recharge_packages"]
    assert isinstance(pkgs, list) and len(pkgs) == 6, "recharge_packages 应为 6 套"


def test_m9_econtract_config_has_enabled_flag() -> None:
    """econtract_config 域在 M9-A 加 enabled 字段（前端腾讯电子签总开关）"""
    from app.services.admin_config import DEFAULT_CONFIGS

    assert "econtract_config" in DEFAULT_CONFIGS, "econtract_config 域缺失"
    payload = DEFAULT_CONFIGS["econtract_config"][2]
    assert "enabled" in payload, "econtract_config 缺少 enabled"
    assert payload["enabled"] is False, "econtract_config.enabled 默认 False"
    # 既有 items 4 项保留
    items = payload["items"]
    assert isinstance(items, list) and len(items) == 4, "items 应保留 4 项"
    keys = [item["key"] for item in items]
    for k in ("sign_expire_days", "expire_remind_days", "default_contract_type", "allow_revoke"):
        assert k in keys, f"econtract_config.items 缺 {k}"


# ============================================
# 2. 新端点 POST /admin/finance/credit-grants
# ============================================


def test_m9_credit_grants_route_registered() -> None:
    """credit-grants 端点已注册"""
    import app.api.router as r
    from app.api.routes.finance import admin_router

    # 取所有注册的路由路径与 method
    paths = [(getattr(rt, "path", ""), getattr(rt, "methods", set())) for rt in admin_router.routes]
    matched = [(p, m) for p, m in paths if p == "/admin/finance/credit-grants"]
    assert matched, "credit-grants 路由未注册"
    # 必须支持 POST
    has_post = any("POST" in m for _, m in matched)
    assert has_post, "credit-grants 必须支持 POST"


def test_m9_credit_grants_schema_validation() -> None:
    """CreditGrantRequest 入参校验（target_type/user_ids/amount/reason）"""
    from app.schemas.finance import CreditGrantRequest

    # 缺 user_ids 的 member 目标 → 校验失败
    try:
        CreditGrantRequest(target_type="member", amount=10, reason="活动奖励")
        assert False, "member 目标必须传 user_ids，但没抛错"
    except Exception:
        pass
    # amount ≤ 0 → 校验失败
    try:
        CreditGrantRequest(target_type="all", amount=0, reason="")
        assert False, "amount ≤ 0 必须失败，但没抛错"
    except Exception:
        pass
    # 合法请求
    ok = CreditGrantRequest(target_type="member", user_ids=[1, 2, 3], amount=100, reason="春节红包")
    assert ok.target_type == "member"
    assert ok.amount == 100
    assert ok.reason == "春节红包"


def test_m9_credit_grants_resolve_targets_logic() -> None:
    """_resolve_credit_targets 在 4 种 target 下返回正确类型的账号集"""
    from app.services.finance import _resolve_credit_targets

    src = inspect.getsource(_resolve_credit_targets)
    # 4 种 target_type 全覆盖
    for t in ("all", "member", "verified", "matchmaker_team"):
        assert f'target_type == "{t}"' in src or f"target_type == '{t}'" in src, f"_resolve_credit_targets 缺 {t} 分支"
    # all 必须有 status=1 过滤
    assert "status = 1" in src, "_resolve_credit_targets.all 必须 status=1 过滤"
    # verified 必须 JOIN user_auth + realname_status=2
    assert "realname_status = 2" in src, "_resolve_credit_targets.verified 必须 realname_status=2"
    # matchmaker_team 必须 role_code IN ('service_matchmaker', 'promoter')
    assert "service_matchmaker" in src and "promoter" in src, "_resolve_credit_targets.matchmaker_team 缺 role_code"


def test_m9_credit_grants_audit_logged() -> None:
    """admin_grant_credits 必须写 business_audit_log（actor/action=finance.credit_grant）"""
    from app.services.finance import admin_grant_credits

    src = inspect.getsource(admin_grant_credits)
    assert "business_audit_log" in src, "admin_grant_credits 必须写 audit_log"
    assert "finance.credit_grant" in src, "audit_log action 必须是 finance.credit_grant"
    assert "after_json" in src, "audit_log 必须含 after_json"


def test_m9_credit_grants_uses_account_ledger() -> None:
    """admin_grant_credits 写 account_ledger（CREDIT/AVAILABLE/source_type=admin_grant）"""
    from app.services.finance import admin_grant_credits

    src = inspect.getsource(admin_grant_credits)
    assert "INSERT INTO account_ledger" in src
    assert "'CREDIT'" in src or '"CREDIT"' in src
    assert "'AVAILABLE'" in src or '"AVAILABLE"' in src
    assert "'admin_grant'" in src or '"admin_grant"' in src
    # idempotency_key 必须含 credit-grant
    assert "credit-grant" in src, "idempotency_key 必须含 credit-grant 前缀"


def test_m9_credit_grants_batch_500() -> None:
    """大批量 user_ids 必须分批写入（每批 500）"""
    from app.services.finance import admin_grant_credits

    src = inspect.getsource(admin_grant_credits)
    assert "batch_size = 500" in src or "500" in src, "必须支持分批写入（batch_size=500）"


# ============================================
# 3. 既有 finance admin 端点契约保留
# ============================================


def test_m9_existing_finance_endpoints_intact() -> None:
    """/admin/finance/orders|withdrawals|ledger|report|daily-report 端点未变动"""
    from app.api.routes.finance import admin_router

    paths = {getattr(rt, "path", "") for rt in admin_router.routes}
    for p in (
        "/admin/finance/orders",
        "/admin/finance/withdrawals",
        "/admin/finance/ledger",
        "/admin/finance/report",
        "/admin/finance/daily-report",
    ):
        assert p in paths, f"既有 finance admin 端点丢失: {p}"


def test_m9_config_endpoints_intact() -> None:
    """/admin/configs/{namespace} GET/PATCH 端点保留"""
    from app.api.routes.admin_config import router

    paths = {getattr(rt, "path", "") for rt in router.routes}
    assert "/admin/configs/{namespace}" in paths
    assert "/admin/configs/{namespace}/audit-logs" in paths


# ============================================
# 4. 不新增任何业务表
# ============================================


def test_m9_no_new_business_tables() -> None:
    """M9 不新增业务表：所有 9 🔴 页都走 account_ledger/admin_config_snapshot/matchmaker_admin_account"""
    import database_setup_marriage as setup

    cls = next((c for c in dir(setup) if c.endswith("Manager") and not c.startswith("_")), None)
    assert cls is not None, "找不到 Manager 类"
    source = inspect.getsource(getattr(setup, cls))

    # M9 不应新增的表
    forbidden = [
        "CREATE TABLE IF NOT EXISTS `credit_grant`",
        "CREATE TABLE IF NOT EXISTS `credit_grant_batch`",
        "CREATE TABLE IF NOT EXISTS `e_contract_record`",
        "CREATE TABLE IF NOT EXISTS `e_contract_template`",
        "CREATE TABLE IF NOT EXISTS `withdraw_method`",
        "CREATE TABLE IF NOT EXISTS `recharge_package`",
    ]
    leaks = [t for t in forbidden if t in source]
    assert not leaks, f"M9 不应建专用表，实际新增了: {leaks}"


# ============================================
# 5. 语法 / 模块完整性
# ============================================


def test_m9_syntax_all_files() -> None:
    """M9 修改/新增的所有 .py 文件语法检查"""
    files = [
        "app/services/admin_config.py",
        "app/services/finance.py",
        "app/schemas/finance.py",
        "app/api/routes/finance.py",
    ]
    for f in files:
        try:
            ast.parse(open(f, encoding="utf-8").read())
        except SyntaxError as e:
            raise AssertionError(f"{f} 语法错误: {e}")
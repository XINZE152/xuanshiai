"""2026-10-24 会员生命周期缺口补全回归（A12：积分排序 / 账号软删除 / 一键刷新属地）。

覆盖：
- 积分排序：``SORT_SQL`` 新增 ``points``/``integral``，走 ``user_points`` 子查询表达式；
  路由 ``sort_by`` 枚举放行 ``points|integral``。
- 会员软删除：列表/详情默认排除 ``users.deleted_at``；``batch-delete`` 与单条 ``DELETE``
  为软删（UPDATE deleted_at，不物理删），敏感写（幂等+二次确认）+ D-16 作用域收口。
- 一键刷新属地：provider 门控（disabled→503、real→501、mock→干跑不写库）；
  三端点注册；config ``region_resolver_provider`` 默认 disabled 且生产禁 mock。
"""

from __future__ import annotations

import inspect

from app.api.routes import matchmaker_crm_admin as crm
from app.services.member_crm_filter import MEMBER_POINTS_EXPR, SORT_SQL
from app.schemas.matchmaker_crm_admin import MemberDetail


# --------------------------------------------------------------------------- #
# 积分排序
# --------------------------------------------------------------------------- #
def test_points_sort_whitelisted_and_uses_user_points() -> None:
    assert "points" in SORT_SQL and "integral" in SORT_SQL
    assert "user_points" in MEMBER_POINTS_EXPR
    assert "user_points" in SORT_SQL["points"]
    assert "user_points" in SORT_SQL["integral"]


def test_route_sort_by_accepts_points_and_integral() -> None:
    assert "points|integral" in inspect.getsource(crm.members)


# --------------------------------------------------------------------------- #
# 会员软删除（列已存在，复用 users.deleted_at）
# --------------------------------------------------------------------------- #
def test_list_excludes_soft_deleted() -> None:
    assert "u.deleted_at IS NULL" in inspect.getsource(crm._member_query)


def test_detail_excludes_soft_deleted() -> None:
    src = inspect.getsource(crm.member_detail)
    assert "deleted_guard" in src and "u.deleted_at IS NULL" in src


def test_batch_delete_is_soft_and_sensitive() -> None:
    src = inspect.getsource(crm.batch_member_delete)
    assert "UPDATE users SET deleted_at" in src
    assert "DELETE FROM users" not in src          # 绝不物理删
    assert "require_sensitive_operation" in src     # 幂等 + 二次确认
    assert "FOR UPDATE" in src
    assert "scope_exists_clause" in src             # D-16 收口
    assert "member.batch_delete" in src


def test_single_delete_classifies_404_409() -> None:
    src = inspect.getsource(crm.member_delete)
    assert "UPDATE users SET deleted_at" in src
    assert "404" in src and "409" in src
    assert "scope_exists_clause" in src


def test_delete_routes_registered() -> None:
    paths = {(r.path, tuple(sorted(r.methods))) for r in crm.router.routes}
    assert ("/admin/matchmaker/members/batch-delete", ("POST",)) in paths
    assert ("/admin/matchmaker/members/{member_id}", ("DELETE",)) in paths


# --------------------------------------------------------------------------- #
# 一键刷新属地（provider 门控骨架）
# --------------------------------------------------------------------------- #
def test_region_refresh_gating_and_dry_run() -> None:
    src = inspect.getsource(crm._refresh_region)
    assert "region_resolver_provider" in src
    assert "503" in src and "501" in src
    assert "dry_run=True" in src                    # mock 不伪造真实地理、不写库
    assert "UPDATE users SET ip_region" not in src  # 干跑阶段绝不落库


def test_region_routes_registered_before_detail() -> None:
    order = [r.path for r in crm.router.routes]
    detail_idx = order.index("/admin/matchmaker/members/{member_id}")
    for static in (
        "/admin/matchmaker/members/region-resolver",
        "/admin/matchmaker/members/refresh-ip-region",
        "/admin/matchmaker/members/refresh-phone-region",
    ):
        assert static in order and order.index(static) < detail_idx


def test_config_region_provider_default_disabled() -> None:
    from app.core.config import Settings

    field = Settings.model_fields["region_resolver_provider"]
    assert field.default == "disabled"


def test_config_blocks_mock_in_production() -> None:
    src = inspect.getsource(__import__("app.core.config", fromlist=["Settings"]))
    assert "属地解析 Mock Provider" in src


# --------------------------------------------------------------------------- #
# 扣减积分（A12-积分修复）：与发放积分对称，写 user_points 负数行，敏感写
# --------------------------------------------------------------------------- #
def test_deduct_credits_writes_user_points_and_sensitive() -> None:
    from app.services import finance as finance_svc

    src = inspect.getsource(finance_svc.admin_deduct_credits)
    assert "INSERT INTO user_points" in src     # 改写 C 端积分账本
    assert "INSERT INTO account_ledger" not in src  # 不再写余额账本
    assert "admin_grant" in src                 # 与发放同账本同 source_type
    assert "FOR UPDATE" in src                  # 行锁
    assert "409" in src                         # 可用不足整批 409
    assert "operator_id" in src                 # 记录操作人
    assert "finance.credit_deduct" in src       # 审计


def test_deduct_route_registered_and_sensitive() -> None:
    from app.api.routes import finance as finance_route

    src = inspect.getsource(finance_route.deduct_credits)
    assert "require_sensitive_operation" in src
    assert "begin_sensitive_operation" in src and "abort_sensitive_operation" in src
    paths = {(r.path, tuple(sorted(r.methods))) for r in finance_route.admin_router.routes}
    assert ("/admin/finance/credit-deductions", ("POST",)) in paths


# --------------------------------------------------------------------------- #
# 微信绑定/解绑（第389行）：敏感写 + openid 唯一 + 解绑防锁死
# --------------------------------------------------------------------------- #
def test_wechat_bind_checks_openid_uniqueness() -> None:
    src = inspect.getsource(crm.member_wechat_bind)
    assert "UPDATE users SET" in src and "openid=:openid" in src
    assert "已绑定其他会员" in src          # openid 全局唯一 409
    assert "require_sensitive_operation" in src
    assert "scope_exists_clause" in src


def test_wechat_unbind_blocks_lockout() -> None:
    src = inspect.getsource(crm.member_wechat_unbind)
    assert "openid=NULL" in src            # 解绑置空
    assert "无法登录" in src                # 无手机号时 409 防锁死
    assert "未绑定微信" in src
    assert "require_sensitive_operation" in src


def test_wechat_binding_routes_registered() -> None:
    paths = {(r.path, tuple(sorted(r.methods))) for r in crm.router.routes}
    assert ("/admin/matchmaker/members/{member_id}/wechat-binding", ("POST",)) in paths
    assert ("/admin/matchmaker/members/{member_id}/wechat-binding", ("DELETE",)) in paths


def test_member_detail_exposes_wechat_bound() -> None:
    assert "wechat_bound" in MemberDetail.model_fields

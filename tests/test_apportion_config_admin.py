"""单元测试：总店红娘 → 分派配置 后台 API。

测试策略（参照 test_community_message_admin.py / test_admin_home_routes.py）：
- OpenAPI 路径注册校验；
- 未登录 401；
- Pydantic model_validator 互斥校验；
- Service 函数级单元测试（不依赖数据库）。
"""

from datetime import datetime

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api import routes as api_routes_module
from app.api.dependencies import CurrentMatchmakerAdmin, get_current_matchmaker_admin
from app.api.routes import apportion_config_admin
from app.main import app
from app.schemas.matchmaker_admin import (
    ApportionAbandonUpdate,
    ApportionAssignUpdate,
    ApportionConfig,
    ApportionConfigAuditLogPage,
    MatchmakerAdminAccount,
)


client = TestClient(app)


def _admin(permissions: set[str] | None = None) -> CurrentMatchmakerAdmin:
    return CurrentMatchmakerAdmin(
        account=MatchmakerAdminAccount(
            id=1,
            username="admin",
            display_name="Admin",
            matchmaker_user_id=7,
            data_scope="ALL",
            organization_id=10,
            status=1,
            last_login_at=datetime(2026, 9, 8),
        ),
        session_id=1,
        permissions=frozenset(permissions or set()),
    )


def test_apportion_config_routes_are_registered() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    base = "/api/v1/admin/matchmaker/apportion-config"
    assert "get" in paths[base]
    assert "get" in paths[f"{base}/audit-log"]
    assert "get" in paths[f"{base}/{{scope}}/{{config_type}}"]
    assert "patch" in paths[f"{base}/{{scope}}/{{config_type}}/toggle"]
    assert "put" in paths[f"{base}/{{scope}}/assign"]
    assert "put" in paths[f"{base}/{{scope}}/abandon"]


def test_apportion_config_endpoints_require_authentication() -> None:
    assert client.get("/api/v1/admin/matchmaker/apportion-config").status_code == 401
    assert client.get("/api/v1/admin/matchmaker/apportion-config/audit-log").status_code == 401
    assert (
        client.get("/api/v1/admin/matchmaker/apportion-config/member_crm/assign").status_code == 401
    )
    assert (
        client.put(
            "/api/v1/admin/matchmaker/apportion-config/member_crm/assign",
            json={"strategy": "none"},
        ).status_code
        == 401
    )


def test_assign_designated_requires_target_matchmaker() -> None:
    with pytest.raises(ValidationError) as exc:
        ApportionAssignUpdate(strategy="designated")
    assert "target_matchmaker_id" in str(exc.value)


def test_assign_non_designated_rejects_target_matchmaker() -> None:
    with pytest.raises(ValidationError) as exc:
        ApportionAssignUpdate(strategy="none", target_matchmaker_id=99)
    assert "target_matchmaker_id" in str(exc.value)


def test_assign_default_valid_payload() -> None:
    body = ApportionAssignUpdate(strategy="none")
    assert body.is_enabled is True
    assert body.target_matchmaker_id is None


def test_abandon_days_whitelist_enforced() -> None:
    # 合法白名单
    for days in (0, 3, 7, 15, 30, 45, 60, 90):
        ApportionAbandonUpdate(auto_abandon_days=days, daily_pickup_limit=10)
    # 非法
    for bad in (1, 2, 5, 14, 29, 91, 100):
        with pytest.raises(ValidationError):
            ApportionAbandonUpdate(auto_abandon_days=bad)


def test_abandon_pickup_limit_must_be_non_negative() -> None:
    ApportionAbandonUpdate(auto_abandon_days=7, daily_pickup_limit=0)
    ApportionAbandonUpdate(auto_abandon_days=7, daily_pickup_limit=100000)
    with pytest.raises(ValidationError):
        ApportionAbandonUpdate(auto_abandon_days=7, daily_pickup_limit=-1)
    with pytest.raises(ValidationError):
        ApportionAbandonUpdate(auto_abandon_days=7, daily_pickup_limit=100001)


def test_admin_permission_write_required_for_put() -> None:
    """无写权限时 CurrentMatchmakerAdmin.require 必须 403。"""

    admin = _admin(permissions={"matchmaker.apportion.read"})
    with pytest.raises(HTTPException) as exc:
        admin.require("matchmaker.apportion.write")
    assert exc.value.status_code == 403


def test_admin_permission_read_alias_to_write() -> None:
    admin = _admin(permissions={"matchmaker.apportion.write"})
    admin.require("matchmaker.apportion.read")  # read 应作为 write 的别名通过


def test_get_current_matchmaker_admin_dependency_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """确保依赖可被覆写 + service 可被 mock，方便后续单测扩展。"""

    fake_payload = ApportionConfig(
        id=1, scope="member_crm", config_type="assign",
        strategy="none", target_matchmaker_id=None, target_matchmaker_name=None,
        auto_abandon_days=None, daily_pickup_limit=None,
        show_admin_abandoned_in_pool=True, show_store_abandoned_in_pool=True,
        is_enabled=True, updated_by=None, remark=None,
        created_at=datetime(2026, 9, 8), updated_at=datetime(2026, 9, 8),
    )

    async def fake_get_config(db, scope, config_type):
        return fake_payload

    async def fake_admin() -> CurrentMatchmakerAdmin:
        return _admin(permissions={"matchmaker.apportion.read", "matchmaker.apportion.write"})

    monkeypatch.setattr(apportion_config_admin, "get_config", fake_get_config)
    app.dependency_overrides[get_current_matchmaker_admin] = fake_admin
    try:
        response = client.get(
            "/api/v1/admin/matchmaker/apportion-config/member_crm/assign"
        )
        assert response.status_code == 200
        assert response.json()["scope"] == "member_crm"
        assert response.json()["strategy"] == "none"
    finally:
        app.dependency_overrides.pop(get_current_matchmaker_admin, None)
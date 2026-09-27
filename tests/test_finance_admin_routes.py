"""回归测试：财务后台路由接线（F821 修复回归）。

背景：app/api/routes/finance.py 的 grant_credits handler 调用 admin_grant_credits
但导入块漏了该名字，app/api/routes/merchant_admin.py 的 order_detail 使用
sqlalchemy.text 同样未导入，两处均为 ruff F821。本文件锁住两件事：
1. 两个路由模块的名字解析（防止再次漏导入）；
2. POST /admin/finance/credit-grants 的路由级接线——按 test_apportion_config_admin.py
   的 dependency_overrides 模式覆写管理员依赖、monkeypatch service 函数，走
   『发放目标为空 404』分支，无需真实数据库。
"""

from datetime import datetime

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.dependencies import CurrentMatchmakerAdmin, get_current_matchmaker_admin
from app.api.routes import finance as finance_routes
from app.api.routes import merchant_admin as merchant_admin_routes
from app.main import app
from app.schemas.matchmaker_admin import MatchmakerAdminAccount
from app.services import finance as finance_service

client = TestClient(app)


def _admin(permissions: set[str]) -> CurrentMatchmakerAdmin:
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
        permissions=frozenset(permissions),
    )


def test_finance_route_module_resolves_admin_grant_credits() -> None:
    """finance.py 导入块必须解析到 service 层的 admin_grant_credits。"""
    assert finance_routes.admin_grant_credits is finance_service.admin_grant_credits


def test_merchant_admin_route_module_uses_sqlalchemy_text() -> None:
    """merchant_admin.py 命名空间内的 text 必须就是 sqlalchemy.text。"""
    assert merchant_admin_routes.text is text


def test_credit_grants_route_maps_empty_target_to_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """发放目标为空时 service 抛 404，路由必须原样透出（handler 接线覆盖）。"""

    async def fake_admin_grant_credits(db, admin, request):
        assert request.target_type == "verified"
        assert request.user_ids is None
        raise HTTPException(404, detail="发放目标为空，请检查 target_type 或 user_ids")

    async def fake_admin() -> CurrentMatchmakerAdmin:
        return _admin(permissions={"finance.write"})

    monkeypatch.setattr(finance_routes, "admin_grant_credits", fake_admin_grant_credits)
    app.dependency_overrides[get_current_matchmaker_admin] = fake_admin
    try:
        response = client.post(
            "/api/v1/admin/finance/credit-grants",
            json={"target_type": "verified", "amount": 10, "reason": "运营活动"},
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "发放目标为空，请检查 target_type 或 user_ids"
    finally:
        app.dependency_overrides.pop(get_current_matchmaker_admin, None)

"""单元测试：后台账号注销申请（删除账号 → 注销申请 → 审核）。

策略（与 test_cancellation_feedback.py 一致，不依赖真实数据库）：
- OpenAPI 路径注册校验（DELETE 提交申请 + 列表 + 审核）；
- 未登录 401；
- Pydantic 契约校验；
- 建表 DDL 存在且带防重复 pending 的唯一键。
"""

import pathlib
import re

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.db.business_schema import BUSINESS_TABLES
from app.main import app
from app.schemas.admin_account_cancellation import (
    AdminAccountCancellationItem,
    AdminAccountCancellationReview,
)


client = TestClient(app)

LIST_PATH = "/api/v1/admin/matchmaker/account-cancellations"
REVIEW_PATH = "/api/v1/admin/matchmaker/account-cancellations/{cancellation_id}/review"
DELETE_PATH = "/api/v1/admin/matchmaker/accounts/{account_id}"


def test_account_cancellation_routes_are_registered() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "delete" in paths["/api/v1/admin/matchmaker/accounts/{account_id}"]
    assert "get" in paths[LIST_PATH]
    assert "post" in paths[REVIEW_PATH]


def test_account_cancellation_endpoints_require_authentication() -> None:
    assert client.delete(DELETE_PATH.format(account_id=1)).status_code == 401
    assert client.get(LIST_PATH).status_code == 401
    response = client.post(
        REVIEW_PATH.format(cancellation_id=1), json={"approve": True, "note": "ok"}
    )
    assert response.status_code == 401


def test_cancellation_item_default_flags_are_false() -> None:
    item = AdminAccountCancellationItem(
        id=1,
        account_id=2,
        username="44415151",
        display_name="admin11",
        status="pending",
        created_at="2026-01-01 00:00:00",
        updated_at="2026-01-01 00:00:00",
    )
    assert item.has_member_profile is False
    assert item.has_promoter_link is False
    assert item.has_partner_link is False
    assert item.has_matchmaker_link is False
    assert item.previous_status == 1
    assert item.linked_user_id is None


def test_review_requires_approve_field() -> None:
    with pytest.raises(ValidationError):
        AdminAccountCancellationReview(note="no approve field")  # type: ignore[call-arg]


def test_review_note_max_length() -> None:
    with pytest.raises(ValidationError):
        AdminAccountCancellationReview(approve=True, note="x" * 501)


def test_admin_account_cancellation_table_has_pending_guard() -> None:
    """DDL 必须存在，且用 (account_id, status) 唯一键防止重复的待处理申请。"""
    ddl = BUSINESS_TABLES.get("admin_account_cancellation")
    assert ddl, "admin_account_cancellation 建表定义缺失"
    assert "CREATE TABLE IF NOT EXISTS `admin_account_cancellation`" in ddl
    assert "UNIQUE KEY `uk_admin_account_cancel_pending` (`account_id`, `status`)" in ddl
    # 快照列：取消注销时要恢复提交前状态
    assert "`previous_status`" in ddl
    for column in (
        "has_member_profile",
        "has_promoter_link",
        "has_partner_link",
        "has_matchmaker_link",
    ):
        assert f"`{column}`" in ddl


def test_service_sql_stays_parameterized() -> None:
    """服务层 SQL 里不允许出现 f-string 直接插值用户输入的痕迹（keyword 必须走绑定参数）。"""
    source = (
        pathlib.Path(__file__).resolve().parents[1]
        / "app"
        / "services"
        / "admin_account_cancellation.py"
    ).read_text(encoding="utf-8")
    assert "LIKE :kw" in source
    assert re.search(r"LIKE '%", source) is None

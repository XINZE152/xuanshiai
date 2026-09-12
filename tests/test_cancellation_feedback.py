"""单元测试：账号注销申请（/admin/user-cancellations）+ 平台工单（/admin/tickets）。

策略：
- OpenAPI 路径注册校验；
- 未登录 401；
- Pydantic 字段互斥校验；
- 业务状态枚举校验。
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.admin_ticket import (
    AdminTicketCreate,
    AdminTicketReply,
    AdminTicketStatusUpdate,
)
from app.schemas.user_cancellation_admin import UserCancellationItem, UserCancellationReview


client = TestClient(app)


# ─── 注销申请 ────────────────────────────────────────────────


def test_user_cancellation_routes_are_registered() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    base = "/api/v1/admin/user-cancellations"
    assert "get" in paths[base]
    assert "get" in paths[f"{base}/statistics"]
    assert "post" in paths[f"{base}/{{cancellation_id}}/review"]


def test_user_cancellation_endpoints_require_authentication() -> None:
    assert client.get("/api/v1/admin/user-cancellations").status_code == 401
    assert client.get("/api/v1/admin/user-cancellations/statistics").status_code == 401
    response = client.post(
        "/api/v1/admin/user-cancellations/1/review",
        json={"approve": True, "note": "ok"},
    )
    assert response.status_code == 401


def test_user_cancellation_item_default_flags_are_false() -> None:
    """未设置 has_* 字段时默认为 False。"""
    item = UserCancellationItem(
        id=1,
        user_id=100,
        status="pending",
        created_at="2026-01-01 00:00:00",
        updated_at="2026-01-01 00:00:00",
    )
    assert item.has_member_profile is False
    assert item.has_promoter_link is False
    assert item.has_partner_link is False
    assert item.has_matchmaker_link is False


def test_user_cancellation_review_requires_approve_field() -> None:
    """approve 是必填字段（bool）。"""
    with pytest.raises(ValidationError):
        UserCancellationReview(note="no approve field")  # type: ignore[call-arg]


def test_user_cancellation_review_note_max_length() -> None:
    with pytest.raises(ValidationError):
        UserCancellationReview(approve=True, note="x" * 501)


# ─── 平台工单 ────────────────────────────────────────────────


def test_admin_ticket_routes_are_registered() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    base = "/api/v1/admin/tickets"
    assert "get" in paths[base]
    assert "get" in paths[f"{base}/statistics"]
    assert "post" in paths[base]
    assert "post" in paths[f"{base}/{{ticket_id}}/reply"]
    assert "patch" in paths[f"{base}/{{ticket_id}}/status"]


def test_admin_ticket_endpoints_require_authentication() -> None:
    assert client.get("/api/v1/admin/tickets").status_code == 401
    assert client.get("/api/v1/admin/tickets/statistics").status_code == 401
    assert (
        client.post(
            "/api/v1/admin/tickets",
            json={"feedback_type": "BUG", "title": "x", "content": "y"},
        ).status_code
        == 401
    )


def test_admin_ticket_create_title_required() -> None:
    with pytest.raises(ValidationError):
        AdminTicketCreate(content="only content")  # type: ignore[call-arg]


def test_admin_ticket_create_content_required() -> None:
    with pytest.raises(ValidationError):
        AdminTicketCreate(title="only title")  # type: ignore[call-arg]


def test_admin_ticket_create_feedback_type_enum() -> None:
    """非法反馈类型应被 Pydantic 拒绝。"""
    with pytest.raises(ValidationError):
        AdminTicketCreate(
            feedback_type="INVALID",  # type: ignore[arg-type]
            title="ok",
            content="ok",
        )


def test_admin_ticket_reply_content_required() -> None:
    with pytest.raises(ValidationError):
        AdminTicketReply()  # type: ignore[call-arg]


def test_admin_ticket_status_update_validates_enum() -> None:
    """非法状态应被 Pydantic 拒绝。"""
    with pytest.raises(ValidationError):
        AdminTicketStatusUpdate(status="BAD")  # type: ignore[arg-type]


def test_admin_ticket_status_update_accepts_valid_values() -> None:
    for s in ("待处理", "处理中", "已处理"):
        body = AdminTicketStatusUpdate(status=s)  # type: ignore[arg-type]
        assert body.status == s

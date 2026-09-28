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
from app.services import user_cancellation_admin as cancellation_service
from app.services.revisions import RevisionKind


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


@pytest.mark.asyncio
@pytest.mark.parametrize("approve, expected_user_status", [(True, 3), (False, 1)])
async def test_review_cancellation_bumps_privacy_revision_before_commit(
    monkeypatch: pytest.MonkeyPatch,
    approve: bool,
    expected_user_status: int,
) -> None:
    class _Result:
        def __init__(self, row: dict[str, object] | None) -> None:
            self._row = row

        def mappings(self) -> "_Result":
            return self

        def first(self) -> dict[str, object] | None:
            return self._row

    class _Db:
        def __init__(self) -> None:
            self.statements: list[str] = []
            self.commits = 0

        async def execute(self, statement: object, params: object) -> _Result:
            del params
            self.statements.append(str(statement))
            if len(self.statements) == 1:
                return _Result({"id": 7, "user_id": 42, "status": "pending"})
            return _Result(None)

        async def commit(self) -> None:
            self.commits += 1

    db = _Db()
    revision_calls: list[tuple[object, ...]] = []

    async def fake_increment(
        _db: object,
        user_id: int,
        kind: RevisionKind,
        changed_fields: tuple[str, ...],
        event_type: str,
        priority: int,
        **kwargs: object,
    ) -> None:
        del _db, kwargs
        revision_calls.append(
            (user_id, kind, changed_fields, event_type, priority)
        )

    result_item = UserCancellationItem(
        id=7,
        user_id=42,
        status="approved" if approve else "cancelled",
        created_at="2026-01-01 00:00:00",
        updated_at="2026-01-01 00:00:00",
    )

    async def fake_get_cancellation(_db: object, cancellation_id: int) -> UserCancellationItem:
        assert cancellation_id == 7
        return result_item

    monkeypatch.setattr(
        cancellation_service,
        "increment_revision_and_enqueue",
        fake_increment,
    )
    monkeypatch.setattr(
        cancellation_service,
        "get_cancellation",
        fake_get_cancellation,
    )

    result = await cancellation_service.review_cancellation(
        db,
        cancellation_id=7,
        admin_id=9,
        approve=approve,
        note="privacy regression",
    )

    assert result.status == ("approved" if approve else "cancelled")
    assert revision_calls == [
        (42, RevisionKind.PRIVACY, ("account_status",), "account_state_changed", 10)
    ]
    assert f"SET status = {expected_user_status}" in db.statements[1]
    assert db.commits == 1

"""MM-A-03 回归：敏感后台操作的幂等键与二次确认。"""

from __future__ import annotations

import inspect

import pytest
from fastapi import HTTPException

from app.api.dependencies import require_sensitive_operation
from app.api.routes import finance as finance_routes
from app.api.routes import matchmaker_admin_account as account_routes
from app.api.routes import organization_admin as organization_routes
from app.main import app

SENSITIVE_OPERATIONS = (
    ("/api/v1/admin/matchmaker/accounts/{account_id}/reset-password", "post"),
    ("/api/v1/admin/finance/orders/{order_id}/refund", "post"),
    ("/api/v1/admin/finance/withdrawals/{withdrawal_id}", "patch"),
    ("/api/v1/admin/matchmaker/assignments/{assignment_id}/end", "post"),
    ("/api/v1/admin/matchmaker/store-members/{member_id}", "delete"),
)

CASES = (
    (account_routes.reset_account_password, "matchmaker.admin_account.reset_password", "reset_password("),
    (finance_routes.refund, "finance.order.refund", "refund_order("),
    (finance_routes.review, "finance.withdrawal.review", "review_withdrawal("),
    (organization_routes.assignment_end, "organization.assignment.end", "end_assignment("),
    (organization_routes.store_member_remove, "organization.store_member.remove", "remove_store_member("),
)


def test_sensitive_endpoints_require_idempotency_key_and_confirmation() -> None:
    paths = app.openapi()["paths"]
    for path, method in SENSITIVE_OPERATIONS:
        params = {item["name"] for item in paths[path][method].get("parameters", [])}
        assert "Idempotency-Key" in params, path
        assert "confirm" in params, path


@pytest.mark.asyncio
async def test_confirmation_flag_is_mandatory() -> None:
    with pytest.raises(HTTPException) as exc:
        await require_sensitive_operation(idempotency_key="abcdefgh", confirm=False)
    assert exc.value.status_code == 428

    assert await require_sensitive_operation(idempotency_key="abcdefgh", confirm=True) == "abcdefgh"


def test_each_sensitive_route_uses_the_shared_helper() -> None:
    for handler, operation, _ in CASES:
        source = inspect.getsource(handler)
        assert "begin_sensitive_operation(" in source, handler.__name__
        assert f'operation="{operation}"' in source, handler.__name__
        assert "abort_sensitive_operation(" in source, handler.__name__
        assert "finish_sensitive_operation(" in source, handler.__name__


def test_replay_short_circuits_before_business_action() -> None:
    """命中重放时必须直接返回，不得再次执行不可逆的业务动作。"""
    for handler, _, business_call in CASES:
        source = inspect.getsource(handler)
        replay_at = source.index("if replayed is not None:")
        business_at = source.index(f"await {business_call}")
        finish_at = source.index("await finish_sensitive_operation(")
        assert replay_at < business_at < finish_at, handler.__name__


def test_failures_abort_the_reservation_so_retry_is_possible() -> None:
    for handler, _, _ in CASES:
        source = inspect.getsource(handler)
        assert (
            "    except Exception:\n"
            "        await abort_sensitive_operation(db, reservation)\n"
            "        raise\n"
        ) in source, handler.__name__

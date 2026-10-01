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
from app.services.idempotency import IdempotencyReservation, abort

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


class RecordingDB:
    """记录 session 上的操作顺序：审计第 1c 项判据全在顺序里。"""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.committed = False
        self.rolled_back = False

    async def execute(self, statement, params=None):
        self.events.append(f"execute:{str(statement).split()[:3][0].upper()}")
        return None

    async def commit(self) -> None:
        self.events.append("commit")
        self.committed = True

    async def rollback(self) -> None:
        self.events.append("rollback")
        self.rolled_back = True
        self.committed = False


@pytest.mark.asyncio
async def test_abort_rolls_back_before_deleting_and_committing() -> None:
    """审计第 1c 项：abort 必须 rollback → DELETE → commit。

    业务写入与预占行共用同一 session，若直接 DELETE + commit，这次 commit 会把异常前
    已完成的部分业务写入一起落库，形成「操作失败但数据改了一半」。
    """
    db = RecordingDB()
    # 模拟调用方在异常前已经写入的业务语句
    await db.execute("UPDATE users SET password_hash = :h WHERE id = :id")
    assert db.committed is False
    await abort(db, IdempotencyReservation(7, "owner-token"))
    assert db.events == ["execute:UPDATE", "rollback", "execute:DELETE", "commit"]


@pytest.mark.asyncio
async def test_abort_does_not_commit_partial_business_writes() -> None:
    """rollback 必须先于任何 commit，否则半途写入会被 abort 一起提交。"""
    db = RecordingDB()
    await db.execute("INSERT INTO account_ledger (a) VALUES (1)")
    await db.execute("UPDATE withdrawal_request SET status = :s WHERE id = :id")
    await abort(db, IdempotencyReservation(9, "tok"))
    first_commit = db.events.index("commit")
    assert "rollback" in db.events
    assert db.events.index("rollback") < first_commit
    # 半途写入之后再无任何提前 commit
    assert db.events.count("commit") == 1


def test_abort_source_keeps_rollback_first() -> None:
    """静态兜底：顺序被改回去时立刻失败（不依赖 mock 的调用时序）。"""
    source = inspect.getsource(abort)
    assert source.index("await db.rollback()") < source.index("DELETE FROM api_idempotency_record")
    assert source.index("DELETE FROM api_idempotency_record") < source.index("await db.commit()")


def test_begin_keeps_reservation_committed_separately() -> None:
    """预占行必须由 begin 单独提交，rollback 才不会顺手丢掉预占语义。"""
    from app.services import idempotency

    source = inspect.getsource(idempotency.reserve_or_replay)
    assert "INSERT INTO api_idempotency_record" in source
    assert "await db.commit()" in source

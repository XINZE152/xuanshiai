"""履约 Worker 领取、重试与死信容错测试。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from app.services.payment.fulfillment.base import FulfillmentError, UnsupportedOrderType
from app.workers import payment_worker
from tests.payment.conftest import FakeResult, FakeSession


class WorkerFakeDB(FakeSession):
    """按 order_id 返回订单的最小 Worker 库。"""

    def __init__(self, orders: dict[int, dict[str, Any]]) -> None:
        super().__init__(responder=self._respond)
        self.orders = orders

    async def _respond(
        self,
        sql: str,
        params: dict[str, Any],
        _session: FakeSession,
    ) -> FakeResult:
        if "FROM payment_order WHERE id = :id" in sql:
            return FakeResult(first=self.orders.get(int(params["id"])))
        return FakeResult()


def _job(order_id: int, *, order_type: int = 1, attempt_count: int = 0) -> dict[str, Any]:
    return {
        "id": order_id,
        "order_id": order_id,
        "order_no": f"TEST{order_id:08d}",
        "order_type": order_type,
        "job_type": "FULFILL",
        "status": "PROCESSING",
        "attempt_count": attempt_count,
        "payload_json": None,
    }


def _order(order_id: int, order_type: int = 1) -> dict[str, Any]:
    return {
        "id": order_id,
        "order_no": f"TEST{order_id:08d}",
        "user_id": 7,
        "type": order_type,
        "amount": "1.00",
        "status": 1,
        "product_id": None,
        "product_type": "monthly",
        "product_name": "月卡",
        "service_product_id": None,
        "transaction_id": "42000001",
        "paid_amount": "1.00",
        "channel": "wechat",
        "fulfillment_status": "PENDING",
    }


class RetryHandler:
    order_type = 1

    async def fulfill(self, _db, _order):
        raise FulfillmentError("模拟可重试失败")

    async def revoke(self, _db, _order, _refund):
        return None


class SuccessHandler:
    order_type = 1

    async def fulfill(self, _db, _order):
        return {"fulfilled": True}

    async def revoke(self, _db, _order, _refund):
        return None


@pytest.mark.asyncio
async def test_worker_claim_uses_skip_locked() -> None:
    """领取任务 SQL 必须包含 FOR UPDATE SKIP LOCKED。"""
    db = FakeSession(responder=lambda *_args: _empty_result())

    jobs = await payment_worker.claim_jobs(db, "worker-1", 5)

    assert jobs == []
    assert "FOR UPDATE SKIP LOCKED" in db.sql_text()
    assert db.commits == 1


async def _empty_result() -> FakeResult:
    return FakeResult(rows=[])


@pytest.mark.asyncio
async def test_worker_retryable_error_increments_attempt_and_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FulfillmentError：attempt_count+1，状态 FAILED，写入 next_retry_at。"""
    monkeypatch.setattr(payment_worker, "get_handler", lambda _type: RetryHandler())
    db = WorkerFakeDB({1: _order(1, 1)})
    job = _job(1, order_type=1, attempt_count=0)

    outcome = await payment_worker.process_job(db, job, "worker-1")

    assert outcome == "failed"
    updates = db.params_for("SET status = :status, attempt_count = :attempt_count")
    assert updates
    update = updates[0]
    assert update["status"] == "FAILED"
    assert update["attempt_count"] == 1
    assert isinstance(update["next_retry_at"], datetime)
    assert "payment_fulfillment_retry" not in db.sql_text()  # 日志不是 SQL


@pytest.mark.asyncio
async def test_worker_unsupported_type_is_dead_and_next_job_still_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UnsupportedOrderType 直接 DEAD，且不影响后续任务。"""
    def _handler(order_type: int):
        if order_type == 99:
            raise UnsupportedOrderType("unsupported")
        return SuccessHandler()

    monkeypatch.setattr(payment_worker, "get_handler", _handler)
    db = WorkerFakeDB({1: _order(1, 99), 2: _order(2, 1)})

    first = await payment_worker.process_job(db, _job(1, order_type=99), "worker-1")
    second = await payment_worker.process_job(db, _job(2, order_type=1), "worker-1")

    assert first == "dead"
    assert second == "success"
    dead_updates = [
        params
        for sql, params in db.statements
        if "SET status = :status, attempt_count = :attempt_count" in sql
        and params.get("status") == "DEAD"
    ]
    success_updates = [
        params
        for sql, params in db.statements
        if "SET status = 'SUCCESS'" in sql
    ]
    assert len(dead_updates) == 1
    assert success_updates

"""退款并发、超时与部分退款边界测试。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from app.services.payment.base import PaymentAPIError, PaymentValidationError, RefundResult
from app.services.payment.refunds import RefundService
from tests.payment.conftest import FakeResult, FakeSession


class RefundFakeDB(FakeSession):
    """带订单/退款单/REVOKE 任务状态的最小退款库。"""

    def __init__(self, order_amount: str = "100.00") -> None:
        super().__init__(responder=self._respond, scalar_responder=self._scalar)
        self.order = {
            "id": 1,
            "order_no": "TEST12345678",
            "user_id": 7,
            "type": 1,
            "amount": Decimal(order_amount),
            "status": 1,
            "transaction_id": "42000001",
            "channel": "wechat",
            "refund_status": "NONE",
            "refunded_amount": Decimal("0.00"),
        }
        self.refunds: list[dict[str, Any]] = []
        self.jobs: list[dict[str, Any]] = []
        self._next_refund_id = 1

    async def _respond(
        self,
        sql: str,
        params: dict[str, Any],
        _session: FakeSession,
    ) -> FakeResult:
        if "FROM payment_order" in sql and "FOR UPDATE" in sql:
            return FakeResult(first=self.order)
        if "FROM payment_refund" in sql and "idempotency_key" in sql:
            for refund in self.refunds:
                if (
                    refund["order_id"] == params["order_id"]
                    and refund["idempotency_key"] == params["idem"]
                ):
                    return FakeResult(first=refund)
            return FakeResult(first=None)
        if "INSERT INTO payment_refund" in sql:
            refund = {
                "id": self._next_refund_id,
                "refund_no": params["refund_no"],
                "order_id": params["order_id"],
                "order_no": params["order_no"],
                "user_id": params["user_id"],
                "transaction_id": params["transaction_id"],
                "order_amount": params["order_amount"],
                "refund_amount": params["refund_amount"],
                "refund_type": params["refund_type"],
                "status": "CREATED",
                "reason": params["reason"],
                "notify_id": None,
                "wechat_refund_id": None,
                "attempt_count": 0,
                "next_retry_at": None,
                "idempotency_key": params["idem"],
            }
            self.refunds.append(refund)
            self._next_refund_id += 1
            return FakeResult(lastrowid=refund["id"])
        if "FROM payment_refund" in sql and "FOR UPDATE" in sql:
            refund = _find_refund(self.refunds, params.get("refund_no"))
            return FakeResult(first=refund)
        if "UPDATE payment_refund" in sql and "attempt_count = attempt_count + 1" in sql:
            refund = _find_refund(self.refunds, params["refund_no"])
            if refund:
                refund["attempt_count"] += 1
                refund["next_retry_at"] = datetime.now(UTC)
                refund["fail_reason"] = params.get("error")
            return FakeResult(rowcount=1 if refund else 0)
        if "UPDATE payment_refund" in sql and "SET status = :status" in sql:
            refund = _find_refund(self.refunds, params.get("refund_no")) or _find_refund_by_id(
                self.refunds, params.get("id")
            )
            if refund:
                refund["status"] = params["status"]
                if params.get("wechat_refund_id"):
                    refund["wechat_refund_id"] = params["wechat_refund_id"]
                if params.get("notify_id"):
                    refund["notify_id"] = params["notify_id"]
            return FakeResult(rowcount=1 if refund else 0)
        if "UPDATE payment_order" in sql and "refunded_amount" in sql:
            self.order["refunded_amount"] = params["refunded"]
            self.order["refund_status"] = params["refund_status"]
            self.order["status"] = params["status"]
            return FakeResult(rowcount=1)
        if "INSERT INTO payment_fulfillment_job" in sql:
            if not any(job["biz_key"] == params["biz_key"] for job in self.jobs):
                self.jobs.append(dict(params))
            return FakeResult(rowcount=1)
        return FakeResult()

    async def _scalar(
        self,
        sql: str,
        params: dict[str, Any],
        _session: FakeSession,
    ) -> Any:
        if "SUM(refund_amount)" in sql:
            statuses = {value for key, value in params.items() if key.startswith("s")}
            total = sum(
                (refund["refund_amount"] for refund in self.refunds if refund["status"] in statuses),
                Decimal("0.00"),
            )
            return total
        return None


def _find_refund(refunds: list[dict[str, Any]], refund_no: str | None):
    return next((refund for refund in refunds if refund["refund_no"] == refund_no), None)


def _find_refund_by_id(refunds: list[dict[str, Any]], refund_id: Any):
    return next((refund for refund in refunds if refund["id"] == refund_id), None)


class RefundGateway:
    def __init__(self, result: RefundResult | None = None, error: Exception | None = None):
        self.result = result
        self.error = error

    async def create_refund(self, **kwargs) -> RefundResult:
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return RefundResult(
            refund_no=kwargs["out_refund_no"],
            wechat_refund_id="wx_refund_1",
            status=self.result.status,
            refund_type=self.result.refund_type,
        )


@pytest.mark.asyncio
async def test_refund_concurrent_different_keys_prevents_over_refund() -> None:
    """两个不同幂等键并发退款：串行化后第二笔因可退余额不足被拒绝。"""
    db = RefundFakeDB("100.00")
    service = RefundService(RefundGateway(RefundResult("", "wx", "PROCESSING")), "https://refund.test")
    lock = asyncio.Lock()

    async def invoke(amount: str, key: str):
        # 假设：MySQL FOR UPDATE 会把同一订单的退款创建串行化；这里用进程内锁模拟。
        async with lock:
            return await service.create_refund(
                db,
                order_id=1,
                amount=Decimal(amount),
                reason="并发退款测试",
                idempotency_key=key,
                operator_id=4,
            )

    results = await asyncio.gather(
        invoke("60.00", "idem-concurrent-a"),
        invoke("60.00", "idem-concurrent-b"),
        return_exceptions=True,
    )

    successes = [item for item in results if isinstance(item, RefundResult)]
    failures = [item for item in results if isinstance(item, PaymentValidationError)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert sum((refund["refund_amount"] for refund in db.refunds), Decimal("0")) <= Decimal("100.00")


@pytest.mark.asyncio
async def test_refund_timeout_keeps_created_and_sets_next_retry() -> None:
    """微信退款 504：退款单保持 CREATED，并设置重试时间。"""
    db = RefundFakeDB("100.00")
    service = RefundService(
        RefundGateway(error=PaymentAPIError(504, "微信退款超时")),
        "https://refund.test",
    )

    with pytest.raises(PaymentAPIError):
        await service.create_refund(
            db,
            order_id=1,
            amount=Decimal("30.00"),
            reason="超时测试",
            idempotency_key="idem-timeout-1",
            operator_id=4,
        )

    refund = db.refunds[0]
    assert refund["status"] == "CREATED"
    assert refund["attempt_count"] == 1
    assert refund["next_retry_at"] is not None
    assert db.order["status"] == 1


@pytest.mark.asyncio
async def test_refund_partial_then_full_only_enqueues_one_revoke() -> None:
    """100 元订单分两次退 30/70：最终 status=3，REVOKE 只投递一次。"""
    db = RefundFakeDB("100.00")
    service = RefundService(
        RefundGateway(RefundResult("", "wx", "SUCCESS")),
        "https://refund.test",
    )

    await service.create_refund(
        db,
        order_id=1,
        amount=Decimal("30.00"),
        reason="部分退款",
        idempotency_key="idem-partial-30",
        operator_id=4,
    )
    assert db.order["status"] == 1
    assert db.order["refund_status"] == "PARTIAL"
    assert db.jobs == []

    await service.create_refund(
        db,
        order_id=1,
        amount=Decimal("70.00"),
        reason="补退剩余金额",
        idempotency_key="idem-partial-70",
        operator_id=4,
    )
    assert db.order["status"] == 3
    assert db.order["refund_status"] == "FULL"
    assert len(db.jobs) == 1
    assert db.jobs[0]["biz_key"].startswith("REVOKE:")

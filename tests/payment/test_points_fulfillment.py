"""虚拟商品积分入账幂等测试。"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.payment.fulfillment.virtual_goods import VirtualGoodsFulfillmentHandler
from tests.payment.conftest import FakeResult, FakeSession


class PointsFakeDB(FakeSession):
    """模拟 user_points 唯一键与余额的最新行。"""

    def __init__(self) -> None:
        super().__init__(responder=self._respond, scalar_responder=self._scalar)
        self.balance = 10
        self.point_rows: list[dict[str, Any]] = []

    async def _respond(
        self,
        sql: str,
        params: dict[str, Any],
        _session: FakeSession,
    ) -> FakeResult:
        if "FROM config_point_product" in sql:
            return FakeResult(
                first={
                    "id": 1,
                    "code": "points_100",
                    "name": "100积分",
                    "product_type": "points",
                    "points_cost": 100,
                    "value": "100",
                }
            )
        if "SELECT balance FROM user_points" in sql:
            return FakeResult(first=(self.balance,))
        if "INSERT INTO user_points" in sql:
            biz_key = params.get("biz_key")
            if biz_key and any(row.get("biz_key") == biz_key for row in self.point_rows):
                return FakeResult(rowcount=0)
            self.point_rows.append(dict(params))
            self.balance = int(params["balance"])
            return FakeResult(rowcount=1)
        return FakeResult()

    async def _scalar(
        self,
        sql: str,
        params: dict[str, Any],
        _session: FakeSession,
    ) -> Any:
        if "SELECT id FROM users" in sql:
            return 7
        if "SELECT balance FROM user_points" in sql:
            biz_key = params.get("biz_key")
            row = next(
                (item for item in self.point_rows if item.get("biz_key") == biz_key),
                None,
            )
            return int(row["balance"]) if row else None
        return None


@pytest.mark.asyncio
async def test_points_same_order_five_times_credits_once() -> None:
    """同一订单 POINTS:{order_no} 入账 5 次，只增加一次积分。"""
    db = PointsFakeDB()
    handler = VirtualGoodsFulfillmentHandler()
    order = {
        "id": 1,
        "order_no": "TEST12345678",
        "user_id": 7,
        "type": 6,
        "product_id": 1,
        "product_type": "points_100",
        "amount": "1.00",
    }

    results = [await handler.fulfill(db, order) for _ in range(5)]

    assert len(db.point_rows) == 1
    assert db.point_rows[0]["biz_key"] == "POINTS:TEST12345678"
    assert db.balance == 110
    assert results[0]["balance"] == 110
    assert all(result["fulfilled"] for result in results)
    assert any("ON DUPLICATE KEY UPDATE" in sql for sql, _ in db.statements)

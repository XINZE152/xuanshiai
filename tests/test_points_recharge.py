"""Q57-b 积分充值链路（order_type=7）单元契约。

全部为不依赖数据库的内存/假库契约：目录定价下单、幂等、履约入账/冲正、
退款拒绝、路由边界。真实微信支付回调与对账属集成验证（方案 33 §3.5），
不在本文件伪造「支付已验证」结论。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.dependencies import CurrentUser, get_current_user
from app.api.routes import points as points_routes
from app.db.session import get_db
from app.main import app
from app.schemas.finance import CREDIT_CATEGORY_SOURCES, FinanceRefundRequest
from app.schemas.points import CreatePointRechargeOrderRequest
from app.services import finance as finance_service
from app.services import points_recharge
from app.services.payment.base import PaymentValidationError
from app.services.payment.fulfillment import get_handler, registered_order_types
from app.services.payment.fulfillment.base import (
    FulfillmentError,
    PermanentFulfillmentError,
)
from app.services.payment.fulfillment.points_recharge import (
    PointsRechargeFulfillmentHandler,
)
from app.services.payment.refunds import RefundService
from app.services.payments import get_paid_order
from tests.payment.conftest import FakeResult, FakeSession

client = TestClient(app)

_ORDER = {
    "id": 1,
    "order_no": "TEST7202611110001",
    "user_id": 7,
    "type": 7,
    "product_id": 5,
    "product_type": "points_recharge",
    "amount": "6.00",
}


class RechargeFakeDB(FakeSession):
    """模拟 admin_recharge_item 目录 + user_points 唯一键与最新余额。"""

    def __init__(
        self,
        *,
        resource_type: str = "points",
        quantity: int = 100,
        balance: int = 10,
        missing_product: bool = False,
    ) -> None:
        super().__init__(responder=self._respond, scalar_responder=self._scalar)
        self.balance = balance
        self.quantity = quantity
        self.resource_type = resource_type
        self.missing_product = missing_product
        self.point_rows: list[dict[str, Any]] = []

    async def _respond(self, sql: str, params: dict[str, Any], _session: FakeSession) -> FakeResult:
        if "FROM admin_recharge_item" in sql:
            if self.missing_product:
                return FakeResult()
            return FakeResult(
                first={"id": 5, "quantity": self.quantity, "resource_type": self.resource_type}
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

    async def _scalar(self, sql: str, params: dict[str, Any], _session: FakeSession) -> Any:
        if "SELECT id FROM users" in sql:
            return 7
        if "FROM user_points" in sql:
            row = next(
                (item for item in self.point_rows if item.get("biz_key") == params.get("biz_key")),
                None,
            )
            if row is None:
                return None
            return int(row["balance"]) if "SELECT balance" in sql else 1
        return None


@pytest.mark.asyncio
async def test_fulfill_same_order_five_times_credits_once() -> None:
    db = RechargeFakeDB()
    handler = PointsRechargeFulfillmentHandler()

    results = [await handler.fulfill(db, _ORDER) for _ in range(5)]

    assert len(db.point_rows) == 1
    row = db.point_rows[0]
    assert row["biz_key"] == "POINTS:TEST7202611110001"
    assert row["type"] == 5
    assert row["source_type"] == "points_recharge"
    assert row["amount"] == 100
    assert db.balance == 110
    assert results[0]["balance"] == 110
    assert "already" not in results[0]
    assert all(result["fulfilled"] for result in results)
    assert all(result.get("already") for result in results[1:])
    assert any("ON DUPLICATE KEY UPDATE" in sql for sql, _ in db.statements)


@pytest.mark.asyncio
async def test_fulfill_missing_product_is_retryable() -> None:
    db = RechargeFakeDB(missing_product=True)
    with pytest.raises(FulfillmentError) as exc:
        await PointsRechargeFulfillmentHandler().fulfill(db, _ORDER)
    assert not isinstance(exc.value, PermanentFulfillmentError)
    assert db.point_rows == []


@pytest.mark.asyncio
async def test_fulfill_wrong_resource_type_is_permanent() -> None:
    db = RechargeFakeDB(resource_type="sms")
    with pytest.raises(PermanentFulfillmentError):
        await PointsRechargeFulfillmentHandler().fulfill(db, _ORDER)
    assert db.point_rows == []


@pytest.mark.asyncio
async def test_fulfill_zero_quantity_is_retryable() -> None:
    db = RechargeFakeDB(quantity=0)
    with pytest.raises(FulfillmentError):
        await PointsRechargeFulfillmentHandler().fulfill(db, _ORDER)
    assert db.point_rows == []


@pytest.mark.asyncio
async def test_revoke_deducts_recharged_points_once() -> None:
    db = RechargeFakeDB()
    handler = PointsRechargeFulfillmentHandler()
    await handler.fulfill(db, _ORDER)
    assert db.balance == 110

    await handler.revoke(db, _ORDER, {"refund_no": "RF1", "reason": "用户申请"})
    await handler.revoke(db, _ORDER, {"refund_no": "RF1", "reason": "用户申请"})

    assert len(db.point_rows) == 2
    refund_row = db.point_rows[1]
    assert refund_row["biz_key"] == "REFUND:TEST7202611110001"
    assert refund_row["amount"] == -100
    assert refund_row["source_type"] == "points_recharge"
    assert db.balance == 10


@pytest.mark.asyncio
async def test_revoke_never_deducts_below_zero() -> None:
    db = RechargeFakeDB(balance=30)
    handler = PointsRechargeFulfillmentHandler()
    # 未走 fulfill 直接冲正：余额 30 < 充值 100，只扣 30。
    await handler.revoke(db, _ORDER, {"refund_no": "RF1"})
    assert db.point_rows[0]["amount"] == -30
    assert db.balance == 0


@pytest.mark.asyncio
async def test_revoke_zero_balance_writes_nothing() -> None:
    db = RechargeFakeDB(balance=0)
    await PointsRechargeFulfillmentHandler().revoke(db, _ORDER, {"refund_no": "RF1"})
    assert db.point_rows == []


def test_handler_registered_as_type_seven() -> None:
    assert 7 in registered_order_types()
    assert isinstance(get_handler(7), PointsRechargeFulfillmentHandler)


def test_ledger_category_recharge_covers_points_recharge() -> None:
    assert "points_recharge" in CREDIT_CATEGORY_SOURCES["recharge"]


class OrderFakeDB(FakeSession):
    """模拟目录读取 + payment_order 幂等键 lookup/insert。"""

    def __init__(self, *, package: dict[str, Any] | None = None, missing: bool = False) -> None:
        super().__init__(responder=self._respond)
        self.package = package if package is not None else {
            "id": 5,
            "name": "100积分",
            "quantity": 100,
            "price": Decimal("6.00"),
        }
        self.missing = missing
        self.orders: list[dict[str, Any]] = []

    async def _respond(self, sql: str, params: dict[str, Any], _session: FakeSession) -> FakeResult:
        if "FROM admin_recharge_item" in sql:
            if self.missing:
                return FakeResult()
            if "id" in params:
                if int(params["id"]) != int(self.package["id"]):
                    return FakeResult()
                return FakeResult(first=dict(self.package))
            return FakeResult(rows=[dict(self.package)])
        if "FROM payment_order" in sql:
            row = next(
                (
                    item
                    for item in self.orders
                    if item["key"] == params.get("key")
                    and item["user_id"] == params.get("user_id")
                    and item["type"] == params.get("type")
                ),
                None,
            )
            return FakeResult(first=dict(row) if row else None)
        if "INSERT INTO payment_order" in sql:
            # INSERT 的 status 是 SQL 字面量 0，不在参数里；回放时补上。
            self.orders.append({**params, "status": 0})
            return FakeResult(rowcount=1)
        return FakeResult()


@pytest.mark.asyncio
async def test_create_order_prices_from_catalog_not_client() -> None:
    db = OrderFakeDB()
    body = CreatePointRechargeOrderRequest.model_validate({"package_id": 5, "price": "0.01"})
    assert not hasattr(body, "price")

    order = await points_recharge.create_recharge_order(db, 7, body, "idem-key-0001")

    assert order.amount == Decimal("6.00")
    assert order.status == 0
    assert order.payment_required is True
    assert len(db.orders) == 1
    inserted = db.orders[0]
    assert inserted["type"] == 7
    assert inserted["product_id"] == 5
    assert inserted["amount"] == Decimal("6.00")


@pytest.mark.asyncio
async def test_create_order_idempotent_replay_returns_first_order() -> None:
    db = OrderFakeDB()
    body = CreatePointRechargeOrderRequest(package_id=5)

    first = await points_recharge.create_recharge_order(db, 7, body, "idem-key-0001")
    second = await points_recharge.create_recharge_order(db, 7, body, "idem-key-0001")

    assert len(db.orders) == 1
    assert second.order_no == first.order_no
    assert second.amount == first.amount


@pytest.mark.asyncio
async def test_create_order_unknown_or_disabled_package_404() -> None:
    db = OrderFakeDB(missing=True)
    with pytest.raises(HTTPException) as exc:
        await points_recharge.create_recharge_order(
            db, 7, CreatePointRechargeOrderRequest(package_id=5), "idem-key-0001"
        )
    assert exc.value.status_code == 404
    assert db.orders == []


@pytest.mark.asyncio
async def test_create_order_misconfigured_package_409() -> None:
    db = OrderFakeDB(package={"id": 5, "name": "坏配置", "quantity": 100, "price": Decimal("0")})
    with pytest.raises(HTTPException) as exc:
        await points_recharge.create_recharge_order(
            db, 7, CreatePointRechargeOrderRequest(package_id=5), "idem-key-0001"
        )
    assert exc.value.status_code == 409
    assert db.orders == []


@pytest.mark.asyncio
async def test_list_packages_maps_catalog_rows() -> None:
    db = OrderFakeDB()
    packages = await points_recharge.list_recharge_packages(db)
    assert len(packages) == 1
    assert packages[0].id == 5
    assert packages[0].points == 100
    assert packages[0].price == Decimal("6.00")
    sql = db.sql_text()
    assert "resource_type = 'points'" in sql
    assert "enabled = 1" in sql


@pytest.mark.asyncio
async def test_get_paid_order_other_users_order_404() -> None:
    db = OrderFakeDB()
    with pytest.raises(HTTPException) as exc:
        await get_paid_order(db, 8, "TEST7202611110001", 7)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_real_refund_service_rejects_type_seven() -> None:
    class RefundOrderDB(FakeSession):
        async def _respond(self, sql: str, params: dict[str, Any], _session: FakeSession) -> FakeResult:
            if "FROM payment_order" in sql:
                return FakeResult(
                    first={
                        "id": 1,
                        "order_no": "TEST7202611110001",
                        "user_id": 7,
                        "type": 7,
                        "amount": Decimal("6.00"),
                        "status": 1,
                        "transaction_id": "WX123",
                        "channel": "wechat",
                        "refund_status": "NONE",
                    }
                )
            return FakeResult()

    db = RefundOrderDB()
    db.responder = db._respond
    service = RefundService(gateway=object(), notify_url="https://example.com/refund-notify")
    with pytest.raises(PaymentValidationError, match="人工工单"):
        await service.create_refund(
            db,
            order_id=1,
            amount=Decimal("6.00"),
            reason="用户申请退款",
            idempotency_key="refund-key-0001",
            operator_id=1,
        )
    assert not any("INSERT INTO payment_refund" in sql for sql, _ in db.statements)


@pytest.mark.asyncio
async def test_legacy_local_refund_rejects_type_seven() -> None:
    class LegacyOrderDB(FakeSession):
        async def _respond(self, sql: str, params: dict[str, Any], _session: FakeSession) -> FakeResult:
            if "FROM payment_order" in sql:
                return FakeResult(
                    first={
                        "id": 1,
                        "status": 1,
                        "type": 7,
                        "order_no": "TEST7202611110001",
                        "service_request_id": None,
                    }
                )
            return FakeResult()

    db = LegacyOrderDB()
    db.responder = db._respond
    admin = CurrentUser(id=1, session_id=1, phone="13800000000", status=1, realname_status=2)
    with pytest.raises(HTTPException, match="人工工单") as exc:
        await finance_service.refund_order(db, admin, 1, FinanceRefundRequest(reason="用户申请"))
    assert exc.value.status_code == 409
    assert not any("UPDATE payment_order" in sql for sql, _ in db.statements)


def _override_points_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_current_user() -> CurrentUser:
        return CurrentUser(id=7, session_id=9, phone="13800000000", status=1, realname_status=2)

    async def fake_db():
        yield object()

    app.dependency_overrides[get_current_user] = fake_current_user
    app.dependency_overrides[get_db] = fake_db


def test_recharge_order_route_requires_idempotency_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _override_points_dependencies(monkeypatch)
    try:
        response = client.post("/api/v1/points/recharge/orders", json={"package_id": 5})
        assert response.status_code == 422
        response = client.post(
            "/api/v1/points/recharge/orders",
            json={"package_id": 5},
            headers={"Idempotency-Key": "short"},
        )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)


def test_recharge_order_route_rejects_client_price(monkeypatch: pytest.MonkeyPatch) -> None:
    """请求体只有 package_id；price/amount 等额外字段不进契约、不参与定价。"""
    _override_points_dependencies(monkeypatch)
    captured: list[dict[str, Any]] = []

    async def fake_create(db: Any, user_id: int, body: Any, key: str) -> Any:
        captured.append({"user_id": user_id, "body": body, "key": key})
        from app.schemas.payments import PaidOrderResponse

        return PaidOrderResponse(
            order_no="TEST7202611110001",
            product_code="points_recharge",
            product_name="100积分",
            amount=Decimal("6.00"),
            status=0,
            expire_at=None,
            payment_required=True,
        )

    monkeypatch.setattr(points_routes, "create_recharge_order", fake_create)
    try:
        response = client.post(
            "/api/v1/points/recharge/orders",
            json={"package_id": 5, "price": "0.01", "amount": "0.01"},
            headers={"Idempotency-Key": "idem-key-0001"},
        )
        assert response.status_code == 201
        assert len(captured) == 1
        assert captured[0]["body"].model_dump() == {"package_id": 5}
        assert response.json()["amount"] == "6.00"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)


def test_recharge_order_detail_route_scoped_to_type_seven(monkeypatch: pytest.MonkeyPatch) -> None:
    _override_points_dependencies(monkeypatch)
    captured: list[tuple[Any, ...]] = []

    async def fake_get_paid_order(db: Any, user_id: int, order_no: str, order_type: int | None = None) -> Any:
        captured.append((user_id, order_no, order_type))
        from app.schemas.payments import PaidOrderResponse

        return PaidOrderResponse(
            order_no=order_no,
            product_code="points_recharge",
            product_name="100积分",
            amount=Decimal("6.00"),
            status=1,
            expire_at=None,
            payment_required=False,
        )

    monkeypatch.setattr(points_routes, "get_paid_order", fake_get_paid_order)
    try:
        response = client.get("/api/v1/points/recharge/orders/TEST7202611110001")
        assert response.status_code == 200
        assert captured == [(7, "TEST7202611110001", 7)]
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)
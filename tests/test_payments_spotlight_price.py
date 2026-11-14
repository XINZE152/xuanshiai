"""付费爆灯：单价来源与实名门禁（SPOTLIGHT-PAY-20261114）。

背景：爆灯由「只做免费额度」改回「免费额度 + 付费」双通道后，
付费路径必须和免费路径共用同一道实名门禁，单价必须和展示接口读同一处配置，
否则会重演历史上「前端写死 9.9、后端按 5.00 收」的口径分裂。

全部用例走纯函数与打桩，不依赖 MySQL/Redis。
"""

from decimal import Decimal
from typing import Any

import pytest
from fastapi import HTTPException

from app.schemas.payments import PaidOrderResponse, SpotlightPaymentRequest, SpotlightPriceResponse
from app.services import payments as payments_service


class _ScalarResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar(self) -> Any:
        return self._value


class _FakeDB:
    """只回答 realname_status 一条查询；其余一律不参与断言。"""

    def __init__(self, realname_status: Any) -> None:
        self.realname_status = realname_status

    async def execute(self, statement: Any, params: Any = None) -> _ScalarResult:
        return _ScalarResult(self.realname_status)


# ── 1. 单价读取 ──────────────────────────────────────────────────────────
def test_price_response_defaults_to_cny() -> None:
    assert SpotlightPriceResponse(amount=Decimal("9.90")).currency == "CNY"


@pytest.mark.asyncio
async def test_get_spotlight_price_reads_runtime_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str]] = []

    async def fake_runtime_value(db: Any, namespace: str, key: str, fallback: str) -> str:
        calls.append((namespace, key, fallback))
        return "12.50"

    monkeypatch.setattr(payments_service, "get_runtime_value", fake_runtime_value)

    price = await payments_service.get_spotlight_price(None)

    assert price.amount == Decimal("12.50")
    # 坐标必须固定：改价靠运营配置，不靠改代码
    assert calls == [("finance", "spotlight_price", "9.90")]


@pytest.mark.asyncio
async def test_get_spotlight_price_tolerates_empty_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """运营把配置清空时 get_runtime_value 会给兜底值，不能再抛 500。"""

    async def fake_runtime_value(db: Any, namespace: str, key: str, fallback: str) -> str:
        return fallback

    monkeypatch.setattr(payments_service, "get_runtime_value", fake_runtime_value)

    price = await payments_service.get_spotlight_price(None)
    assert price.amount == Decimal(payments_service.SPOTLIGHT_PRICE_FALLBACK)


def test_create_order_reuses_the_same_price_source() -> None:
    """下单不得再自己调一次 get_runtime_value，否则两个入口迟早漂移。"""
    import inspect

    source = inspect.getsource(payments_service.create_spotlight_order)
    assert "get_spotlight_price" in source
    assert "get_runtime_value" not in source


# ── 2. 付费爆灯也要实名 ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_paid_spotlight_requires_realname(monkeypatch: pytest.MonkeyPatch) -> None:
    """免费路径强制实名，付费路径不能成为绕过口子。"""

    async def noop_ensure_target(db: Any, user_id: int, target_id: int) -> None:
        return None

    monkeypatch.setattr(payments_service, "_ensure_target", noop_ensure_target)

    with pytest.raises(HTTPException) as excinfo:
        await payments_service.create_spotlight_order(
            _FakeDB(0), 1, SpotlightPaymentRequest(target_user_id=2), "spotlight-key-0001"
        )

    assert excinfo.value.status_code == 403
    assert "实名" in str(excinfo.value.detail)


@pytest.mark.asyncio
async def test_paid_spotlight_passes_runtime_price_to_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def noop_ensure_target(db: Any, user_id: int, target_id: int) -> None:
        return None

    async def fake_runtime_value(db: Any, namespace: str, key: str, fallback: str) -> str:
        return "12.50"

    captured: dict[str, Any] = {}

    async def fake_create_order(
        db: Any,
        user_id: int,
        order_type: int,
        product_id: int | None,
        product_code: str,
        product_name: str,
        amount: Decimal,
        idempotency_key: str,
    ) -> PaidOrderResponse:
        captured.update(
            order_type=order_type,
            product_id=product_id,
            product_code=product_code,
            amount=amount,
        )
        return PaidOrderResponse(
            order_no="SPOTLIGHT0001",
            product_code=product_code,
            product_name=product_name,
            amount=amount,
            status=0,
            expire_at=None,
            payment_required=True,
        )

    monkeypatch.setattr(payments_service, "_ensure_target", noop_ensure_target)
    monkeypatch.setattr(payments_service, "get_runtime_value", fake_runtime_value)
    monkeypatch.setattr(payments_service, "_create_order", fake_create_order)

    order = await payments_service.create_spotlight_order(
        _FakeDB(2), 1, SpotlightPaymentRequest(target_user_id=2), "spotlight-key-0001"
    )

    assert captured["order_type"] == 2
    assert captured["product_code"] == "superlike"
    assert captured["product_id"] == 2
    assert captured["amount"] == Decimal("12.50")
    assert order.payment_required is True


# ── 3. 路由登记 ─────────────────────────────────────────────────────────
def test_spotlight_price_route_is_public() -> None:
    """单价是公开读接口（同 /boost/packages），不该挂登录依赖。"""
    import inspect

    from app.api.routes import payments as payments_routes

    source = inspect.getsource(payments_routes.spotlight_price)
    assert "get_spotlight_price" in source
    assert "get_current_user" not in source

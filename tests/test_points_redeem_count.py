"""积分兑换数量（2026-11-10，Q66）。

前端此前固定 ``count: 1``，后端也只扣一件。本文件锁定：

1. ``count`` 1~10，总价 = 单价 × count；
2. 余额、库存按总量校验；
3. 权益数量按 count 倍增；
4. 同 ``Idempotency-Key`` 必须同载荷，不同 count 返回 409。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas.points import RedeemRequest
from app.services import points


class _Result:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> "_Result":
        return self

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class _Tx:
    async def __aenter__(self) -> "_Tx":
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _FakeDb:
    def __init__(
        self,
        *,
        product: dict[str, Any] | None = None,
        balance: int = 1000,
        existing: dict[str, Any] | None = None,
    ) -> None:
        self.product = product
        self.balance = balance
        self.existing = existing
        self.statements: list[tuple[str, dict[str, Any]]] = []

    def begin(self) -> _Tx:
        return _Tx()

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = " ".join(str(statement).split())
        self.statements.append((sql, dict(params or {})))
        if "FROM point_redeem_order o" in sql:
            return _Result([self.existing] if self.existing else [])
        if "FROM config_point_product" in sql and "FOR UPDATE" in sql:
            return _Result([self.product] if self.product else [])
        if "SELECT balance FROM user_points" in sql:
            return _Result([(self.balance,)])
        return _Result()

    def sql(self, fragment: str) -> list[dict[str, Any]]:
        return [params for sql, params in self.statements if fragment in sql]


def _product(**overrides: Any) -> dict[str, Any]:
    row = {
        "id": 9,
        "code": "extra_apply",
        "name": "申请加次",
        "points_cost": 20,
        "value": 2,
        "stock": 20,
    }
    row.update(overrides)
    return row


@pytest.fixture(autouse=True)
def _stable_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        points,
        "settings",
        SimpleNamespace(point_cost_override=lambda _code, fallback: fallback),
    )

    async def grant(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(points, "grant", grant)


@pytest.mark.parametrize("count", [0, 11])
def test_count_out_of_range_rejected(count: int) -> None:
    with pytest.raises(ValidationError):
        RedeemRequest(product_code="extra_apply", count=count)


def test_count_defaults_to_one() -> None:
    assert RedeemRequest(product_code="extra_apply").count == 1


@pytest.mark.asyncio
async def test_redeem_multiplies_cost_grant_and_stock() -> None:
    db = _FakeDb(product=_product())
    response = await points.redeem(db, 7, RedeemRequest(product_code="extra_apply", count=3), "idem-123456")

    assert response.points_cost == 60
    assert response.count == 3
    assert response.balance == 940
    assert db.sql("INSERT INTO user_points")[0]["amount"] == -60
    order_params = db.sql("INSERT INTO point_redeem_order")[0]
    assert order_params["points_cost"] == 60
    assert order_params["count"] == 3
    assert db.sql("UPDATE config_point_product")[0]["count"] == 3


@pytest.mark.asyncio
async def test_insufficient_balance_uses_total_cost() -> None:
    db = _FakeDb(product=_product(), balance=59)
    with pytest.raises(Exception) as error:
        await points.redeem(db, 7, RedeemRequest(product_code="extra_apply", count=3), "idem-123456")
    assert getattr(error.value, "status_code", None) == 409
    assert not db.sql("INSERT INTO user_points")


@pytest.mark.asyncio
async def test_insufficient_stock_uses_total_count() -> None:
    db = _FakeDb(product=_product(stock=2))
    with pytest.raises(Exception) as error:
        await points.redeem(db, 7, RedeemRequest(product_code="extra_apply", count=3), "idem-123456")
    assert getattr(error.value, "status_code", None) == 409
    assert not db.sql("INSERT INTO user_points")


@pytest.mark.asyncio
async def test_idempotent_replay_returns_original_order() -> None:
    existing = {
        "order_no": "PT-1",
        "product_code": "extra_apply",
        "count": 2,
        "name": "申请加次",
        "points_cost": 40,
        "status": 1,
    }
    db = _FakeDb(existing=existing)
    response = await points.redeem(db, 7, RedeemRequest(product_code="extra_apply", count=2), "idem-123456")

    assert response.order_no == "PT-1"
    assert response.points_cost == 40
    assert response.count == 2
    assert not db.sql("INSERT INTO point_redeem_order")


@pytest.mark.asyncio
async def test_same_key_with_different_count_is_rejected() -> None:
    existing = {
        "order_no": "PT-1",
        "product_code": "extra_apply",
        "count": 2,
        "name": "申请加次",
        "points_cost": 40,
        "status": 1,
    }
    db = _FakeDb(existing=existing)
    with pytest.raises(Exception) as error:
        await points.redeem(db, 7, RedeemRequest(product_code="extra_apply", count=3), "idem-123456")
    assert getattr(error.value, "status_code", None) == 409

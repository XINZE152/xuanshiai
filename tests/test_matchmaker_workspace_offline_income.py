"""红娘工作台「财务明细 / 线下业绩」契约（2026-10-28 新增）。

口径：只回本人作为销售红娘的线下 VIP 已收款订单（`payment_order.matchmaker_id` +
`status = 1` + `product_type = 'offline_vip'`），与后台工作汇报 `offline_income`、
组织报表 `offline_performance` 完全一致；会员信息按订单回带。用 StubDB，不依赖 MySQL。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.services.matchmaker_workspace import WorkspaceActor, list_workspace_offline_income

PAID_AT = datetime(2026, 10, 22, 14, 20, 0)


class StubResult:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> "StubResult":
        return self

    def all(self) -> list[dict[str, object]]:
        return self._rows


class StubDB:
    def __init__(self, *, rows: list[dict[str, object]], total: int, total_amount: str) -> None:
        self.rows = rows
        self.total = total
        self.total_amount = total_amount
        self.sql: list[str] = []
        self.params: list[dict[str, object]] = []

    async def execute(self, sql: object, params: dict | None = None) -> StubResult:
        text = str(sql)
        assert "FROM payment_order po" in text
        self.sql.append(text)
        self.params.append(dict(params or {}))
        return StubResult(self.rows)

    async def scalar(self, sql: object, params: dict | None = None) -> object:
        text = str(sql)
        self.sql.append(text)
        self.params.append(dict(params or {}))
        if "COUNT(*)" in text:
            return self.total
        if "COALESCE(SUM(po.amount), 0)" in text:
            return self.total_amount
        raise AssertionError(f"未预期的读查询：{text}")


def _actor() -> WorkspaceActor:
    return WorkspaceActor(user_id=21, display_name="钱多多（示例）", level="NORMAL", organization_id=3)


def _row(**overrides: object) -> dict[str, object]:
    base = {
        "id": 88,
        "paid_at": PAID_AT,
        "order_no": "PO202610220009",
        "product_name": "线下VIP · 季度服务",
        "amount": "1200.00",
        "consumer_id": 53,
        "consumer_name": "小鹿",
        "consumer_avatar": "/storage/uploads/53/avatar.webp",
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_offline_income_reads_own_paid_offline_vip_orders() -> None:
    db = StubDB(rows=[_row()], total=1, total_amount="1200.00")
    page = await list_workspace_offline_income(db, _actor(), 1, 20)

    assert (page.total, page.page, page.page_size, page.has_more) == (1, 1, 20, False)
    assert page.total_amount == "1200.00"
    item = page.items[0]
    assert (item.consumer_id, item.consumer_name) == (53, "小鹿")
    assert item.consumer_avatar == "/storage/uploads/53/avatar.webp"
    assert item.item_name == "线下VIP · 季度服务"
    assert item.amount == "1200.00"
    assert item.order_no == "PO202610220009"
    assert item.paid_at == PAID_AT

    # 只认本人 + 已收款 + 线下 VIP，避免把其他商品或未支付订单算进业绩。
    assert "po.matchmaker_id = :user_id AND po.status = 1 AND po.product_type = 'offline_vip'" in db.sql[0]
    assert db.params[0]["user_id"] == 21
    assert "LEFT JOIN users c ON c.id = po.user_id" in db.sql[0]
    assert "ORDER BY paid_at DESC, po.id DESC" in db.sql[0]
    # 合计与概览 offline_performance 同口径，不含冲正概念。
    assert "COALESCE(SUM(po.amount), 0)" in db.sql[2]


@pytest.mark.asyncio
async def test_offline_income_falls_back_when_order_fields_are_missing() -> None:
    db = StubDB(
        rows=[_row(consumer_id=None, consumer_name=None, consumer_avatar=None, product_name=None, order_no=None)],
        total=3,
        total_amount="0.00",
    )
    page = await list_workspace_offline_income(db, _actor(), 2, 1)

    item = page.items[0]
    assert (item.consumer_id, item.consumer_name, item.consumer_avatar) == (0, "未知会员", None)
    assert item.item_name == "线下VIP服务"
    assert item.order_no is None
    assert page.has_more is True

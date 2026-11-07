"""红娘工作台「分成明细」契约（2026-10-28 新增）。

口径：只回本人（`beneficiary_type='service_matchmaker'` + 本人 user_id）的分成行；
会员名/头像从支付订单回带，事件名取分成规则名（缺失退回商品名），
合计剔除冲正行，与工作台概览的 online_commission 一致。用 StubDB，不依赖 MySQL。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.services.matchmaker_workspace import WorkspaceActor, list_workspace_commission_entries

CREATED_AT = datetime(2026, 10, 22, 10, 12, 0)


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
        assert "FROM commission_entry ce" in text
        self.sql.append(text)
        self.params.append(dict(params or {}))
        return StubResult(self.rows)

    async def scalar(self, sql: object, params: dict | None = None) -> object:
        text = str(sql)
        self.sql.append(text)
        self.params.append(dict(params or {}))
        if "COUNT(*)" in text:
            return self.total
        if "COALESCE(SUM(ce.amount), 0)" in text:
            return self.total_amount
        raise AssertionError(f"未预期的读查询：{text}")


def _actor() -> WorkspaceActor:
    return WorkspaceActor(user_id=21, display_name="钱多多（示例）", level="NORMAL", organization_id=3)


def _row(**overrides: object) -> dict[str, object]:
    base = {
        "id": 1024,
        "created_at": CREATED_AT,
        "status": "AVAILABLE",
        "base_amount": "1980.00",
        "amount": "297.00",
        "order_no": "PO202610220001",
        "consumer_id": 53,
        "product_name": "季度会员",
        "consumer_name": "小鹿",
        "consumer_avatar": "/storage/uploads/53/avatar.webp",
        "event_name": "VIP会员-季度开通",
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_commission_entries_enrich_consumer_and_event_name() -> None:
    db = StubDB(rows=[_row()], total=1, total_amount="297.00")
    page = await list_workspace_commission_entries(db, _actor(), 1, 20)

    assert (page.total, page.page, page.page_size, page.has_more) == (1, 1, 20, False)
    assert page.total_amount == "297.00"
    item = page.items[0]
    assert (item.consumer_id, item.consumer_name) == (53, "小鹿")
    assert item.consumer_avatar == "/storage/uploads/53/avatar.webp"
    assert item.event_name == "VIP会员-季度开通"
    assert (item.base_amount, item.amount, item.status) == ("1980.00", "297.00", "AVAILABLE")
    assert item.order_no == "PO202610220001"

    # 范围只认本人；会员名/事件名走订单与规则回带；合计与概览同口径剔除冲正。
    assert "ce.beneficiary_type = 'service_matchmaker' AND ce.beneficiary_id = :user_id" in db.sql[0]
    assert db.params[0]["user_id"] == 21
    assert "LEFT JOIN payment_order po ON po.id = ce.order_id" in db.sql[0]
    assert "COALESCE(rule.name, po.product_name) AS event_name" in db.sql[0]
    assert "ce.status <> 'REVERSED'" in db.sql[2]


@pytest.mark.asyncio
async def test_commission_entries_fall_back_when_order_or_rule_is_missing() -> None:
    db = StubDB(
        rows=[_row(consumer_id=None, consumer_name=None, consumer_avatar=None, event_name=None, order_no=None)],
        total=3,
        total_amount="0.00",
    )
    page = await list_workspace_commission_entries(db, _actor(), 2, 1)

    item = page.items[0]
    assert (item.consumer_id, item.consumer_name, item.consumer_avatar) == (0, "未知会员", None)
    assert item.event_name == "服务分成"
    assert item.order_no is None
    assert page.has_more is True

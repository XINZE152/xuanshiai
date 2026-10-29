"""`/community/quotas` 的 `points_available` 由 `config_point_product` 在架状态派生（A-6）。

变更前该字段写死 ``False``，理由是「前端立即兑换入口尚未接真接口」。但加次写路径
（``POST /users/me/points/redeem``）早已上线且强制幂等键，恒 ``False`` 会让后端主动
报「不可兑换」，把已可用的能力也一起压住。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services import community, quotas


class _Result:
    def __init__(self, scalar_value: Any = None, row: dict[str, Any] | None = None) -> None:
        self._scalar_value = scalar_value
        self._row = row

    def scalar(self) -> Any:
        return self._scalar_value

    def mappings(self) -> "_Result":
        return self

    def first(self) -> dict[str, Any] | None:
        return self._row


class _FakeDb:
    """按 ``code`` 参数决定商品是否在架，同时给 ``_point_product_cost`` 供价。"""

    def __init__(self, active_codes: set[str]) -> None:
        self.active_codes = active_codes

    async def execute(self, _statement: object, params: dict[str, Any] | None = None) -> _Result:
        code = str((params or {}).get("code"))
        active = code in self.active_codes
        return _Result(
            scalar_value=1 if active else None,
            row={"points_cost": 20} if active else None,
        )


class _BrokenDb:
    async def execute(self, *_args: object) -> _Result:
        raise RuntimeError("config table unavailable")


@pytest.mark.asyncio
async def test_sellable_true_when_product_is_active() -> None:
    assert await community._point_product_sellable(_FakeDb({"extra_apply"}), "extra_apply") is True


@pytest.mark.asyncio
async def test_sellable_false_when_product_missing_or_off_shelf() -> None:
    # 前端 my-tasks 页写死的 4 个码都不在商品表里，必须读出 False 而不是异常。
    assert await community._point_product_sellable(_FakeDb({"extra_apply"}), "plane_catch") is False
    assert await community._point_product_sellable(_FakeDb(set()), "extra_apply") is False


@pytest.mark.asyncio
async def test_sellable_fails_closed_when_config_table_unavailable() -> None:
    """与 ``_point_product_cost`` 的 fail-open 相反：在架状态没有安全默认值。

    价格猜错只是显示不准，在架状态猜 ``True`` 会让用户点进一个买不到的商品。
    """
    assert await community._point_product_sellable(_BrokenDb(), "extra_apply") is False


def _quota_summary() -> quotas.QuotaSummary:
    return quotas.QuotaSummary(
        items=[
            quotas.QuotaItem(
                quota_code="apply",
                daily_limit=3,
                daily_used=1,
                daily_remaining=2,
                extra_remaining=0,
                total_remaining=2,
            ),
            quotas.QuotaItem(
                quota_code="paper_plane",
                daily_limit=3,
                daily_used=0,
                daily_remaining=3,
                extra_remaining=0,
                total_remaining=3,
            ),
        ]
    )


@pytest.mark.asyncio
async def test_community_quotas_points_available_derives_per_product(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """两个 ``points_available`` 各自跟随自己的商品，不能一起恒真或恒假。"""

    async def _summary(_db: Any, _user_id: int) -> quotas.QuotaSummary:
        return _quota_summary()

    monkeypatch.setattr(community, "quota_summary", _summary)

    result = await community.get_community_quotas(_FakeDb({"extra_apply"}), 7)

    assert result.apply_daily.points_available is True
    assert result.paper_plane_daily.points_available is False


@pytest.mark.asyncio
async def test_community_quotas_points_available_false_when_table_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配置表读不到时额度接口整体仍要可用，只是不虚报可兑换。"""

    async def _summary(_db: Any, _user_id: int) -> quotas.QuotaSummary:
        return _quota_summary()

    monkeypatch.setattr(community, "quota_summary", _summary)

    result = await community.get_community_quotas(_BrokenDb(), 7)

    assert result.apply_daily.points_available is False
    assert result.paper_plane_daily.points_available is False
    # 价格走 fail-open 回退旧常量，接口不因展示字段整体失败
    assert result.apply_daily.points_cost == 20

"""纸飞机「积分加次」链路（2026-10-29）。

背景：`points.redeem` 早已支持 ``paper_plane_unlock`` 并写入 ``user_quota_grant``，
但 ``create_paper_plane`` 只扣每日免费额度、从不 ``consume_extra``，导致
"花积分买的纸飞机次数发不出去"（extra_remaining 显示 +1 却用不了）。

本文件锁定三件事：
1. 日额度用尽但有加次余额时能发出去；
2. 两者都没有时仍然 429；
3. 内容被审核拒绝时**不扣**加次（顺序：审核通过后才扣加次），只退日额度。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app.schemas.community import PaperPlaneCreate
from app.services import community


class _Result:
    """同时满足 lastrowid / mappings().one() 两种取用方式的最小假结果。"""

    def __init__(self, lastrowid: int = 1, row: dict[str, Any] | None = None) -> None:
        self.lastrowid = lastrowid
        self._row = row or {"id": lastrowid, "user_id": 7}

    def mappings(self) -> _Result:
        return self

    def one(self) -> dict[str, Any]:
        return dict(self._row)

    def first(self) -> dict[str, Any] | None:
        return dict(self._row)


class _FakeDb:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.committed = False
        self.rolled_back = False

    async def execute(self, statement: Any, _params: Any = None) -> _Result:
        sql = " ".join(str(statement).split())
        self.statements.append(sql)
        return _Result()

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


def _request() -> PaperPlaneCreate:
    return PaperPlaneCreate(content="周末一起爬山吗")


def _patch_common(monkeypatch: pytest.MonkeyPatch, *, daily_ok: bool, extra_ok: bool,
                  action: str = "allow") -> dict[str, list[Any]]:
    calls: dict[str, list[Any]] = {"daily": [], "extra": [], "refund": [], "reject_notice": []}

    async def allow(*_args: object, **_kwargs: object) -> None:
        return None

    async def consume_daily(key: str, limit: int) -> bool:
        calls["daily"].append((key, limit))
        return daily_ok

    async def consume_extra(_db: object, _uid: int, code: str, _reason: str, *_a: object) -> bool:
        calls["extra"].append(code)
        return extra_ok

    async def refund(key: str) -> None:
        calls["refund"].append(key)

    async def moderate(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(action=action, display_content="周末一起爬山吗")

    async def paper_response(row: dict[str, Any], viewer: int) -> Any:
        return {"id": row.get("id", 1), "viewer": viewer}

    async def reject_notice(*_args: object, **_kwargs: object) -> None:
        calls["reject_notice"].append(True)

    async def limit(*_args: object, **_kwargs: object) -> int:
        return 3

    monkeypatch.setattr(community, "ensure_user_allowed", allow)
    monkeypatch.setattr(community, "resolve_paper_plane_limit", limit)
    monkeypatch.setattr(community, "consume_daily", consume_daily)
    monkeypatch.setattr(community, "consume_extra", consume_extra)
    monkeypatch.setattr(community, "_refund_quota_after_failure", refund)
    # moderate_text 是函数内 `from app.services.content_filter import moderate_text` 取的，
    # 必须 patch 源模块的属性，patch community.moderate_text 无效。
    from app.services import content_filter

    monkeypatch.setattr(content_filter, "moderate_text", moderate)
    monkeypatch.setattr(community, "_paper_response", paper_response)
    monkeypatch.setattr(community, "_notify_moderation_rejected", reject_notice)
    monkeypatch.setattr(community, "bind_media", allow, raising=False)
    return calls


@pytest.mark.asyncio
async def test_daily_quota_is_enough_so_extra_is_not_touched(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_common(monkeypatch, daily_ok=True, extra_ok=False)
    db = _FakeDb()
    await community.create_paper_plane(db, 7, _request(), commit=True)
    assert db.committed is True
    assert calls["extra"] == [], "日额度够用时不应当扣用户花积分买的次数"


@pytest.mark.asyncio
async def test_purchased_chance_is_usable_when_daily_quota_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_common(monkeypatch, daily_ok=False, extra_ok=True)
    db = _FakeDb()
    await community.create_paper_plane(db, 7, _request(), commit=True)
    assert calls["extra"] == ["paper_plane"], "日额度用尽后必须走积分加次"
    assert db.committed is True
    assert calls["refund"] == [], "成功路径不应退款"


@pytest.mark.asyncio
async def test_no_daily_and_no_extra_still_returns_429(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_common(monkeypatch, daily_ok=False, extra_ok=False)
    db = _FakeDb()
    with pytest.raises(HTTPException) as error:
        await community.create_paper_plane(db, 7, _request(), commit=True)
    assert error.value.status_code == 429
    assert calls["extra"] == ["paper_plane"]
    assert not any("INSERT INTO paper_plane" in sql for sql in db.statements)


@pytest.mark.asyncio
async def test_rejected_content_does_not_consume_purchased_chance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """审核拒绝时只退日额度；加次还没扣，所以不能扣——否则用户白丢一次。"""
    calls = _patch_common(monkeypatch, daily_ok=True, extra_ok=True, action="reject")
    db = _FakeDb()
    with pytest.raises(HTTPException) as error:
        await community.create_paper_plane(db, 7, _request(), commit=True)
    assert error.value.status_code == 422
    assert calls["extra"] == []
    assert len(calls["refund"]) == 1
    assert calls["reject_notice"] == [True]


@pytest.mark.asyncio
async def test_point_product_cost_prefers_config_table(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Db:
        async def execute(self, *_args: object) -> _Result:
            return _Result(row={"points_cost": 30})

    assert await community._point_product_cost(_Db(), "paper_plane_unlock", 10) == 30

    class _BrokenDb:
        async def execute(self, *_args: object) -> _Result:
            raise RuntimeError("config table unavailable")

    # 展示字段 fail-open：配置表不可用时回退旧常量，不让额度接口整体 500
    assert await community._point_product_cost(_BrokenDb(), "paper_plane_unlock", 10) == 10

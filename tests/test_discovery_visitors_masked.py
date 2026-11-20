"""非会员访客列表：从「空列表 + 会员墙」改为「脱敏占位列表」。

契约（2026-11-20）：
1. 非会员也返回与 ``count`` 一致的列表条数（不再 items=[]）；
2. 每条都是脱敏占位：昵称打码、头像换本地 seed-real 素材、``user_id`` 为负数；
3. ``can_view_details`` 语义不变（只有会员 True），``masked`` 标记脱敏态；
4. 会员路径完全不变（明文昵称 / 真实头像 / 真实 user_id）。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pytest

from app.services import discovery


_VIEWED_AT = datetime(2026, 11, 20, 9, 0)


class _Result:
    def __init__(self, rows: list[dict[str, Any]] | None = None, scalar: Any = 0) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def mappings(self) -> _Result:
        return self

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def scalar(self) -> Any:
        return self._scalar


class _SeqDb:
    """按调用顺序返回预置结果：第 1 次是 count，第 2 次是访客行。"""

    def __init__(self, results: list[_Result]) -> None:
        self._results = list(results)
        self.calls = 0

    async def execute(self, *_args: object, **_kwargs: object) -> _Result:
        index = self.calls
        self.calls += 1
        return self._results[index] if index < len(self._results) else _Result()


class _Predicate:
    def __init__(self) -> None:
        self.clause = "1=1"
        self.params: dict[str, Any] = {}


def _target_row(user_id: int, nickname: str, gender: int) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "nickname": nickname,
        "avatar": f"https://cdn.example.com/avatar/{user_id}.webp",
        "gender": gender,
        "birthday": date(1997, 5, 1),
        "residence_city_code": "360100",
        "online_status": 1,
        "only_vip_can_see_detail": 0,
    }


def _patch(monkeypatch: pytest.MonkeyPatch, vip: bool, attached: list[Any]) -> None:
    async def fake_scene(*_args: object, **_kwargs: object) -> tuple[dict[str, Any], bool, _Predicate]:
        return {"user_id": 1}, vip, _Predicate()

    async def fake_targets(
        _db: Any, _viewer_id: int, ids: list[int], **_kwargs: object
    ) -> dict[int, dict[str, Any]]:
        pool = {21: _target_row(21, "林小满", 2), 22: _target_row(22, "陈默", 1)}
        return {user_id: pool[user_id] for user_id in ids if user_id in pool}

    async def fake_attach(_db: Any, cards: list[Any]) -> None:
        attached.extend(cards)

    monkeypatch.setattr(discovery, "_scene_visibility", fake_scene)
    monkeypatch.setattr(discovery, "_target_rows", fake_targets)
    monkeypatch.setattr(discovery, "_attach_card_qa_media", fake_attach)


@pytest.mark.asyncio
async def test_non_vip_gets_masked_list_instead_of_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    attached: list[Any] = []
    _patch(monkeypatch, vip=False, attached=attached)
    db = _SeqDb([_Result(scalar=2), _Result(rows=[{"user_id": 21, "viewed_at": _VIEWED_AT}, {"user_id": 22, "viewed_at": _VIEWED_AT}])])

    page = await discovery.visitors(db, 1, 1, 20)

    assert page.can_view_details is False
    assert page.masked is True
    assert page.count == 2
    assert len(page.items) == 2, "非会员必须拿到与 count 一致的列表，不能是空列表"
    # 脱敏后不再需要拉问答媒体
    assert attached == []


@pytest.mark.asyncio
async def test_masked_card_hides_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, vip=False, attached=[])
    db = _SeqDb([_Result(scalar=1), _Result(rows=[{"user_id": 21, "viewed_at": _VIEWED_AT}])])

    page = await discovery.visitors(db, 1, 1, 20)
    card = page.items[0].target

    assert card.user_id == -1, "user_id 必须是负数占位，前端不能拿到真实 id 拼详情页"
    assert card.nickname == "林**", "昵称须打码"
    assert card.avatar is not None and card.avatar.startswith("/static/portraits/seed-real/"), (
        "头像须换成本地占位素材，不能下发真实 URL"
    )
    assert card.detail_locked is True
    # 距离与合拍度保留（列表要显示），身份字段清空
    assert card.city_code is None
    assert card.occupation is None
    assert card.match_score >= 0


@pytest.mark.asyncio
async def test_vip_path_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    attached: list[Any] = []
    _patch(monkeypatch, vip=True, attached=attached)
    db = _SeqDb([_Result(scalar=1), _Result(rows=[{"user_id": 21, "viewed_at": _VIEWED_AT}])])

    page = await discovery.visitors(db, 1, 1, 20)
    card = page.items[0].target

    assert page.can_view_details is True
    assert page.masked is False
    assert card.user_id == 21
    assert card.nickname == "林小满"
    assert card.avatar == "https://cdn.example.com/avatar/21.webp"
    assert len(attached) == 1, "会员路径仍要拉问答媒体"


@pytest.mark.asyncio
async def test_no_visitors_returns_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, vip=False, attached=[])
    db = _SeqDb([_Result(scalar=0), _Result(rows=[])])

    page = await discovery.visitors(db, 1, 1, 20)

    assert page.count == 0
    assert page.items == []
    assert page.masked is True

"""黑名单列表不得走推荐可见性门禁（2026-11-12，线上 bug）。

背景：屏蔽写入成功（``user_block`` 已落库，前端提示「已屏蔽」），但
``GET /security/blocks`` 恒返回 ``[]``，设置页「已屏蔽用户」始终 0 人。

根因是 ``list_blocks`` 复用 ``_target_rows``，而 ``candidate_visibility`` 的谓词里
有一条 ``NOT EXISTS user_block``（排除双方存在屏蔽关系的用户）——与本接口要返回的
正是这些人**自相矛盾**，所以任何环境下都必然为空，与生产是否放开
``DISCOVERY_SKIP_GATES`` 无关；再叠加完整度门禁（``c.score >= 100``）与资料隐私
门禁（``show_profile`` / ``profile_visibility`` / ``who_can_see_me``），被屏蔽者
只要资料不完整或设了隐私就同样被过滤。

黑名单是用户自己的关系记录，不是推荐曝光位，与浏览记录/访客/收藏同理，不套门禁。

本文件锁定四件事：

1. ``list_blocks`` 不再调用 ``_target_rows``；
2. 只发一条 SQL，且不出现可见性/完整度/隐私门禁与 ``:viewer_id`` 依赖；
3. 返回顺序保持 ``created_at DESC``，字段与 ``SocialUser`` / 前端 ``mapBlockedUser`` 兼容；
4. 空黑名单返回空数组。
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from app.services import social


class _Result:
    """最小结果集替身：只兼容 ``mappings().all()``。"""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> _Result:
        return self

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)


class _Db:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []
        self.statements: list[str] = []
        self.params: list[dict[str, Any]] = []

    async def execute(self, statement: object, params: dict[str, Any] | None = None) -> _Result:
        self.statements.append(str(statement))
        self.params.append(dict(params or {}))
        return _Result(self._rows)


def _row(user_id: int, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "user_id": user_id,
        "nickname": f"用户{user_id}",
        "avatar": f"/storage/avatar/{user_id}.jpg",
        "avatar_thumb": f"/storage/avatar/{user_id}_thumb.jpg",
        "gender": 1,
        "birthday": None,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_list_blocks_does_not_route_through_visibility_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """回归主断言：一旦重新接回 ``_target_rows``，本用例立刻失败。"""

    async def fail_if_called(*_args: object, **_kwargs: object) -> object:
        raise AssertionError(
            "黑名单列表不得复用 _target_rows：其谓词排除双方存在屏蔽关系的用户，"
            "与本接口要返回的正是这些人自相矛盾，结果恒为空"
        )

    monkeypatch.setattr(social, "_target_rows", fail_if_called)

    db = _Db([_row(11)])
    items = await social.list_blocks(db, 7)

    assert [item.user_id for item in items] == [11]
    # 只查一次库：不再有 _viewer_context / _is_vip / _target_rows 那几趟附带查询。
    assert len(db.statements) == 1
    assert db.params[0] == {"user_id": 7}, "只依赖当前用户，不再需要 viewer 上下文参数"

    sql = db.statements[0]
    for forbidden in (
        "user_profile_completion",
        "user_privacy",
        "c.score",
        "show_profile",
        "profile_visibility",
        "who_can_see_me",
        ":viewer_id",
    ):
        assert forbidden not in sql, f"黑名单列表不应套用可见性门禁：{forbidden}"
    assert "FROM user_block" in sql
    assert "ORDER BY b.created_at DESC" in sql


@pytest.mark.asyncio
async def test_list_blocks_preserves_order_and_maps_fields() -> None:
    """顺序按 SQL 的 created_at DESC 原样透传，字段对齐前端 mapBlockedUser。"""

    second = date(1998, 3, 4)
    db = _Db(
        [
            _row(22, nickname="乙", avatar="/a2.jpg", avatar_thumb="/t2.jpg", gender=2, birthday=second),
            _row(11, nickname=None, avatar=None, avatar_thumb=None),
        ]
    )

    items = await social.list_blocks(db, 7)

    assert [item.user_id for item in items] == [22, 11], "必须保持 created_at DESC，不能被 dict 顺序打乱"
    first = items[0]
    assert (first.nickname, first.avatar, first.avatar_thumb, first.gender) == ("乙", "/a2.jpg", "/t2.jpg", 2)
    today = date.today()
    assert first.age == today.year - second.year - ((today.month, today.day) < (second.month, second.day))
    # 生日缺失时不应抛错，age 为 None（前端对 nickname/avatar 也有兜底）。
    assert items[1].nickname is None
    assert items[1].age is None


@pytest.mark.asyncio
async def test_list_blocks_returns_empty_for_no_blocks() -> None:
    db = _Db()
    assert await social.list_blocks(db, 7) == []
    assert len(db.statements) == 1

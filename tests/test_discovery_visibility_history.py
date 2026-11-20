"""浏览记录 / 访客列表不得套用推荐流的资料完整度门禁（2026-11-02，Q21）。

背景：``candidate_visibility`` 的 ``score >= 100`` 是**推荐曝光**的门槛；把它
沿用到底 HISTORY/VISITORS 会导致「会员开通了，访客列表还是空」。本文件锁定三件事：

1. HISTORY/VISITORS 场景构造 predicate 时可以关掉完整度门禁；
2. 关掉后封禁/隐私条件仍在（不能顺手把别的门也放开）；
3. ``browse_history`` / ``visitors`` 两个入口**同时**给 predicate 与
   ``_target_rows`` 传 ``skip_completion_gate=True``（只改一处会出现
   「列表有记录但卡片被丢掉」）。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services import discovery
from app.services.candidate_visibility import (
    CandidateVisibilityService,
    ViewerContext,
    VisibilityScene,
)


class _Result:
    """最小结果集替身：兼容 mappings().all()/first()/scalar()/fetchall()。"""

    def __init__(self, rows: list[dict[str, Any]] | None = None, scalar: Any = 0) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def mappings(self) -> _Result:
        return self

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return list(self._rows)

    def scalar(self) -> Any:
        return self._scalar


class _Db:
    async def execute(self, *_args: object, **_kwargs: object) -> _Result:
        return _Result()

    async def commit(self) -> None:  # pragma: no cover - 本用例不触发
        return None


class _Predicate:
    """predicate 的鸭子类型替身，只暴露调用方实际读取的两个属性。"""

    def __init__(self) -> None:
        self.clause = "1=1"
        self.params: dict[str, Any] = {}


def test_history_scene_can_skip_completion_gate() -> None:
    service = CandidateVisibilityService()
    viewer = ViewerContext(user_id=1, realname_status=2, is_vip=True)

    gated = service.predicate(viewer, VisibilityScene.HISTORY)
    ungated = service.predicate(viewer, VisibilityScene.HISTORY, skip_completion_gate=True)

    assert "COALESCE(c.score" in gated.clause, "默认仍应保留完整度门禁"
    assert "COALESCE(c.score" not in ungated.clause, "skip 后不应再出现完整度门禁"
    # 放开完整度不等于放开安全门：封禁关系必须仍在。
    assert "user_block" in ungated.clause


@pytest.mark.asyncio
async def test_browse_history_passes_skip_gate_to_both_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, dict[str, Any]] = {}

    async def fake_scene(
        _db: Any, viewer_id: int, scene: VisibilityScene, **kwargs: Any
    ) -> tuple[dict[str, Any], bool, _Predicate]:
        captured["scene"] = kwargs
        return {"user_id": viewer_id}, True, _Predicate()

    async def fake_targets(
        _db: Any, _viewer_id: int, _ids: list[int], **kwargs: Any
    ) -> dict[int, dict[str, Any]]:
        captured["targets"] = kwargs
        return {}

    monkeypatch.setattr(discovery, "_scene_visibility", fake_scene)
    monkeypatch.setattr(discovery, "_target_rows", fake_targets)

    await discovery.browse_history(_Db(), 5, 1, 20)

    assert captured["scene"]["skip_completion_gate"] is True
    assert captured["targets"]["skip_completion_gate"] is True


@pytest.mark.asyncio
async def test_visitors_passes_skip_gate_to_both_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, dict[str, Any]] = {}

    async def fake_scene(
        _db: Any, viewer_id: int, scene: VisibilityScene, **kwargs: Any
    ) -> tuple[dict[str, Any], bool, _Predicate]:
        captured["scene"] = kwargs
        return {"user_id": viewer_id}, True, _Predicate()

    async def fake_targets(
        _db: Any, _viewer_id: int, _ids: list[int], **kwargs: Any
    ) -> dict[int, dict[str, Any]]:
        captured["targets"] = kwargs
        return {}

    monkeypatch.setattr(discovery, "_scene_visibility", fake_scene)
    monkeypatch.setattr(discovery, "_target_rows", fake_targets)

    # 2026-11-20 起非会员也查列表（返回脱敏占位），两条路径都要带 skip_completion_gate。
    await discovery.visitors(_Db(), 5, 1, 20)

    assert captured["scene"]["skip_completion_gate"] is True
    assert captured["targets"]["skip_completion_gate"] is True


def test_recommendation_scene_keeps_gate_by_default() -> None:
    """回归：推荐/搜索/详情链路不能被这次改动顺手放开。"""
    service = CandidateVisibilityService()
    viewer = ViewerContext(user_id=1, realname_status=2, is_vip=False)
    for scene in (VisibilityScene.DISCOVERY, VisibilityScene.SEARCH, VisibilityScene.PROFILE):
        assert "COALESCE(c.score" in service.predicate(viewer, scene).clause

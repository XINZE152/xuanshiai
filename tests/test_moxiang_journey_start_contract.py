"""``POST /ai/moxiang/journey/start`` 的响应字段契约（P3 回归）。

钉住两个曾被硬编码的字段：

1. ``already_existed`` 必须区分「复用了既有活动会话」与「本次真正新建」；
2. ``journey_stage`` 必须取该会话的真实阶段。

修复前二者恒为 ``False`` / ``"chatting"``，而 ``create_master_session`` 是
「创建/恢复」合一：WS 重连、过期重建、并发冲突后回读复用都会返回真实存在的老
会话，前端据此判断「是否首次进入」就永远判错。前端已在 ``api/ai-moxiang.uts``
把 ``already_existed`` 映射为 ``alreadyExisted``。

形态与 tests/test_master_session.py 一致（ProfileStore 内存假库，不依赖 DB）。
功能开关与 ``can_start_ideal_partner`` 门禁不是本用例主题（前者由
test_ai_moxiang_journey_feature_gate.py 覆盖），此处直接桩掉以聚焦响应契约。
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest

import app.api.routes.ai_moxiang as journey_route
from tests.test_ai_profile_sessions import ProfileStore, _now


async def _state_allows_start(*args: Any, **kwargs: Any) -> Any:
    return SimpleNamespace(can_start_ideal_partner=True)


@pytest.fixture()
def journey_store(monkeypatch: pytest.MonkeyPatch) -> ProfileStore:
    monkeypatch.setattr(journey_route, "_require_journey_feature", lambda: None)
    monkeypatch.setattr(journey_route, "build_state_response", _state_allows_start)
    return ProfileStore()


@pytest.mark.asyncio
async def test_journey_start_reports_already_existed_and_real_stage(
    journey_store: ProfileStore,
) -> None:
    user = SimpleNamespace(id=10)

    created = await journey_route.start_journey(
        subject="ideal_partner", current_user=user, db=journey_store.db
    )
    assert created["subject"] == "ideal_partner"
    assert created["already_existed"] is False
    # 会话行没有 journey_stage 值时回落默认阶段（而不是把常量写死在响应里）。
    assert created["journey_stage"] == "chatting"

    # 同一主体再次进入：复用同一个会话，并且响应如实反映这件事。
    journey_store.sessions[created["session_id"]]["journey_stage"] = "building"
    resumed = await journey_route.start_journey(
        subject="ideal_partner", current_user=user, db=journey_store.db
    )
    assert resumed["session_id"] == created["session_id"]
    assert resumed["already_existed"] is True
    assert resumed["journey_stage"] == "building"
    assert len(journey_store.sessions) == 1


@pytest.mark.asyncio
async def test_journey_start_marks_rebuilt_session_as_new(
    journey_store: ProfileStore,
) -> None:
    """过期会话被重建时 ``already_existed`` 必须回到 ``False``。

    「恢复」有两种：复用仍活动的会话（老会话还能继续聊，True）与旧会话已
    stale、本次插入全新会话（用户从头开始，False）。只按「有没有历史会话」
    判断会把后者也说成已存在，前端据此跳过开场白。
    """
    user = SimpleNamespace(id=10)
    created = await journey_route.start_journey(
        subject="ideal_partner", current_user=user, db=journey_store.db
    )
    # 让该会话过期：复用路径按 stale 关槽，随后插入新行。
    journey_store.sessions[created["session_id"]]["expires_at"] = _now() - timedelta(days=1)
    rebuilt = await journey_route.start_journey(
        subject="ideal_partner", current_user=user, db=journey_store.db
    )
    assert rebuilt["session_id"] != created["session_id"]
    assert rebuilt["already_existed"] is False
    assert journey_store.sessions[created["session_id"]]["active_status"] == 0

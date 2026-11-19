"""合拍度可见性拒绝的可观测化契约（2026-11-07，AIT-08 / BE-02）。

排障背景：``GET /api/v1/ai/compatibility/{target_user_id}`` 把 ``PROFILE_INCOMPLETE``、
``BLOCKED_RELATIONSHIP``、``SELF_REFERENCE`` 等原因统一压成 404
``CANDIDATE_NOT_VISIBLE``（不泄露归属，统一方案 §11.2），日志里只有 ``status=404``；
本次定位就靠手工捞 12 个可见性判定输入才确认是完成度不足。

本文件钉住两件事，缺一不可：

1. 真实原因码**只**进服务端 WARNING 日志（含 viewer/target，便于定位）；
2. 对外响应体**永远不含** ``denial_code``，也不含原因码子串 —— 这是「不泄露归属」
   的硬约束，一旦有人为了排障方便把它塞进 detail，本测试必须失败。
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.routes import ai_compatibility as route
from app.services.ai.compatibility import CandidateNotVisible


class _FakeDb:
    """只满足路由对 ``db.commit()`` 的调用；404 路径不会走到 commit。"""

    def __init__(self) -> None:
        self.committed = 0

    async def commit(self) -> None:
        self.committed += 1


def _gate_open_settings() -> SimpleNamespace:
    """与测试服实测一致的门禁配置：development + master/search/shadow 打开。"""

    return SimpleNamespace(
        ai_master_enabled=True,
        ai_profile_enabled=False,
        ai_search_enabled=True,
        ai_compatibility_shadow_enabled=True,
        ai_voice_enabled=False,
        ai_enabled=False,
        ai_avatar_provider="disabled",
        environment="development",
        ai_policy_approved=False,
        ai_provider_approved=False,
        ai_retention_policy_version=None,
        ai_provider="deepseek",
        ai_test_only_user_id_set=frozenset(),
    )


def _denying_reader(denial_code: str | None):
    """读取路径 3 参、重算路径 6 参，统一用 ``*_args`` 兼容两处替身。"""

    async def _deny(*_args: object):
        raise (
            CandidateNotVisible()
            if denial_code is None
            else CandidateNotVisible(denial_code)
        )

    return _deny


def _warning_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
    ]


@pytest.mark.asyncio
async def test_read_denial_is_logged_and_never_returned(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(route, "settings", _gate_open_settings())
    monkeypatch.setattr(
        route, "read_compatibility_snapshot", _denying_reader("PROFILE_INCOMPLETE")
    )

    with caplog.at_level(logging.WARNING, logger="app.api.routes.ai_compatibility"):
        with pytest.raises(HTTPException) as exc_info:
            await route.get_compatibility_route(
                target_user_id=23, current=SimpleNamespace(id=2), db=_FakeDb()
            )

    assert exc_info.value.status_code == 404
    detail = exc_info.value.detail
    assert detail["code"] == "CANDIDATE_NOT_VISIBLE"
    assert detail["message"] == "目标用户当前不可见"
    # 响应体不得出现原因码：既不能是新字段，也不能混在文案里。
    assert "denial_code" not in detail
    assert "PROFILE_INCOMPLETE" not in str(detail)

    lines = _warning_lines(caplog)
    assert any(
        "ai_visibility_denied scene=read" in line
        and "denial_code=PROFILE_INCOMPLETE" in line
        and "viewer_id=2" in line
        and "target_user_id=23" in line
        for line in lines
    ), lines


@pytest.mark.asyncio
async def test_llm_refresh_denial_logs_its_own_scene(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """读取命中 coverage_insufficient 后，精算触发路径的拒绝要能和读取区分开。"""
    monkeypatch.setattr(route, "settings", _gate_open_settings())

    async def _coverage_insufficient(_db, _viewer_id, _target_user_id):
        return SimpleNamespace(
            status=route.CompatibilitySnapshotStatus.COVERAGE_INSUFFICIENT.value
        )

    async def _deny(_db, _viewer_id, _target_user_id):
        raise CandidateNotVisible("BLOCKED_RELATIONSHIP")

    monkeypatch.setattr(route, "read_compatibility_snapshot", _coverage_insufficient)
    monkeypatch.setattr(route, "request_compatibility_llm_refresh", _deny)

    with caplog.at_level(logging.WARNING, logger="app.api.routes.ai_compatibility"):
        with pytest.raises(HTTPException) as exc_info:
            await route.get_compatibility_route(
                target_user_id=21, current=SimpleNamespace(id=2), db=_FakeDb()
            )

    assert exc_info.value.status_code == 404
    assert "BLOCKED_RELATIONSHIP" not in str(exc_info.value.detail)
    lines = _warning_lines(caplog)
    assert any(
        "ai_visibility_denied scene=llm_refresh" in line
        and "denial_code=BLOCKED_RELATIONSHIP" in line
        for line in lines
    ), lines


@pytest.mark.asyncio
async def test_recompute_denial_is_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(route, "settings", _gate_open_settings())
    monkeypatch.setattr(
        route, "request_compatibility_recompute", _denying_reader("SELF_REFERENCE")
    )

    with caplog.at_level(logging.WARNING, logger="app.api.routes.ai_compatibility"):
        with pytest.raises(HTTPException) as exc_info:
            await route.recompute_compatibility_route(
                body=SimpleNamespace(
                    expected_viewer_profile_revision=0,
                    expected_target_profile_revision=0,
                ),
                target_user_id=2,
                current=SimpleNamespace(id=2),
                db=_FakeDb(),
                idempotency_key="recompute-20261107-0001",
            )

    assert exc_info.value.status_code == 404
    lines = _warning_lines(caplog)
    assert any(
        "ai_visibility_denied scene=recompute" in line
        and "denial_code=SELF_REFERENCE" in line
        for line in lines
    ), lines


@pytest.mark.asyncio
async def test_missing_denial_code_falls_back_to_unknown_without_leaking(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """历史调用方仍可 ``CandidateNotVisible()`` 无参抛出：日志降级为 UNKNOWN，响应不变。"""
    monkeypatch.setattr(route, "settings", _gate_open_settings())
    monkeypatch.setattr(
        route, "read_compatibility_snapshot", _denying_reader(None)
    )

    with caplog.at_level(logging.WARNING, logger="app.api.routes.ai_compatibility"):
        with pytest.raises(HTTPException) as exc_info:
            await route.get_compatibility_route(
                target_user_id=23, current=SimpleNamespace(id=1), db=_FakeDb()
            )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["code"] == "CANDIDATE_NOT_VISIBLE"
    lines = _warning_lines(caplog)
    assert any(
        "ai_visibility_denied scene=read" in line and "denial_code=UNKNOWN" in line
        for line in lines
    ), lines


def test_exception_keeps_legacy_shape() -> None:
    """无参构造的既有契约不变：code/status_code/message 与新增属性互不影响。"""
    legacy = CandidateNotVisible()
    assert legacy.code == "CANDIDATE_NOT_VISIBLE"
    assert legacy.status_code == 404
    assert legacy.message == "目标用户当前不可见"
    assert legacy.denial_code is None
    assert CandidateNotVisible("PROFILE_INCOMPLETE").denial_code == "PROFILE_INCOMPLETE"

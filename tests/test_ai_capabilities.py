"""运行时 AI 能力探测（2026-11-02，B0）。

四种互不相干的测试反馈（AI 搜索不可用 / 画像不可用 / 语音不可用 / 用心度失败）
其实是同一个事实：AI 各能力默认关闭，前端却照常展示入口。本文件锁定：

1. ``/ai/capabilities`` 的取值等价于「对应接口此刻会不会 503」——
   直接复用 ``require_ai_feature``，不是另写一套判断；
2. master 关闭时其余能力恒为 false；
3. 路由真的挂上了。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api.routes import ai_capabilities as route
from app.services.ai.flags import AiFeature


def _settings(**overrides: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "ai_master_enabled": False,
        "ai_profile_enabled": False,
        "ai_search_enabled": False,
        "ai_compatibility_shadow_enabled": False,
        "ai_voice_enabled": False,
        "ai_voice_conversation_enabled": False,
        "ai_recommend_enabled": False,
        "ai_enabled": False,
        "ai_avatar_provider": "disabled",
        "environment": "development",
        "ai_test_only_user_id_set": frozenset(),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_master_off_disables_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        route,
        "settings",
        _settings(
            ai_master_enabled=False,
            ai_profile_enabled=True,
            ai_search_enabled=True,
            ai_compatibility_shadow_enabled=True,
            ai_voice_enabled=True,
            ai_enabled=True,
            ai_avatar_provider="qwen",
        ),
    )

    assert route._usable(AiFeature.PROFILE) is False
    assert route._usable(AiFeature.SEARCH) is False
    assert route._usable(AiFeature.COMPATIBILITY_SHADOW) is False
    assert route._usable(AiFeature.VOICE) is False
    assert route._usable(AiFeature.ADVISOR) is False
    assert route._usable(AiFeature.AVATAR) is False


def test_master_on_follows_each_feature_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        route,
        "settings",
        _settings(
            ai_master_enabled=True,
            ai_profile_enabled=True,
            ai_search_enabled=False,
            ai_compatibility_shadow_enabled=True,
            ai_voice_enabled=True,
            ai_enabled=True,
            ai_avatar_provider="disabled",
        ),
    )

    assert route._usable(AiFeature.PROFILE) is True
    assert route._usable(AiFeature.SEARCH) is False
    assert route._usable(AiFeature.COMPATIBILITY_SHADOW) is True
    assert route._usable(AiFeature.VOICE) is True
    assert route._usable(AiFeature.ADVISOR) is True
    assert route._usable(AiFeature.AVATAR) is False, "avatar provider 为 disabled 时不可用"


@pytest.mark.asyncio
async def test_endpoint_reports_master_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(route, "settings", _settings(ai_master_enabled=False))
    response = await route.get_ai_capabilities(None)  # type: ignore[arg-type]
    assert response.master is False
    assert response.model_dump() == {
        "master": False,
        "profile": False,
        "search": False,
        "compatibility": False,
        "voice": False,
        "advisor": False,
        "avatar": False,
    }


def test_route_is_registered() -> None:
    import warnings

    warnings.filterwarnings("ignore")
    from app.main import app

    paths = app.openapi()["paths"]
    assert "/api/v1/ai/capabilities" in paths
    assert "get" in paths["/api/v1/ai/capabilities"]

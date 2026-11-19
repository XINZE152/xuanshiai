"""AI 运行时关停层契约（PAGE-SWITCH-20261116，BE-04）。

纯内存单元契约，不依赖 MySQL/Redis：
1. kill=true → require_ai_feature 抛 AI_FEATURE_DISABLED；
2. kill 只关不开：不能打开 env 关闭的功能，生产审批门照常 fail-closed；
3. master_kill 覆盖全部 feature；缓存未初始化视为无 kill；
4. TTL 刷新与 DB 异常保留旧缓存；
5. /ai/capabilities 随 kill 变化且响应结构不变（向后兼容）；
6. public_ai_capabilities 不含用户测试范围检查。
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes import ai_capabilities as route
from app.schemas.ai_common import AiErrorCode
from app.services.ai import runtime_gates
from app.services.ai.flags import (
    AiFeature,
    AiFeatureDisabledError,
    public_ai_capabilities,
    require_ai_feature,
)


def _settings(**overrides: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "ai_master_enabled": True,
        "ai_profile_enabled": True,
        "ai_search_enabled": True,
        "ai_compatibility_shadow_enabled": True,
        "ai_voice_enabled": True,
        "ai_voice_conversation_enabled": True,
        "ai_recommend_enabled": True,
        "ai_enabled": True,
        "ai_avatar_provider": "openai_compatible",
        "ai_provider": "deepseek",
        "environment": "development",
        "ai_policy_approved": True,
        "ai_provider_approved": True,
        "ai_retention_policy_version": "v1",
        "ai_test_only_user_id_set": frozenset(),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _arm(**kills: bool) -> None:
    cache = {key: False for key in runtime_gates.KILL_KEYS}
    cache.update(kills)
    runtime_gates._cache = cache
    runtime_gates._cache_at = time.monotonic()


@pytest.fixture(autouse=True)
def _fresh_cache():
    runtime_gates.invalidate_cache()
    yield
    runtime_gates.invalidate_cache()


def test_uninitialized_cache_means_no_kill() -> None:
    assert runtime_gates.is_killed(AiFeature.SEARCH) is False
    assert runtime_gates.master_killed() is False
    require_ai_feature(AiFeature.SEARCH, _settings())


def test_kill_disables_enabled_feature() -> None:
    _arm(search_kill=True)
    with pytest.raises(AiFeatureDisabledError) as exc:
        require_ai_feature(AiFeature.SEARCH, _settings())
    assert exc.value.code == AiErrorCode.AI_FEATURE_DISABLED.value
    require_ai_feature(AiFeature.PROFILE, _settings())


def test_master_kill_disables_every_feature() -> None:
    _arm(master_kill=True)
    for feature in AiFeature:
        with pytest.raises(AiFeatureDisabledError):
            require_ai_feature(feature, _settings())


def test_kill_cannot_enable_env_disabled_feature() -> None:
    _arm(search_kill=False)
    with pytest.raises(AiFeatureDisabledError):
        require_ai_feature(AiFeature.SEARCH, _settings(ai_search_enabled=False))
    with pytest.raises(AiFeatureDisabledError):
        require_ai_feature(AiFeature.SEARCH, _settings(ai_master_enabled=False))


def test_production_approval_gate_still_fail_closed() -> None:
    _arm()
    with pytest.raises(AiFeatureDisabledError):
        require_ai_feature(
            AiFeature.SEARCH,
            _settings(environment="production", ai_policy_approved=False),
        )


def test_kill_key_mapping_covers_all_features() -> None:
    for feature in AiFeature:
        assert f"{feature.value}_kill" in runtime_gates.KILL_KEYS


@pytest.mark.asyncio
async def test_maybe_refresh_reads_snapshot_and_respects_ttl() -> None:
    calls: list[int] = []

    class _FakeResult:
        def mappings(self) -> _FakeResult:
            return self

        def first(self) -> dict[str, object]:
            return {"config_json": json.dumps({"search_kill": True})}

    class _FakeSession(AsyncSession):
        async def execute(self, *args: object, **kwargs: object) -> _FakeResult:
            calls.append(1)
            return _FakeResult()

    session = _FakeSession()
    await runtime_gates.maybe_refresh(session)
    assert runtime_gates.is_killed(AiFeature.SEARCH) is True
    assert runtime_gates.is_killed(AiFeature.PROFILE) is False
    assert len(calls) == 1
    await runtime_gates.maybe_refresh(session)
    assert len(calls) == 1, "TTL 内不得重复读库"
    runtime_gates._cache_at = time.monotonic() - runtime_gates.CACHE_TTL_SECONDS - 1
    assert runtime_gates.refresh_due() is True
    await runtime_gates.maybe_refresh(session)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_db_error_keeps_previous_cache() -> None:
    _arm(search_kill=True)
    runtime_gates._cache_at = time.monotonic() - runtime_gates.CACHE_TTL_SECONDS - 1

    class _BrokenSession(AsyncSession):
        async def execute(self, *args: object, **kwargs: object):
            raise RuntimeError("db down")

    await runtime_gates.maybe_refresh(_BrokenSession())
    assert runtime_gates.is_killed(AiFeature.SEARCH) is True, "DB 异常必须保留旧 kill 状态"


@pytest.mark.asyncio
async def test_capabilities_follow_kill_and_keep_structure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(route, "settings", _settings())
    response = await route.get_ai_capabilities(None)  # type: ignore[arg-type]
    assert response.master is True
    assert response.search is True
    _arm(master_kill=True)
    response = await route.get_ai_capabilities(None)  # type: ignore[arg-type]
    assert response.model_dump() == {
        "master": False,
        "profile": False,
        "search": False,
        "compatibility": False,
        "voice": False,
        "advisor": False,
        "avatar": False,
    }


def test_public_capabilities_ignore_user_test_scope() -> None:
    settings = _settings(ai_test_only_user_id_set=frozenset({42}))
    public = public_ai_capabilities(settings)
    assert public["search"] is True, "公开基准位不含用户范围检查"
    _arm(search_kill=True)
    public = public_ai_capabilities(settings)
    assert public["search"] is False
    assert public["profile"] is True
    _arm(master_kill=True)
    assert public_ai_capabilities(settings)["master"] is False


def test_public_capabilities_production_gate() -> None:
    settings = _settings(environment="production", ai_provider_approved=False)
    public = public_ai_capabilities(settings)
    assert public["master"] is True
    assert public["search"] is False
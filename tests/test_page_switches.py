"""页面显示开关契约（PAGE-SWITCH-20261116，BE-02/BE-03）。

纯内存/假库单元契约，不依赖 MySQL/Redis：
1. 注册表 71 路径与 pages.json 2026-11-16 快照一致（前端改 pages.json 需同步）；
2. 生效值 = 模块 AND 页面，缺键 fail-open，锚点六页（登录/注册/首页/引导/家长两页）恒开；
3. TTL 缓存与 DB 异常保留旧缓存；
4. 路由匿名可达、429 限流、无鉴权依赖。
"""

from __future__ import annotations

import inspect
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.dependencies import get_db
from app.api.routes import public_page_switches as route
from app.services import page_switches as service

# pages.json 2026-11-16 快照：7 主包 + 64 分包 = 71。
EXPECTED_PAGES = frozenset({
    "pages/index/index", "pages/community/community", "pages/matchmaker/matchmaker",
    "pages/message/message", "pages/profile/profile", "pages/auth/login", "pages/auth/register",
    "pagesSub/emotionLab/emotion-lab",
    "pagesSub/parent/parent", "pagesSub/parent/user-detail",
    "pagesSub/community/publish", "pagesSub/community/topic-list",
    "pagesSub/community/topic-detail", "pagesSub/community/post-detail",
    "pagesSub/community/activity-list", "pagesSub/community/activity-detail",
    "pagesSub/community/my-activities", "pagesSub/community/notifications",
    "pagesSub/community/paper-plane", "pagesSub/community/dating-plane",
    "pagesSub/community/dating-plane-compose", "pagesSub/community/paper-plane-messages",
    "pagesSub/community/paper-plane-chat", "pagesSub/community/paper-plane-sent",
    "pagesSub/matchmaker/commission-level", "pagesSub/matchmaker/apply",
    "pagesSub/matchmaker/detail", "pagesSub/matchmaker/rank",
    "pagesSub/matchmaker/payment", "pagesSub/matchmaker/become-matchmaker",
    "pagesSub/matchmaker/become-partner", "pagesSub/matchmaker/partner-center",
    "pagesSub/matchmaker/become-promoter", "pagesSub/matchmaker/promoter-center",
    "pagesSub/matchmaker/application-success", "pagesSub/matchmaker/custom",
    "pagesSub/matchmaker/my-account",
    "pagesSub/profileExtra/settings", "pagesSub/profileExtra/help",
    "pagesSub/profileExtra/security", "pagesSub/profileExtra/certification",
    "pagesSub/profileExtra/unlock-history", "pagesSub/profileExtra/vip",
    "pagesSub/profileExtra/my-moments", "pagesSub/profileExtra/my-tasks",
    "pagesSub/profileExtra/my-registration", "pagesSub/profileExtra/my-date",
    "pagesSub/profileExtra/my-date-service", "pagesSub/profileExtra/my-posters",
    "pagesSub/profileExtra/my-poster-preview", "pagesSub/profileExtra/history",
    "pagesSub/profileExtra/visitors", "pagesSub/profileExtra/search",
    "pagesSub/profileExtra/report", "pagesSub/profileExtra/applications",
    "pagesSub/profileExtra/favorites", "pagesSub/profileExtra/spotlights",
    "pagesSub/profileExtra/support", "pagesSub/profileExtra/my-ai-avatar",
    "pagesSub/profileExtra/my-portrait-master", "pagesSub/profileExtra/my-portrait-result",
    "pagesSub/profileExtra/my-portrait-archive",
    "pagesSub/chat/detail",
    "pagesSub/about/about", "pagesSub/about/article",
    "pagesSub/userExtra/onboarding/profile", "pagesSub/userExtra/user/detail",
    "pagesSub/userExtra/user/edit", "pagesSub/userExtra/user/preference",
    "pagesSub/userExtra/mytags/edit", "pagesSub/userExtra/index/top-placement",
})

EXPECTED_TABS = frozenset({
    "pages/index/index", "pages/matchmaker/matchmaker", "pages/community/community",
    "pages/message/message", "pages/profile/profile",
})

# 与前端 page-switch-store ALWAYS_ENABLED 六页白名单对齐（2026-11-17）：
# 首页/登录/注册 + 引导/家长两页（enforceParentRoute 豁免，防重定向死循环）。
ANCHORS = frozenset({
    "pages/auth/login", "pages/auth/register", "pages/index/index",
    "pagesSub/userExtra/onboarding/profile",
    "pagesSub/parent/parent", "pagesSub/parent/user-detail",
})


@pytest.fixture(autouse=True)
def _fresh_cache():
    service.invalidate_cache()
    yield
    service.invalidate_cache()


def test_registry_matches_pages_json_snapshot() -> None:
    assert len(EXPECTED_PAGES) == 71
    assert set(service.PAGE_REGISTRY) == EXPECTED_PAGES
    assert set(service.TAB_PATHS) == EXPECTED_TABS
    assert service.ANCHOR_PAGES == ANCHORS
    assert len(service.MODULE_KEYS) == 10
    assert all(meta.module in service.MODULE_KEYS for meta in service.PAGE_REGISTRY.values())


def test_default_config_is_all_open() -> None:
    config = service.default_page_switches_config()
    assert set(config["pages"]) == EXPECTED_PAGES
    assert all(config["pages"].values())
    assert all(config["modules"].values())
    assert config["placeholder_text"] == service.DEFAULT_PLACEHOLDER_TEXT


def test_module_off_disables_its_pages_only() -> None:
    config = {"modules": {"ai": False}, "pages": {}}
    effective = service._effective(config)
    assert effective["pagesSub/profileExtra/search"] is False
    assert effective["pagesSub/profileExtra/my-portrait-master"] is False
    assert effective["pagesSub/community/post-detail"] is True
    assert effective["pagesSub/chat/detail"] is True, "chat 共用页不受 ai 模块开关影响"


def test_page_override_is_independent_and_missing_keys_fail_open() -> None:
    config = {"modules": {}, "pages": {"pagesSub/community/publish": False}}
    effective = service._effective(config)
    assert effective["pagesSub/community/publish"] is False
    assert effective["pagesSub/community/post-detail"] is True
    assert effective["pagesSub/matchmaker/apply"] is True


def test_anchor_pages_never_disable() -> None:
    config = {
        "modules": {"system": False, "parent": False},
        "pages": {path: False for path in EXPECTED_PAGES},
    }
    effective = service._effective(config)
    for path in ANCHORS:
        assert effective[path] is True, f"锚点页 {path} 必须恒开"
    assert effective["pages/profile/profile"] is False


def test_bool_tolerance_for_legacy_string_values() -> None:
    config = {"modules": {"ai": "false"}, "pages": {"pagesSub/community/publish": "0"}}
    effective = service._effective(config)
    assert effective["pagesSub/profileExtra/search"] is False
    assert effective["pagesSub/community/publish"] is False


@pytest.mark.asyncio
async def test_db_error_keeps_previous_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    service._cache = {"config": {"modules": {"ai": False}}, "version": 7}
    service._cache_at = time.monotonic() - service.CACHE_TTL_SECONDS - 1

    async def broken(db: object) -> tuple[dict, int]:
        raise RuntimeError("db down")

    monkeypatch.setattr(service, "_load_snapshot", broken)

    class _BrokenSession(AsyncSession):
        pass

    payload = await service._snapshot(_BrokenSession())
    assert payload["version"] == 7
    assert payload["config"]["modules"]["ai"] is False


@pytest.mark.asyncio
async def test_payload_defaults_when_no_snapshot() -> None:
    payload = await service.page_switches_payload(None)  # type: ignore[arg-type]
    assert payload["version"] == 0
    assert set(payload["pages"]) == EXPECTED_PAGES
    assert all(payload["pages"].values())
    assert payload["tabs"] == list(service.TAB_PATHS)
    assert payload["placeholder_text"] == service.DEFAULT_PLACEHOLDER_TEXT


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
        "ai_provider": "mock",
        "environment": "development",
        "ai_policy_approved": False,
        "ai_provider_approved": False,
        "ai_retention_policy_version": "",
        "ai_test_only_user_id_set": frozenset(),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_route_signature_has_no_auth_dependency() -> None:
    assert "current" not in inspect.signature(route.get_public_page_switches).parameters


def _client(monkeypatch: pytest.MonkeyPatch, *, allowed: bool) -> TestClient:
    async def limiter(key: str, limit: int, window: int) -> bool:
        return allowed

    monkeypatch.setattr(route, "consume_rate_limit", limiter)
    monkeypatch.setattr(route, "settings", _settings())
    app = FastAPI()
    app.include_router(route.router)
    app.dependency_overrides[get_db] = lambda: None
    return TestClient(app)


def test_route_is_anonymous_and_returns_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, allowed=True)
    response = client.get("/public/page-switches")
    assert response.status_code == 200
    body = response.json()
    assert len(body["pages"]) == 71
    assert all(body["pages"][path] is True for path in ANCHORS)
    assert len(body["tabs"]) == 5
    assert body["operation"]["registration_enabled"] is True
    assert body["operation"]["browse_enabled"] is True
    assert body["ai"]["master"] is False


def test_route_rate_limited_returns_429(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, allowed=False)
    response = client.get("/public/page-switches")
    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "PUBLIC_PAGE_SWITCHES_RATE_LIMITED"


@pytest.mark.asyncio
async def test_rate_limit_exhausted_raises_before_db(monkeypatch: pytest.MonkeyPatch) -> None:
    async def denied(key: str, limit: int, window: int) -> bool:
        return False

    monkeypatch.setattr(route, "consume_rate_limit", denied)
    request = SimpleNamespace(headers={}, client=SimpleNamespace(host="1.2.3.4"))
    with pytest.raises(HTTPException) as exc:
        await route.get_public_page_switches(request, None)  # type: ignore[arg-type]
    assert exc.value.status_code == 429
    assert exc.value.detail["retryable"] is True


def test_route_is_registered() -> None:
    import warnings

    warnings.filterwarnings("ignore")
    from app.main import app

    paths = app.openapi()["paths"]
    assert "/api/v1/public/page-switches" in paths
    assert "get" in paths["/api/v1/public/page-switches"]
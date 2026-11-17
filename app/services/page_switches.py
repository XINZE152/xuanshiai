"""页面显示开关注册表与生效值计算（PAGE-SWITCH-20261116）。

生效值 = 模块开关 AND 页面开关，只在后端计算，前端不复制与/或逻辑
（与 /ai/capabilities 同一设计哲学，避免两处漂移）。缺失/异常一律
fail-open 到默认全开；锚点页恒开：登录/注册关闭会把用户锁在 App 外，
首页是前端守卫 reLaunch 的重定向锚点，引导页/家长两页与前端
enforceParentRoute 共享豁免白名单（关掉会形成家长模式重定向死循环，
且前端对这六页不加守卫，后端必须保证关不死）。

注册表路径清单与前端 pages.json 2026-11-16 快照一致（7 主包 + 64 分包）；
前端改 pages.json 时必须同步本注册表与 tests/test_page_switches.py。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

NAMESPACE = "platform_page_switches"
CACHE_TTL_SECONDS = 30.0
DEFAULT_PLACEHOLDER_TEXT = "该功能维护中，敬请期待"

MODULE_KEYS: tuple[str, ...] = (
    "ai", "emotion_lab", "community", "paper_plane", "matchmaker",
    "membership", "parent", "profile_tools", "chat", "system",
)


@dataclass(frozen=True)
class PageMeta:
    module: str
    is_tab: bool = False
    allow_disable: bool = True


PAGE_REGISTRY: dict[str, PageMeta] = {
    # 主包（7）
    "pages/index/index": PageMeta("system", is_tab=True, allow_disable=False),
    "pages/community/community": PageMeta("community", is_tab=True),
    "pages/matchmaker/matchmaker": PageMeta("matchmaker", is_tab=True),
    "pages/message/message": PageMeta("system", is_tab=True),
    "pages/profile/profile": PageMeta("system", is_tab=True),
    "pages/auth/login": PageMeta("system", allow_disable=False),
    "pages/auth/register": PageMeta("system", allow_disable=False),
    # 情感实验室（1）
    "pagesSub/emotionLab/emotion-lab": PageMeta("emotion_lab"),
    # 家长端（2）：与前端 enforceParentRoute 豁免白名单对齐，恒开
    "pagesSub/parent/parent": PageMeta("parent", allow_disable=False),
    "pagesSub/parent/user-detail": PageMeta("parent", allow_disable=False),
    # 社区（8 分包页）
    "pagesSub/community/publish": PageMeta("community"),
    "pagesSub/community/topic-list": PageMeta("community"),
    "pagesSub/community/topic-detail": PageMeta("community"),
    "pagesSub/community/post-detail": PageMeta("community"),
    "pagesSub/community/activity-list": PageMeta("community"),
    "pagesSub/community/activity-detail": PageMeta("community"),
    "pagesSub/community/my-activities": PageMeta("community"),
    "pagesSub/community/notifications": PageMeta("community"),
    # 纸飞机（6）
    "pagesSub/community/paper-plane": PageMeta("paper_plane"),
    "pagesSub/community/dating-plane": PageMeta("paper_plane"),
    "pagesSub/community/dating-plane-compose": PageMeta("paper_plane"),
    "pagesSub/community/paper-plane-messages": PageMeta("paper_plane"),
    "pagesSub/community/paper-plane-chat": PageMeta("paper_plane"),
    "pagesSub/community/paper-plane-sent": PageMeta("paper_plane"),
    # 红娘（13）
    "pagesSub/matchmaker/commission-level": PageMeta("matchmaker"),
    "pagesSub/matchmaker/apply": PageMeta("matchmaker"),
    "pagesSub/matchmaker/detail": PageMeta("matchmaker"),
    "pagesSub/matchmaker/rank": PageMeta("matchmaker"),
    "pagesSub/matchmaker/payment": PageMeta("matchmaker"),
    "pagesSub/matchmaker/become-matchmaker": PageMeta("matchmaker"),
    "pagesSub/matchmaker/become-partner": PageMeta("matchmaker"),
    "pagesSub/matchmaker/partner-center": PageMeta("matchmaker"),
    "pagesSub/matchmaker/become-promoter": PageMeta("matchmaker"),
    "pagesSub/matchmaker/promoter-center": PageMeta("matchmaker"),
    "pagesSub/matchmaker/application-success": PageMeta("matchmaker"),
    "pagesSub/matchmaker/custom": PageMeta("matchmaker"),
    "pagesSub/matchmaker/my-account": PageMeta("matchmaker"),
    # AI（5）：chat/detail 的 AI 模式不在此列，仍由 /ai/capabilities 判定
    "pagesSub/profileExtra/search": PageMeta("ai"),
    "pagesSub/profileExtra/my-ai-avatar": PageMeta("ai"),
    "pagesSub/profileExtra/my-portrait-master": PageMeta("ai"),
    "pagesSub/profileExtra/my-portrait-result": PageMeta("ai"),
    "pagesSub/profileExtra/my-portrait-archive": PageMeta("ai"),
    # 会员/付费服务（4）
    "pagesSub/profileExtra/vip": PageMeta("membership"),
    "pagesSub/profileExtra/unlock-history": PageMeta("membership"),
    "pagesSub/profileExtra/my-date": PageMeta("membership"),
    "pagesSub/profileExtra/my-date-service": PageMeta("membership"),
    # 个人资料工具（16）
    "pagesSub/profileExtra/settings": PageMeta("profile_tools"),
    "pagesSub/profileExtra/help": PageMeta("profile_tools"),
    "pagesSub/profileExtra/security": PageMeta("profile_tools"),
    "pagesSub/profileExtra/certification": PageMeta("profile_tools"),
    "pagesSub/profileExtra/my-moments": PageMeta("profile_tools"),
    "pagesSub/profileExtra/my-tasks": PageMeta("profile_tools"),
    "pagesSub/profileExtra/my-registration": PageMeta("profile_tools"),
    "pagesSub/profileExtra/my-posters": PageMeta("profile_tools"),
    "pagesSub/profileExtra/my-poster-preview": PageMeta("profile_tools"),
    "pagesSub/profileExtra/history": PageMeta("profile_tools"),
    "pagesSub/profileExtra/visitors": PageMeta("profile_tools"),
    "pagesSub/profileExtra/report": PageMeta("profile_tools"),
    "pagesSub/profileExtra/applications": PageMeta("profile_tools"),
    "pagesSub/profileExtra/favorites": PageMeta("profile_tools"),
    "pagesSub/profileExtra/spotlights": PageMeta("profile_tools"),
    "pagesSub/profileExtra/support": PageMeta("profile_tools"),
    # 聊天（1）：AI 与普通聊天共用页，页面开关只控整体
    "pagesSub/chat/detail": PageMeta("chat"),
    # 关于（2）
    "pagesSub/about/about": PageMeta("system"),
    "pagesSub/about/article": PageMeta("system"),
    # 用户资料流程（6）
    "pagesSub/userExtra/onboarding/profile": PageMeta("system", allow_disable=False),
    "pagesSub/userExtra/user/detail": PageMeta("system"),
    "pagesSub/userExtra/user/edit": PageMeta("system"),
    "pagesSub/userExtra/user/preference": PageMeta("system"),
    "pagesSub/userExtra/mytags/edit": PageMeta("system"),
    "pagesSub/userExtra/index/top-placement": PageMeta("system"),
}

TAB_PATHS: tuple[str, ...] = tuple(p for p, m in PAGE_REGISTRY.items() if m.is_tab)
ANCHOR_PAGES: frozenset[str] = frozenset(
    p for p, m in PAGE_REGISTRY.items() if not m.allow_disable
)


def default_page_switches_config() -> dict[str, Any]:
    """admin_config DEFAULT_CONFIGS 用的默认快照（全开）。"""
    return {
        "modules": {key: True for key in MODULE_KEYS},
        "pages": {path: True for path in PAGE_REGISTRY},
        "placeholder_text": DEFAULT_PLACEHOLDER_TEXT,
    }


def _as_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "y"}
    return default


_cache: dict[str, Any] | None = None
_cache_at: float = 0.0


def invalidate_cache() -> None:
    global _cache, _cache_at
    _cache = None
    _cache_at = 0.0


async def _load_snapshot(db: AsyncSession) -> tuple[dict[str, Any], int]:
    result = await db.execute(
        text(
            "SELECT config_json, version FROM admin_config_snapshot "
            "WHERE namespace = :namespace"
        ),
        {"namespace": NAMESPACE},
    )
    row = result.mappings().first()
    if not row:
        return {}, 0
    try:
        config = json.loads(row["config_json"])
    except (TypeError, json.JSONDecodeError):
        config = {}
    return (config if isinstance(config, dict) else {}), int(row["version"])


async def _snapshot(db: AsyncSession) -> dict[str, Any]:
    """TTL 缓存的原始快照；DB 异常保留旧缓存，无缓存时 fail-open 默认。"""
    global _cache, _cache_at
    now = time.monotonic()
    if _cache is not None and now - _cache_at < CACHE_TTL_SECONDS:
        return _cache
    config: dict[str, Any] = {}
    version = 0
    if isinstance(db, AsyncSession):
        try:
            config, version = await _load_snapshot(db)
        except Exception:
            if _cache is not None:
                return _cache
    payload = {"config": config, "version": version}
    _cache = payload
    _cache_at = now
    return payload


def _effective(config: dict[str, Any]) -> dict[str, bool]:
    modules = config.get("modules")
    pages = config.get("pages")
    modules = modules if isinstance(modules, dict) else {}
    pages = pages if isinstance(pages, dict) else {}
    result: dict[str, bool] = {}
    for path, meta in PAGE_REGISTRY.items():
        if not meta.allow_disable:
            result[path] = True
            continue
        module_on = _as_bool(modules.get(meta.module), True)
        page_on = _as_bool(pages.get(path), True)
        result[path] = module_on and page_on
    return result


async def effective_page_switches(db: AsyncSession) -> dict[str, bool]:
    return _effective((await _snapshot(db))["config"])


async def page_switches_payload(db: AsyncSession) -> dict[str, Any]:
    """GET /public/page-switches 的页面开关部分（不含 operation/ai）。"""
    snapshot = await _snapshot(db)
    config = snapshot["config"]
    modules_raw = config.get("modules")
    modules_raw = modules_raw if isinstance(modules_raw, dict) else {}
    placeholder = config.get("placeholder_text")
    return {
        "version": snapshot["version"],
        "modules": {key: _as_bool(modules_raw.get(key), True) for key in MODULE_KEYS},
        "pages": _effective(config),
        "tabs": list(TAB_PATHS),
        "placeholder_text": str(placeholder) if placeholder not in (None, "") else DEFAULT_PLACEHOLDER_TEXT,
    }
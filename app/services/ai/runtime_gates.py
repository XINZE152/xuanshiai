"""AI 运行时关停层（PAGE-SWITCH-20261116，BE-04）。

应急通道：管理端 PATCH ``ai_runtime`` 后 ≤30 秒（TTL）在各实例生效，
无需改 env/重启。安全方向与 flags 的 fail-closed 一致：**kill 只能把
功能关死，不能打开 env 开关或生产审批门关闭的功能**。

DB 异常保留旧缓存；缓存未初始化视为无 kill（env 门禁照常）。
本模块不得在运行期 import flags（flags 依赖本模块），类型引用走
TYPE_CHECKING。
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.services.ai.flags import AiFeature

NAMESPACE = "ai_runtime"
CACHE_TTL_SECONDS = 30.0

KILL_KEYS: frozenset[str] = frozenset({
    "master_kill", "profile_kill", "search_kill", "compatibility_shadow_kill",
    "voice_kill", "voice_conversation_kill", "recommend_kill",
    "advisor_kill", "avatar_kill",
})

_cache: dict[str, bool] | None = None
_cache_at: float = 0.0


def invalidate_cache() -> None:
    global _cache, _cache_at
    _cache = None
    _cache_at = 0.0


def refresh_due() -> bool:
    return _cache is None or time.monotonic() - _cache_at >= CACHE_TTL_SECONDS


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "y"}
    return False


async def maybe_refresh(db: AsyncSession) -> None:
    """TTL 到期才读快照；DB 异常保留旧缓存，绝不抛出。"""
    global _cache, _cache_at
    if not refresh_due():
        return
    if not isinstance(db, AsyncSession):
        _cache = {key: False for key in KILL_KEYS}
        _cache_at = time.monotonic()
        return
    try:
        result = await db.execute(
            text("SELECT config_json FROM admin_config_snapshot WHERE namespace = :namespace"),
            {"namespace": NAMESPACE},
        )
        row = result.mappings().first()
        config = json.loads(row["config_json"]) if row else {}
        if not isinstance(config, dict):
            config = {}
    except Exception:
        if _cache is None:
            _cache = {key: False for key in KILL_KEYS}
            _cache_at = time.monotonic()
        return
    _cache = {key: _as_bool(config.get(key)) for key in KILL_KEYS}
    _cache_at = time.monotonic()


def master_killed() -> bool:
    return bool(_cache is not None and _cache.get("master_kill"))


def is_killed(feature: AiFeature) -> bool:
    if _cache is None:
        return False
    if _cache.get("master_kill"):
        return True
    return bool(_cache.get(f"{feature.value}_kill", False))
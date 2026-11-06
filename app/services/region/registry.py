"""属地解析器注册表：按 IP/手机分别选择 offline / api，并支持 API 失败回落离线。

兼容旧配置：``region_resolver_provider=offline`` 仍映射为 offline；
``real`` 映射为 api，``mock`` 保持干跑语义。
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime

from app.core.config import settings
from app.services.region import ip_offline, online_ip, online_phone, phone_offline
from app.services.region.types import (
    RegionDataNotReady,
    RegionKind,
    RegionUpstreamError,
    ResolveOutcome,
)

logger = logging.getLogger(__name__)

_PROVIDER_DISABLED = "disabled"
_PROVIDER_MOCK = "mock"
_PROVIDER_OFFLINE = "offline"
_PROVIDER_API = "api"
_LEGACY_REAL = "real"


def provider() -> str:
    """旧版总开关，保留用于状态展示与兼容映射。"""
    return settings.region_resolver_provider


def provider_for(kind: RegionKind) -> str:
    """按类型解析有效 provider；新配置优先，旧总开关兜底。"""
    configured = settings.region_ip_provider if kind is RegionKind.IP else settings.region_phone_provider
    if configured != _PROVIDER_DISABLED:
        return configured
    legacy = provider()
    if legacy == _PROVIDER_OFFLINE:
        return _PROVIDER_OFFLINE
    if legacy == _LEGACY_REAL:
        return _PROVIDER_API
    if legacy == _PROVIDER_MOCK:
        return _PROVIDER_MOCK
    return _PROVIDER_DISABLED


def fallback_provider(kind: RegionKind) -> str:
    return settings.region_ip_fallback_provider if kind is RegionKind.IP else settings.region_phone_fallback_provider


def refresh_enabled() -> bool:
    return any(provider_for(kind) != _PROVIDER_DISABLED for kind in RegionKind)


def is_ready(kind: RegionKind) -> bool:
    """离线资产就绪度（状态接口的 ip_db_ready / phone_segment_ready）。"""
    if kind is RegionKind.IP:
        return ip_offline.is_ready()
    return phone_offline.is_ready()


def api_ready(kind: RegionKind) -> bool:
    return online_ip.configured() if kind is RegionKind.IP else online_phone.configured()


def adapter_implemented(kind: RegionKind) -> bool:
    """当前配置下该类型是否有可用适配器。"""
    current = provider_for(kind)
    if current == _PROVIDER_OFFLINE:
        return is_ready(kind)
    if current == _PROVIDER_API:
        return api_ready(kind)
    return False


def data_version(kind: RegionKind) -> str | None:
    """离线数据文件标识（文件名 + 修改日期），供前端展示排查。"""
    if provider_for(kind) == _PROVIDER_API:
        return None
    path = ip_offline.resolve_db_path() if kind is RegionKind.IP else phone_offline.resolve_segment_path()
    if not path.is_file():
        return None
    return f"{path.name}@{datetime.fromtimestamp(path.stat().st_mtime):%Y-%m-%d}"


def _offline_resolve(kind: RegionKind, values: list[str]) -> ResolveOutcome:
    if kind is RegionKind.IP:
        outcome = ip_offline.resolve_many(values)
    else:
        outcome = phone_offline.resolve_many(values)
    return replace(outcome, provider=_PROVIDER_OFFLINE)


def resolve(kind: RegionKind, values: list[str]) -> ResolveOutcome:
    """同步离线解析，保留给旧调用与测试；在线 provider 请使用 ``resolve_async``。"""
    current = provider_for(kind)
    if current == _PROVIDER_DISABLED:
        raise RegionDataNotReady("属地解析服务未接入（厂商未配置）")
    if current == _PROVIDER_API:
        raise RegionDataNotReady("在线 provider 必须使用 resolve_async")
    if current == _PROVIDER_MOCK:
        raise RegionDataNotReady("mock provider 不执行真实解析")
    if current != _PROVIDER_OFFLINE:
        raise RegionDataNotReady(f"未知的属地 provider：{current}")
    return _offline_resolve(kind, values)


async def resolve_async(kind: RegionKind, values: list[str]) -> ResolveOutcome:
    current = provider_for(kind)
    if current == _PROVIDER_DISABLED:
        raise RegionDataNotReady("属地解析服务未接入（厂商未配置）")
    if current == _PROVIDER_MOCK:
        raise RegionDataNotReady("mock provider 不执行真实解析")
    if current == _PROVIDER_OFFLINE:
        return _offline_resolve(kind, values)
    if current != _PROVIDER_API:
        raise RegionDataNotReady(f"未知的属地 provider：{current}")

    try:
        if kind is RegionKind.IP:
            return await online_ip.resolve_many(values)
        return await online_phone.resolve_many(values)
    except RegionUpstreamError:
        if fallback_provider(kind) == _PROVIDER_OFFLINE and is_ready(kind):
            logger.warning("region api failed, fallback to offline: kind=%s", kind.value)
            return _offline_resolve(kind, values)
        raise

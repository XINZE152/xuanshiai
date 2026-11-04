"""IP → 属地（离线 ip2region）。

数据与绑定都是外部资产，缺失即 ``RegionDataNotReady``（路由层转 503），不做任何兜底写入：

- 库文件：``settings.region_ip_db_path``，缺省 ``data/region/ip2region_v4.xdb``；
- 查询绑定：优先 ``app/services/region/vendor/xdbSearcher.py``，其次任意可导入的
  ``xdbSearcher`` 模块（官方 binding 单文件即可）。

字段布局随数据版本变化（开源 3.x 为 ``国家|省份|城市|ISP``，商用满载版多出大洲与区县），
因此**不硬编码下标**，一律按 ``settings.region_ip_city_index`` 取「过滤掉 0 与空段后」的城市位置。
"""

from __future__ import annotations

import importlib
import ipaddress
import threading
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.region.types import RegionDataNotReady, ResolveOutcome

_CONTINENTS = frozenset({"亚洲", "欧洲", "非洲", "北美洲", "南美洲", "大洋洲", "南极洲"})
_HOME_COUNTRY = "中国"
# 运营商 CGNAT 共享地址段：``ipaddress`` 的 ``is_private`` 不覆盖它，必须显式排除。
# 该段出现在 ``register_ip`` 通常意味着反向代理配置异常或客户端伪造 XFF，而非真实用户出口。
_CGNAT = ipaddress.ip_network("100.64.0.0/10")

_LOCK = threading.Lock()
_SEARCHER_CACHE: dict[str, Any] = {}


def default_db_path() -> Path:
    """项目内约定的默认库文件位置。"""
    return Path(__file__).resolve().parents[3] / "data" / "region" / "ip2region_v4.xdb"


def resolve_db_path() -> Path:
    raw = (settings.region_ip_db_path or "").strip()
    return Path(raw) if raw else default_db_path()


def clean_ip(raw: str | None) -> str | None:
    """归一化并校验源 IP；不可用一律返回 ``None``（计入 skipped，不送解析）。

    覆盖 ``register_ip`` 的常见脏形态：带端口、X-Forwarded-For 逗号串、首尾空白/引号。
    IPv6 属**策略性跳过**（v6 库未启用时），与"非法"同样返回 None，但原因不同：
    前者可通过下发 ``ip2region_v6.xdb`` 恢复，后者永远不可解析。
    """
    if not raw:
        return None
    value = str(raw).strip().strip('"').strip("'")
    if not value:
        return None
    if "," in value:
        value = value.split(",", 1)[0].strip()
    # IPv4:port（仅当冒号恰好一个且右侧为端口时剥离）
    if value.count(":") == 1 and "." in value:
        host, _, port = value.partition(":")
        if port.isdigit():
            value = host
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        return None
    if isinstance(addr, ipaddress.IPv6Address):
        return None
    if addr in _CGNAT:
        return None
    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    ):
        return None
    return str(addr)


def format_region(raw: str) -> str | None:
    """把 xdb 返回的 ``a|b|c|d`` 串格式化为「省+市」。"""
    parts = [p.strip() for p in raw.split("|") if p.strip() and p.strip() != "0"]
    if not parts:
        return None
    country = parts[0]
    if country in _CONTINENTS and len(parts) > 1:
        country = parts[1]
    if country != _HOME_COUNTRY:
        return country or None
    index = max(2, int(settings.region_ip_city_index))
    province = parts[index - 1] if index - 1 < len(parts) else ""
    city = parts[index] if index < len(parts) else ""
    if not province and not city:
        return None
    if not city or city == province:
        return province
    return f"{province}{city}"


def _import_searcher_class() -> Any | None:
    for module_name in ("app.services.region.vendor.xdbSearcher", "xdbSearcher"):
        try:
            module = importlib.import_module(module_name)
        except Exception:  # 绑定缺失不是错误，交给调用方判定未就绪
            continue
        searcher_cls = getattr(module, "XdbSearcher", None) or getattr(module, "Searcher", None)
        if searcher_cls is not None:
            return searcher_cls
    return None


def _build_searcher(searcher_cls: Any, db_path: str) -> Any:
    for factory in (
        lambda: searcher_cls.newWithFileOnly(db_path),
        lambda: searcher_cls(db_path),
        lambda: searcher_cls(dbPath=db_path),
    ):
        try:
            return factory()
        except Exception:
            continue
    raise RegionDataNotReady("IP 库查询绑定与数据文件版本不兼容")


def _get_searcher() -> Any:
    path = resolve_db_path()
    key = str(path)
    cached = _SEARCHER_CACHE.get(key)
    if cached is not None:
        return cached
    with _LOCK:
        cached = _SEARCHER_CACHE.get(key)
        if cached is not None:
            return cached
        if not path.is_file():
            raise RegionDataNotReady(f"IP 库文件未就绪：{path}")
        searcher_cls = _import_searcher_class()
        if searcher_cls is None:
            raise RegionDataNotReady("IP 库查询绑定未就绪：需 xdbSearcher 模块")
        searcher = _build_searcher(searcher_cls, str(path))
        _SEARCHER_CACHE[key] = searcher
        return searcher


def binding_available() -> bool:
    """只读探测查询绑定是否可导入（不构造 searcher）。"""
    return _import_searcher_class() is not None


def is_ready() -> bool:
    """只读探测：库文件存在且绑定可导入（不构造 searcher，避免读盘放大）。"""
    return resolve_db_path().is_file() and binding_available()


def resolve_many(values: list[str]) -> ResolveOutcome:
    """批量解析原始 IP 值；返回原始值 → 属地的映射。"""
    cleaned: dict[str, list[str]] = {}
    skipped = 0
    for value in values:
        ip = clean_ip(value)
        if ip is None:
            skipped += 1
            continue
        cleaned.setdefault(ip, []).append(value)
    if not cleaned:
        return ResolveOutcome(resolved={}, skipped=skipped, unknown=0)

    searcher = _get_searcher()
    resolved: dict[str, str] = {}
    unknown = 0
    for ip, originals in cleaned.items():
        try:
            raw = searcher.search(ip)
        except Exception:
            raw = None
        region = format_region(str(raw)) if raw else None
        if region is None:
            unknown += len(originals)
            continue
        for original in originals:
            resolved[original] = region
    return ResolveOutcome(resolved=resolved, skipped=skipped, unknown=unknown)

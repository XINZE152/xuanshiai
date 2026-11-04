"""阿里云云市场 IP 归属地（AppCode）适配器。

商品差异通过 ``REGION_IP_API_*`` 配置吸收：Endpoint/Path/Method/请求字段/返回字段
均可覆盖；未识别的返回按 unknown 处理，不写库。日志与异常不包含 IP 原文。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.core.config import settings
from app.services.region import ip_offline
from app.services.region.http_client import RegionHttpClient
from app.services.region.types import (
    RegionApiNotConfigured,
    RegionUpstreamError,
    ResolveOutcome,
)

logger = logging.getLogger(__name__)


def _clean(value: str | None) -> str:
    text = (value or "").strip()
    return "" if text.upper().startswith("YOUR_") else text


def _secret(value: Any) -> str:
    return _clean(value.get_secret_value()) if value is not None else ""


def configured() -> bool:
    return bool(_clean(settings.region_ip_api_endpoint) and _secret(settings.region_ip_api_app_code))


def _url() -> str:
    endpoint = _clean(settings.region_ip_api_endpoint)
    if not endpoint:
        raise RegionApiNotConfigured("阿里云 IP API Endpoint 未配置")
    if not endpoint.startswith(("http://", "https://")):
        endpoint = f"https://{endpoint}"
    path = _clean(settings.region_ip_api_path)
    if path and not path.startswith("/"):
        path = f"/{path}"
    return endpoint.rstrip("/") + path


def _body_root(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("showapi_res_body", "data", "result", "body", "location"):
        value = payload.get(key)
        if isinstance(value, dict):
            for nested in ("location", "result", "body"):
                inner = value.get(nested)
                if isinstance(inner, dict):
                    return inner
            return value
    return payload


def _is_success(payload: dict[str, Any]) -> bool:
    if "showapi_res_code" in payload:
        return str(payload.get("showapi_res_code")) == "0"
    for key in ("code", "Code", "status", "statusCode"):
        if key in payload:
            return str(payload.get(key)).strip().lower() in {"0", "100", "200", "ok", "success"}
    return True


def _pick(body: dict[str, Any], configured_name: str, candidates: tuple[str, ...]) -> str:
    for name in (configured_name, *candidates):
        value = body.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
    return ""


async def _query(client: RegionHttpClient, ip: str) -> tuple[str | None, str]:
    """返回 (region, status)，status 为 ok / unknown / failed。"""
    headers = {"Authorization": f"APPCODE {_secret(settings.region_ip_api_app_code)}"}
    field = settings.region_ip_api_ip_field
    method = settings.region_ip_api_method.upper()
    url = _url()
    if method == "GET":
        response = await client.request("GET", url, headers=headers, params={field: ip})
    elif settings.region_ip_api_request_format == "form":
        response = await client.request("POST", url, headers=headers, data={field: ip})
    else:
        response = await client.request("POST", url, headers=headers, json={field: ip})

    if response.status_code in {401, 403}:
        raise RegionApiNotConfigured("阿里云 IP API 鉴权失败，请检查 AppCode")
    if response.status_code != 200:
        return None, "failed"
    try:
        payload = response.json()
    except Exception:
        return None, "failed"
    if not isinstance(payload, dict) or not _is_success(payload):
        return None, "failed"

    body = _body_root(payload)
    country = _pick(body, settings.region_ip_api_country_field, ("country", "nation", "country_name"))
    province = _pick(body, settings.region_ip_api_province_field, ("province", "region", "prov", "province_name"))
    city = _pick(body, settings.region_ip_api_city_field, ("city", "city_name"))
    region = ip_offline.format_region_parts(country, province, city)
    return (region, "ok") if region else (None, "unknown")


async def resolve_many(values: list[str], *, client: RegionHttpClient | None = None) -> ResolveOutcome:
    if not configured():
        raise RegionApiNotConfigured("阿里云 IP API 未配置（Endpoint/AppCode）")

    cleaned: dict[str, list[str]] = {}
    skipped = 0
    for value in values:
        ip = ip_offline.clean_ip(value)
        if ip is None:
            skipped += 1
            continue
        cleaned.setdefault(ip, []).append(value)
    if not cleaned:
        return ResolveOutcome(skipped=skipped, provider="api")

    own_client = client is None
    if client is None:
        client = RegionHttpClient(
            timeout_seconds=settings.region_ip_api_timeout_seconds,
            concurrency=settings.region_ip_api_concurrency,
            qps=settings.region_ip_api_qps,
            max_retries=settings.region_ip_api_max_retries,
        )

    try:
        results = await asyncio.gather(
            *(_query(client, ip) for ip in cleaned),
            return_exceptions=True,
        )
    finally:
        if own_client:
            await client.close()

    resolved: dict[str, str] = {}
    unknown = 0
    failed = 0
    for ip, result in zip(cleaned, results):
        originals = cleaned[ip]
        if isinstance(result, RegionApiNotConfigured):
            raise result
        if isinstance(result, Exception):
            failed += len(originals)
            logger.warning("region ip api request failed: %s", type(result).__name__)
            continue
        region, status = result
        if status == "ok" and region:
            for original in originals:
                resolved[original] = region
        elif status == "unknown":
            unknown += len(originals)
        else:
            failed += len(originals)

    if failed and not resolved and not unknown:
        raise RegionUpstreamError("阿里云 IP 接口全部失败", code="all_failed")
    return ResolveOutcome(
        resolved=resolved, skipped=skipped, unknown=unknown, failed=failed, provider="api"
    )

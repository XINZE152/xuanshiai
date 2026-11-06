"""阿里云号码百科（Dytnsapi）号码归属查询适配器。

使用 RPC HMAC-SHA1 签名 + AuthCode；手机号可按配置以 NORMAL/MD5/SHA256 入参。
日志与异常不包含手机号原文，只记录失败计数与错误类型。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from app.core.config import settings
from app.services.region import ip_offline, phone_offline
from app.services.region.http_client import RegionHttpClient
from app.services.region.types import (
    RegionApiNotConfigured,
    RegionUpstreamError,
    ResolveOutcome,
)

logger = logging.getLogger(__name__)

_UNKNOWN_CODES = {"PhoneNumberNotfound", "isp.UNKNOWN"}
_AUTH_CODES = {
    "InvalidParameter",
    "AuthCodeIllegal",
    "Forbidden",
    "SignatureDoesNotMatch",
    "isv.INVALID_PARAMETERS",
    "InvalidAccessKeyId.NotFound",
    "SignatureNonceUsed",
}


def _clean(value: str | None) -> str:
    text = (value or "").strip()
    return "" if text.upper().startswith("YOUR_") else text


def _secret(value: Any) -> str:
    return _clean(value.get_secret_value()) if value is not None else ""


def configured() -> bool:
    return bool(
        _clean(settings.region_phone_api_endpoint)
        and _secret(settings.region_phone_api_access_key_id)
        and _secret(settings.region_phone_api_access_key_secret)
        and _secret(settings.region_phone_api_auth_code)
    )


def _endpoint_url() -> str:
    endpoint = _clean(settings.region_phone_api_endpoint)
    if not endpoint:
        raise RegionApiNotConfigured("阿里云号码百科 Endpoint 未配置")
    if not endpoint.startswith(("http://", "https://")):
        endpoint = f"https://{endpoint}"
    return endpoint.rstrip("/") + "/"


def _percent_encode(value: object) -> str:
    return quote(str(value), safe="~").replace("+", "%20").replace("*", "%2A").replace("%7E", "~")


def _canonical_query(params: dict[str, str]) -> str:
    return "&".join(f"{_percent_encode(key)}={_percent_encode(params[key])}" for key in sorted(params))


def _sign(params: dict[str, str], access_key_secret: str) -> str:
    string_to_sign = f"POST&%2F&{_percent_encode(_canonical_query(params))}"
    digest = hmac.new(
        f"{access_key_secret}&".encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def _masked_number(phone: str) -> str:
    mask = settings.region_phone_api_mask.upper()
    if mask == "MD5":
        return hashlib.md5(phone.encode("utf-8")).hexdigest().upper()
    if mask == "SHA256":
        return hashlib.sha256(phone.encode("utf-8")).hexdigest().upper()
    return phone


def _request_params(phone: str) -> dict[str, str]:
    params: dict[str, str] = {
        "Action": settings.region_phone_api_action,
        "Version": settings.region_phone_api_version,
        "Format": "JSON",
        "AccessKeyId": _secret(settings.region_phone_api_access_key_id),
        "SignatureMethod": "HMAC-SHA1",
        "SignatureVersion": "1.0",
        "SignatureNonce": uuid4().hex,
        "Timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "InputNumber": _masked_number(phone),
        "AuthCode": _secret(settings.region_phone_api_auth_code),
        "Mask": settings.region_phone_api_mask.upper(),
    }
    if settings.region_phone_api_region:
        params["RegionId"] = settings.region_phone_api_region
    params["Signature"] = _sign(params, _secret(settings.region_phone_api_access_key_secret))
    return params


async def _query(client: RegionHttpClient, phone: str) -> tuple[str | None, str]:
    response = await client.request("POST", _endpoint_url(), data=_request_params(phone))
    if response.status_code in {401, 403}:
        raise RegionApiNotConfigured("阿里云号码百科鉴权失败，请检查 AccessKey/AuthCode")
    if response.status_code != 200:
        return None, "failed"
    try:
        payload = response.json()
    except Exception:
        return None, "failed"
    if not isinstance(payload, dict):
        return None, "failed"

    code = str(payload.get("Code") or payload.get("code") or "")
    if code == "OK":
        data = payload.get("Data") or payload.get("data") or {}
        if not isinstance(data, dict):
            return None, "unknown"
        province = str(data.get("Province") or data.get("province") or "")
        city = str(data.get("City") or data.get("city") or "")
        region = ip_offline.format_region_parts("", province, city)
        return (region, "ok") if region else (None, "unknown")
    if code in _UNKNOWN_CODES:
        return None, "unknown"
    if code in _AUTH_CODES:
        raise RegionApiNotConfigured(f"阿里云号码百科鉴权失败：{code}")
    return None, "failed"


async def resolve_many(values: list[str], *, client: RegionHttpClient | None = None) -> ResolveOutcome:
    if not configured():
        raise RegionApiNotConfigured("阿里云号码百科未配置（Endpoint/AccessKey/AuthCode）")

    normalized: dict[str, list[str]] = {}
    skipped = 0
    for value in values:
        phone = phone_offline.normalize_phone(value)
        if phone is None:
            skipped += 1
            continue
        normalized.setdefault(phone, []).append(value)
    if not normalized:
        return ResolveOutcome(skipped=skipped, provider="api")

    own_client = client is None
    if client is None:
        client = RegionHttpClient(
            timeout_seconds=settings.region_phone_api_timeout_seconds,
            concurrency=settings.region_phone_api_concurrency,
            qps=settings.region_phone_api_qps,
            max_retries=settings.region_phone_api_max_retries,
        )

    try:
        results = await asyncio.gather(
            *(_query(client, phone) for phone in normalized),
            return_exceptions=True,
        )
    finally:
        if own_client:
            await client.close()

    resolved: dict[str, str] = {}
    unknown = 0
    failed = 0
    for phone, result in zip(normalized, results):
        originals = normalized[phone]
        if isinstance(result, RegionApiNotConfigured):
            raise result
        if isinstance(result, Exception):
            failed += len(originals)
            logger.warning("region phone api request failed: %s", type(result).__name__)
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
        raise RegionUpstreamError("阿里云号码百科接口全部失败", code="all_failed")
    return ResolveOutcome(
        resolved=resolved, skipped=skipped, unknown=unknown, failed=failed, provider="api"
    )

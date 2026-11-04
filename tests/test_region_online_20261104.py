"""阿里云在线属地适配器契约（2026-11-04）。

全部用 httpx.MockTransport，不访问真实 API；覆盖成功、unknown、限流、鉴权失败、
重试、去重与 Mask。
"""

from __future__ import annotations

import hashlib
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import settings
from app.services.region import online_ip, online_phone, registry
from app.services.region.http_client import RegionHttpClient
from app.services.region.types import (
    RegionApiNotConfigured,
    RegionKind,
    RegionUpstreamError,
    ResolveOutcome,
)


@pytest.fixture()
def ip_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "region_ip_provider", "api")
    monkeypatch.setattr(settings, "region_ip_fallback_provider", "none")
    monkeypatch.setattr(settings, "region_ip_api_endpoint", "https://ip.example.com")
    monkeypatch.setattr(settings, "region_ip_api_path", "/ip")
    monkeypatch.setattr(settings, "region_ip_api_method", "GET")
    monkeypatch.setattr(settings, "region_ip_api_request_format", "json")
    monkeypatch.setattr(settings, "region_ip_api_ip_field", "ip")
    monkeypatch.setattr(settings, "region_ip_api_province_field", "province")
    monkeypatch.setattr(settings, "region_ip_api_city_field", "city")
    monkeypatch.setattr(settings, "region_ip_api_country_field", "country")
    monkeypatch.setattr(settings, "region_ip_api_app_code", SecretStr("appcode"))
    monkeypatch.setattr(settings, "region_ip_api_max_retries", 0)
    monkeypatch.setattr(settings, "region_ip_api_qps", 1000)
    monkeypatch.setattr(settings, "region_ip_api_concurrency", 5)


@pytest.fixture()
def phone_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "region_phone_provider", "api")
    monkeypatch.setattr(settings, "region_phone_fallback_provider", "none")
    monkeypatch.setattr(settings, "region_phone_api_endpoint", "https://dytnsapi.aliyuncs.com")
    monkeypatch.setattr(settings, "region_phone_api_action", "DescribePhoneNumberOperatorAttribute")
    monkeypatch.setattr(settings, "region_phone_api_version", "2020-02-17")
    monkeypatch.setattr(settings, "region_phone_api_region", "cn-hangzhou")
    monkeypatch.setattr(settings, "region_phone_api_auth_code", SecretStr("authcode"))
    monkeypatch.setattr(settings, "region_phone_api_mask", "NORMAL")
    monkeypatch.setattr(settings, "region_phone_api_access_key_id", SecretStr("ak"))
    monkeypatch.setattr(settings, "region_phone_api_access_key_secret", SecretStr("secret"))
    monkeypatch.setattr(settings, "region_phone_api_max_retries", 0)
    monkeypatch.setattr(settings, "region_phone_api_qps", 1000)
    monkeypatch.setattr(settings, "region_phone_api_concurrency", 5)


def _client(handler: object, *, max_retries: int = 0) -> RegionHttpClient:
    return RegionHttpClient(
        timeout_seconds=1,
        concurrency=5,
        qps=1000,
        max_retries=max_retries,
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_ip_api_success_dedupes_and_skips_invalid(ip_api: None) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers["Authorization"] == "APPCODE appcode"
        assert request.url.params["ip"] == "112.23.179.205"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {"country": "中国", "province": "江苏省", "city": "南京市", "isp": "电信"},
            },
        )

    client = _client(handler)
    try:
        outcome = await online_ip.resolve_many(
            ["112.23.179.205", "112.23.179.205", "10.0.0.1", "bad-ip"], client=client
        )
    finally:
        await client.close()

    assert outcome.resolved == {"112.23.179.205": "江苏省南京市"}
    assert outcome.skipped == 2
    assert outcome.unknown == 0
    assert outcome.failed == 0
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_ip_api_nested_showapi_body(ip_api: None) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "showapi_res_code": 0,
                "showapi_res_body": {"country": "中国", "region": "云南省", "city": "昆明市"},
            },
        )

    client = _client(handler)
    try:
        outcome = await online_ip.resolve_many(["223.5.5.5"], client=client)
    finally:
        await client.close()

    assert outcome.resolved == {"223.5.5.5": "云南省昆明市"}


@pytest.mark.asyncio
async def test_ip_api_unknown_and_auth_error(ip_api: None) -> None:
    def unknown_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 0, "data": {}})

    client = _client(unknown_handler)
    try:
        outcome = await online_ip.resolve_many(["112.23.179.205"], client=client)
    finally:
        await client.close()
    assert outcome.unknown == 1

    def forbidden_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "forbidden"})

    forbidden = _client(forbidden_handler)
    try:
        with pytest.raises(RegionApiNotConfigured):
            await online_ip.resolve_many(["112.23.179.205"], client=forbidden)
    finally:
        await forbidden.close()


@pytest.mark.asyncio
async def test_ip_api_all_5xx_raises_after_retry(ip_api: None) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, json={"message": "boom"})

    client = _client(handler, max_retries=1)
    try:
        with pytest.raises(RegionUpstreamError):
            await online_ip.resolve_many(["112.23.179.205"], client=client)
    finally:
        await client.close()
    assert calls == 2


@pytest.mark.asyncio
async def test_phone_api_success_and_form_signature(phone_api: None) -> None:
    calls: list[dict[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(parse_qs(request.content.decode()))
        return httpx.Response(
            200,
            json={
                "Code": "OK",
                "Data": {"Province": "浙江省", "City": "杭州市", "Carrier": "中国移动"},
            },
        )

    client = _client(handler)
    try:
        outcome = await online_phone.resolve_many(
            ["13812345678", "+8613812345678", "12912345678"], client=client
        )
    finally:
        await client.close()

    assert outcome.resolved == {
        "13812345678": "浙江省杭州市",
        "+8613812345678": "浙江省杭州市",
    }
    assert outcome.skipped == 1
    assert len(calls) == 1
    form = calls[0]
    assert form["Action"] == ["DescribePhoneNumberOperatorAttribute"]
    assert form["Version"] == ["2020-02-17"]
    assert form["AuthCode"] == ["authcode"]
    assert form["AccessKeyId"] == ["ak"]
    assert form["Mask"] == ["NORMAL"]
    assert form["InputNumber"] == ["13812345678"]
    assert form["Signature"][0]


@pytest.mark.asyncio
async def test_phone_api_sha256_mask(phone_api: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "region_phone_api_mask", "SHA256")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(parse_qs(request.content.decode())["InputNumber"][0])
        return httpx.Response(200, json={"Code": "OK", "Data": {"Province": "北京市", "City": "北京市"}})

    client = _client(handler)
    try:
        await online_phone.resolve_many(["13812345678"], client=client)
    finally:
        await client.close()

    assert seen == [hashlib.sha256("13812345678".encode()).hexdigest().upper()]


@pytest.mark.asyncio
async def test_phone_api_unknown_and_partial_failure(phone_api: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        phone = parse_qs(request.content.decode())["InputNumber"][0]
        if phone == "13812345678":
            return httpx.Response(200, json={"Code": "PhoneNumberNotfound"})
        if phone == "13912345678":
            return httpx.Response(200, json={"Code": "OK", "Data": {"Province": "江苏省", "City": "南京市"}})
        return httpx.Response(200, json={"Code": "RequestFrequencyLimit"})

    client = _client(handler)
    try:
        outcome = await online_phone.resolve_many(
            ["13812345678", "13912345678", "13712345678"], client=client
        )
    finally:
        await client.close()

    assert outcome.unknown == 1
    assert outcome.failed == 1
    assert outcome.resolved == {"13912345678": "江苏省南京市"}


@pytest.mark.asyncio
async def test_phone_api_auth_error(phone_api: None) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"Code": "AuthCodeIllegal"})

    client = _client(handler)
    try:
        with pytest.raises(RegionApiNotConfigured):
            await online_phone.resolve_many(["13812345678"], client=client)
    finally:
        await client.close()


def test_provider_for_maps_legacy_and_per_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "region_ip_provider", "api")
    monkeypatch.setattr(settings, "region_phone_provider", "disabled")
    monkeypatch.setattr(settings, "region_resolver_provider", "offline")
    assert registry.provider_for(RegionKind.IP) == "api"
    assert registry.provider_for(RegionKind.PHONE) == "offline"

    monkeypatch.setattr(settings, "region_ip_provider", "disabled")
    monkeypatch.setattr(settings, "region_phone_provider", "disabled")
    monkeypatch.setattr(settings, "region_resolver_provider", "real")
    assert registry.provider_for(RegionKind.IP) == "api"
    assert registry.provider_for(RegionKind.PHONE) == "api"

    monkeypatch.setattr(settings, "region_resolver_provider", "mock")
    assert registry.provider_for(RegionKind.IP) == "mock"


def test_adapter_implemented_uses_api_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "region_ip_provider", "api")
    monkeypatch.setattr(registry.online_ip, "configured", lambda: True)
    assert registry.adapter_implemented(RegionKind.IP) is True


def test_placeholder_credentials_are_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "region_ip_api_endpoint", "YOUR_ALIYUN_IP_API_ENDPOINT")
    monkeypatch.setattr(settings, "region_ip_api_app_code", SecretStr("YOUR_ALIYUN_IP_APP_CODE"))
    monkeypatch.setattr(settings, "region_phone_api_endpoint", "YOUR_ALIYUN_PHONE_API_ENDPOINT")
    monkeypatch.setattr(settings, "region_phone_api_auth_code", SecretStr("YOUR_ALIYUN_PHONE_AUTH_CODE"))
    monkeypatch.setattr(settings, "region_phone_api_access_key_id", SecretStr("YOUR_ALIYUN_ACCESS_KEY_ID"))
    monkeypatch.setattr(settings, "region_phone_api_access_key_secret", SecretStr("YOUR_ALIYUN_ACCESS_KEY_SECRET"))

    assert online_ip.configured() is False
    assert online_phone.configured() is False


@pytest.mark.asyncio
async def test_registry_api_failure_falls_back_to_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail_api(_values: list[str]) -> ResolveOutcome:
        raise RegionUpstreamError("api down")

    monkeypatch.setattr(settings, "region_ip_provider", "api")
    monkeypatch.setattr(settings, "region_ip_fallback_provider", "offline")
    monkeypatch.setattr(registry.online_ip, "resolve_many", fail_api)
    monkeypatch.setattr(registry, "is_ready", lambda _kind: True)
    monkeypatch.setattr(
        registry.ip_offline,
        "resolve_many",
        lambda _values: ResolveOutcome(resolved={"1.2.3.4": "江苏省南京市"}),
    )

    outcome = await registry.resolve_async(RegionKind.IP, ["1.2.3.4"])
    assert outcome.resolved == {"1.2.3.4": "江苏省南京市"}
    assert outcome.provider == "offline"

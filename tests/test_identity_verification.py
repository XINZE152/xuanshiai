"""实名二要素在线核验 provider 契约。"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.services.identity_verify.aliyun import AliyunCloudAuthIdentityProvider
from app.services.identity_verify.base import IdentityVerifyRequest
from app.services.identity_verify.providers import (
    DisabledIdentityProvider,
    MockIdentityProvider,
    UnimplementedRealIdentityProvider,
    get_identity_provider,
)

_REQUEST = IdentityVerifyRequest(
    user_id=7,
    real_name="张三",
    id_card_number="110101199001011234",
)


def _use_provider(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setattr(settings, "identity_provider", name)


def test_disabled_provider_fails_closed() -> None:
    with pytest.raises(HTTPException) as error:
        asyncio.run(DisabledIdentityProvider().verify(_REQUEST))
    assert error.value.status_code == 503


def test_mock_provider_passes_or_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", "testing")
    monkeypatch.setattr(settings, "identity_mock_pass", True)
    passed = asyncio.run(MockIdentityProvider().verify(_REQUEST))
    assert passed.verified is True
    assert passed.provider == "mock"

    monkeypatch.setattr(settings, "identity_mock_pass", False)
    rejected = asyncio.run(MockIdentityProvider().verify(_REQUEST))
    assert rejected.verified is False
    assert rejected.reason == "mock_identity_rejected"


def test_mock_provider_rejected_outside_test_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", "production")
    with pytest.raises(HTTPException) as error:
        asyncio.run(MockIdentityProvider().verify(_REQUEST))
    assert error.value.status_code == 503


def test_real_provider_is_explicit_placeholder() -> None:
    with pytest.raises(HTTPException) as error:
        asyncio.run(UnimplementedRealIdentityProvider().verify(_REQUEST))
    assert error.value.status_code == 503


@pytest.mark.parametrize(
    ("provider_name", "expected"),
    [
        ("disabled", DisabledIdentityProvider),
        ("mock", MockIdentityProvider),
        ("aliyun", AliyunCloudAuthIdentityProvider),
        ("real", UnimplementedRealIdentityProvider),
    ],
)
def test_factory_maps_configured_provider(
    monkeypatch: pytest.MonkeyPatch,
    provider_name: str,
    expected: type,
) -> None:
    _use_provider(monkeypatch, provider_name)
    assert isinstance(get_identity_provider(), expected)


def test_factory_rejects_unknown_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "identity_provider", "unknown")
    with pytest.raises(HTTPException) as error:
        get_identity_provider()
    assert error.value.status_code == 503

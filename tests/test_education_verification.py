"""学历在线核验 provider 契约。"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.services.education.base import EducationVerifyRequest
from app.services.education.providers import (
    DisabledEducationProvider,
    MockEducationProvider,
    UnimplementedAliyunMarketEducationProvider,
    UnimplementedRealEducationProvider,
    get_education_provider,
)

_REQUEST = EducationVerifyRequest(
    user_id=7,
    education="本科",
    school="南京大学",
    image_ref="/storage/uploads/7/education-cert-a.webp",
    real_name="张三",
)


def test_disabled_provider_fails_closed() -> None:
    with pytest.raises(HTTPException) as error:
        asyncio.run(DisabledEducationProvider().verify(_REQUEST))
    assert error.value.status_code == 503


def test_mock_provider_passes_or_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", "testing")
    monkeypatch.setattr(settings, "education_mock_pass", True)
    passed = asyncio.run(MockEducationProvider().verify(_REQUEST))
    assert passed.verified is True
    assert passed.provider == "mock"

    monkeypatch.setattr(settings, "education_mock_pass", False)
    rejected = asyncio.run(MockEducationProvider().verify(_REQUEST))
    assert rejected.verified is False
    assert rejected.reason == "mock_education_rejected"


def test_mock_provider_rejected_outside_test_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", "production")
    with pytest.raises(HTTPException) as error:
        asyncio.run(MockEducationProvider().verify(_REQUEST))
    assert error.value.status_code == 503


def test_real_provider_is_explicit_placeholder() -> None:
    with pytest.raises(HTTPException) as error:
        asyncio.run(UnimplementedRealEducationProvider().verify(_REQUEST))
    assert error.value.status_code == 503


@pytest.mark.parametrize(
    ("provider_name", "expected"),
    [
        ("disabled", DisabledEducationProvider),
        ("mock", MockEducationProvider),
        ("aliyun_market", UnimplementedAliyunMarketEducationProvider),
        ("real", UnimplementedRealEducationProvider),
    ],
)
def test_factory_maps_configured_provider(
    monkeypatch: pytest.MonkeyPatch,
    provider_name: str,
    expected: type,
) -> None:
    monkeypatch.setattr(settings, "education_provider", provider_name)
    assert isinstance(get_education_provider(), expected)


def test_factory_rejects_unknown_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "education_provider", "unknown")
    with pytest.raises(HTTPException) as error:
        get_education_provider()
    assert error.value.status_code == 503

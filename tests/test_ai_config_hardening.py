"""Production AI configuration must fail closed before any provider call."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def _production_ai_settings(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "_env_file": None,
        "environment": "production",
        "auto_init_db": False,
        "live_provider": "tencent",
        "sms_provider": "disabled",
        "wechat_provider": "wechat",
        "wechat_payment_mode": "real",
        "ai_master_enabled": True,
        "ai_policy_approved": True,
        "ai_provider_approved": True,
        "ai_retention_policy_version": "retention-v1",
        "ai_provider": "deepseek",
        "ai_deepseek_api_key": "test-only-provider-key",
        "secret_key": "x" * 32,
        "debug": False,
        "docs_enabled": False,
    }
    values.update(overrides)
    return values


def test_production_ai_rejects_placeholder_secret_key() -> None:
    values = _production_ai_settings(secret_key="change-me-in-local-env")

    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(**values)


def test_production_ai_rejects_debug_or_docs() -> None:
    values = _production_ai_settings(debug=True)

    with pytest.raises(ValidationError, match="DEBUG"):
        Settings(**values)


def test_production_ai_requires_real_provider_credentials() -> None:
    values = _production_ai_settings(ai_deepseek_api_key=None)

    with pytest.raises(ValidationError, match="AI_DEEPSEEK_API_KEY"):
        Settings(**values)


def test_production_ai_accepts_complete_non_mock_configuration() -> None:
    configured = Settings(**_production_ai_settings())

    assert configured.environment == "production"
    assert configured.ai_provider == "deepseek"
    assert configured.ai_master_enabled is True
    assert configured.debug is False
    assert configured.docs_enabled is False

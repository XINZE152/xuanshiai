"""实时语音 v2 生产门禁测试。

按实际启用的能力校验对应凭据（方案 §5）：
- 实时分支启用必须配置 senseaudio provider + api_key；
- 实时分支不借用阿里云 AccessKey 门禁；
- 旧实时对话（阿里云 NLS）启用时仍要求原凭据。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def _production_base() -> dict[str, object]:
    return {
        "_env_file": None,
        "environment": "production",
        "auto_init_db": False,
        "debug": False,
        "docs_enabled": False,
        "secret_key": "t" * 32,
        "sms_provider": "disabled",
        "wechat_provider": "wechat",
        "wechat_payment_mode": "real",
        # 生产环境禁止直播 Mock Provider（config.validate_test_providers）；
        # 本文件的断言关注 AI 语音门禁，直播 provider 用非 mock 值让基座先通过。
        "live_provider": "tencent",
        "ai_policy_approved": True,
        "ai_provider_approved": True,
        "ai_retention_policy_version": "ai-policy-2026-08-07-v1",
        "ai_provider": "deepseek",
        "ai_deepseek_api_key": "test-deepseek-key",
    }


def test_realtime_enabled_without_provider_fails_closed() -> None:
    with pytest.raises(ValidationError, match="ai_realtime_voice_provider"):
        Settings(
            **_production_base(),  # type: ignore[arg-type]
            ai_realtime_voice_enabled=True,
            ai_realtime_voice_provider="",
        )


def test_realtime_enabled_without_api_key_fails_closed() -> None:
    with pytest.raises(ValidationError, match="SenseAudio api_key"):
        Settings(
            **_production_base(),  # type: ignore[arg-type]
            ai_realtime_voice_enabled=True,
            ai_realtime_voice_provider="senseaudio",
        )


def test_realtime_enabled_does_not_require_aliyun_credentials() -> None:
    """实时分支凭据独立：未启用旧 STT/TTS 时不检查阿里云 AccessKey。"""
    settings = Settings(
        **_production_base(),  # type: ignore[arg-type]
        ai_realtime_voice_enabled=True,
        ai_realtime_voice_provider="senseaudio",
        ai_senseaudio_api_key="sk-test",  # type: ignore[arg-type]
    )
    assert settings.ai_voice_conversation_enabled is False


def test_realtime_enabled_still_requires_approvals() -> None:
    base = _production_base()
    base["ai_policy_approved"] = False
    with pytest.raises(ValidationError, match="ai_policy_approved"):
        Settings(
            **base,  # type: ignore[arg-type]
            ai_realtime_voice_enabled=True,
            ai_realtime_voice_provider="senseaudio",
            ai_senseaudio_api_key="sk-test",  # type: ignore[arg-type]
        )


def test_legacy_realtime_conversation_still_requires_aliyun_keys() -> None:
    with pytest.raises(ValidationError, match="AccessKey ID/Secret"):
        Settings(
            **_production_base(),  # type: ignore[arg-type]
            ai_voice_conversation_enabled=True,
        )

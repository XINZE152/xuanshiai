"""微信支付 real 模式配置门禁。

这些用例只构造 ``Settings``，不连接数据库或微信；目标是防止以下真实事故：
AppID 不一致、AppSecret 缺失、退款回调缺失、HTTP 回调、公钥 ID 格式错误、
real 模式仍开启 AUTO_INIT_DB、以及 development 环境误开真实收款。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings

_PAYMENT_ENV_KEYS = (
    "ENVIRONMENT",
    "AUTO_INIT_DB",
    "ALLOW_REAL_PAY_IN_DEV",
    "WECHAT_APP_ID",
    "WECHAT_APP_SECRET",
    "WECHAT_PAY_MODE",
    "WECHAT_PAY_MCH_ID",
    "WECHAT_PAY_APP_ID",
    "WECHAT_PAY_SERIAL_NO",
    "WECHAT_PAY_PRIVATE_KEY",
    "WECHAT_PAY_PRIVATE_KEY_PATH",
    "WECHAT_PAY_API_V3_KEY",
    "WECHAT_PAY_NOTIFY_URL",
    "WECHAT_PAY_REFUND_NOTIFY_URL",
    "WECHAT_PAY_PUBLIC_KEY_ID",
    "WECHAT_PAY_PUBLIC_KEY",
    "WECHAT_PAY_PUBLIC_KEY_PATH",
)


@pytest.fixture(autouse=True)
def _clear_payment_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """避免本机 shell 里导出的 WECHAT_* 变量覆盖用例参数。"""
    for key in _PAYMENT_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _real_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "development",
        "auto_init_db": False,
        "allow_real_pay_in_dev": True,
        "wechat_pay_mode": "real",
        "wechat_pay_mch_id": "1111571577",
        "wechat_pay_app_id": "wx0000000000000000",
        "wechat_app_id": "wx0000000000000000",
        "wechat_app_secret": "test-app-secret",
        "wechat_pay_serial_no": "455499E1B986BC88DAE636F196AA468DE035649D",
        "wechat_pay_private_key": "-----BEGIN PRIVATE KEY-----\ndummy\n-----END PRIVATE KEY-----",
        "wechat_pay_api_v3_key": "x" * 32,
        "wechat_pay_notify_url": "https://xhztest.xyz/api/v1/payments/wechat/notify",
        "wechat_pay_refund_notify_url": (
            "https://xhztest.xyz/api/v1/payments/wechat/refund-notify"
        ),
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_real_accepts_complete_configuration() -> None:
    settings = _real_settings()
    assert settings.wechat_pay_mode == "real"
    assert settings.wechat_app_id == settings.wechat_pay_app_id


def test_real_rejects_app_id_mismatch() -> None:
    with pytest.raises(ValidationError, match="必须一致"):
        _real_settings(wechat_app_id="wx1111111111111111")


def test_real_requires_app_secret() -> None:
    with pytest.raises(ValidationError, match="WECHAT_APP_SECRET"):
        _real_settings(wechat_app_secret=None)


def test_real_requires_refund_notify_url() -> None:
    with pytest.raises(ValidationError, match="WECHAT_PAY_REFUND_NOTIFY_URL"):
        _real_settings(wechat_pay_refund_notify_url=None)


def test_real_requires_https_notify_url() -> None:
    with pytest.raises(ValidationError, match="HTTPS"):
        _real_settings(wechat_pay_notify_url="http://xhztest.xyz/notify")


def test_real_requires_https_refund_notify_url() -> None:
    with pytest.raises(ValidationError, match="HTTPS"):
        _real_settings(wechat_pay_refund_notify_url="http://xhztest.xyz/refund-notify")


def test_real_requires_public_key_id_prefix() -> None:
    with pytest.raises(ValidationError, match="PUB_KEY_ID_"):
        _real_settings(
            wechat_pay_public_key_id="PUBLIC_KEY_2607",
            wechat_pay_public_key="-----BEGIN PUBLIC KEY-----\ndummy\n-----END PUBLIC KEY-----",
        )


def test_real_rejects_auto_init_db() -> None:
    with pytest.raises(ValidationError, match="AUTO_INIT_DB"):
        _real_settings(auto_init_db=True)


def test_real_requires_dev_flag_in_development() -> None:
    with pytest.raises(ValidationError, match="ALLOW_REAL_PAY_IN_DEV"):
        _real_settings(allow_real_pay_in_dev=False)


def test_real_requires_dev_flag_in_testing() -> None:
    with pytest.raises(ValidationError, match="ALLOW_REAL_PAY_IN_DEV"):
        _real_settings(environment="testing", allow_real_pay_in_dev=False)


def test_disabled_mode_skips_real_payment_checks() -> None:
    settings = Settings(
        _env_file=None,
        environment="development",
        auto_init_db=True,
        wechat_pay_mode="disabled",
        wechat_app_id="wx1111111111111111",
        wechat_pay_app_id="wx0000000000000000",
    )
    assert settings.wechat_pay_mode == "disabled"


def _production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "production",
        "live_provider": "tencent",
        "wechat_payment_mode": "real",
    }
    values.update(overrides)
    return _real_settings(**values)


def test_production_rejects_face_mock() -> None:
    with pytest.raises(ValidationError, match="人脸核验"):
        _production_settings(face_provider="mock")


def test_production_rejects_identity_mock() -> None:
    with pytest.raises(ValidationError, match="实名核验"):
        _production_settings(identity_provider="mock")


def test_production_rejects_education_mock() -> None:
    with pytest.raises(ValidationError, match="学历核验"):
        _production_settings(education_provider="mock")


def test_aliyun_identity_requires_access_keys() -> None:
    with pytest.raises(ValidationError, match="IDENTITY_ACCESS_KEY"):
        Settings(
            _env_file=None,
            environment="development",
            identity_provider="aliyun",
        )


def test_aliyun_identity_rejects_empty_secret() -> None:
    with pytest.raises(ValidationError, match="IDENTITY_ACCESS_KEY"):
        Settings(
            _env_file=None,
            environment="development",
            identity_provider="aliyun",
            identity_access_key_id="test-access-key",
            identity_access_key_secret="",
        )


def test_aliyun_identity_accepts_complete_credentials() -> None:
    settings = Settings(
        _env_file=None,
        environment="development",
        identity_provider="aliyun",
        identity_access_key_id="test-access-key",
        identity_access_key_secret="test-access-secret",
    )
    assert settings.identity_provider == "aliyun"


def test_aliyun_face_requires_scene_id() -> None:
    with pytest.raises(ValidationError, match="FACE_SCENE_ID"):
        Settings(
            _env_file=None,
            environment="development",
            face_provider="aliyun",
            face_access_key_id="test-access-key",
            face_access_key_secret="test-access-secret",
        )


def test_aliyun_market_education_requires_market_config() -> None:
    with pytest.raises(ValidationError, match="EDUCATION_MARKET"):
        Settings(
            _env_file=None,
            environment="development",
            education_provider="aliyun_market",
        )

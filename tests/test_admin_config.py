from app.services.admin_config import DEFAULT_CONFIGS, _contains_plain_sensitive


def test_default_config_namespaces_are_unique_and_valid():
    assert len(DEFAULT_CONFIGS) == len(set(DEFAULT_CONFIGS))
    assert "platform_basic" in DEFAULT_CONFIGS
    assert "platform_permissions" in DEFAULT_CONFIGS
    assert "wechat" in DEFAULT_CONFIGS


def test_sensitive_config_keys_are_declared():
    assert "app_secret" in DEFAULT_CONFIGS["wechat"][3]
    assert "app_secret" in DEFAULT_CONFIGS["miniprogram"][3]
    assert "merchant_key" in DEFAULT_CONFIGS["finance"][3]


def test_plain_sensitive_values_are_rejected_but_mask_is_allowed():
    assert _contains_plain_sensitive({"app_secret": "real-secret"}, {"app_secret"})
    assert not _contains_plain_sensitive({"app_secret": "******"}, {"app_secret"})

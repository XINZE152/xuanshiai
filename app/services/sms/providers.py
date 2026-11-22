"""短信服务实现和工厂。"""

from fastapi import HTTPException

from app.core.config import settings
from app.services.sms.base import SmsProvider
from app.services.sms.tencent_cloud import TencentCloudSmsProvider


class DisabledSmsProvider:
    """未配置短信服务时的实现。"""

    name = "disabled"

    async def send_code(self, phone: str, purpose: str, code: str) -> None:
        raise HTTPException(status_code=503, detail="短信服务未配置")

    async def send_template(
        self, phone: str, template_code: str, params: dict[str, str]
    ) -> str | None:
        raise HTTPException(status_code=503, detail="短信服务未配置")


class MockSmsProvider:
    """仅用于 development/testing 的短信实现，不调用外部服务。"""

    name = "mock"

    async def send_code(self, phone: str, purpose: str, code: str) -> None:
        if not settings.is_test_mode:
            raise HTTPException(status_code=503, detail="短信 Mock 服务仅允许测试环境使用")
        if code != settings.sms_mock_code:
            raise HTTPException(status_code=500, detail="短信 Mock 配置异常")

    async def send_template(
        self, phone: str, template_code: str, params: dict[str, str]
    ) -> str | None:
        if not settings.is_test_mode:
            raise HTTPException(status_code=503, detail="短信 Mock 服务仅允许测试环境使用")
        if not phone or not template_code:
            raise HTTPException(status_code=422, detail="手机号或模板编码为空")
        return f"mock-{phone[-4:]}-{template_code}"


def ensure_sms_template_configured() -> None:
    """发送前的显式可用性检查：未配置/未实现时直接 503。

    与「逐条发送时才发现不可用」相比，这里在写任何 ``admin_sms_message`` 之前
    就失败，避免依赖回滚清理半批 ``queued`` 记录。
    """
    provider = get_sms_template_provider()
    if provider.name == "disabled":
        raise HTTPException(status_code=503, detail="短信服务未配置")


def get_sms_template_provider() -> SmsProvider:
    """返回后台模板短信供应商（A1）。

    独立于登录验证码的 ``sms_provider``：验证码通道与运营群发通道可能来自不同
    供应商、不同签名与额度，混用会让「短信管理」页的发送记录无法对账。
    未配置（``disabled``）时由调用方 fail-closed（503）。
    """
    providers: dict[str, SmsProvider] = {
        "disabled": DisabledSmsProvider(),
        "mock": MockSmsProvider(),
        "tencent_cloud": TencentCloudSmsProvider(),
    }
    configured = settings.sms_template_provider.lower()
    provider = providers.get("tencent_cloud" if configured == "disabled" else configured)
    if provider is None:
        raise HTTPException(status_code=503, detail="短信服务供应商未实现")
    return provider


def get_sms_provider() -> SmsProvider:
    """根据配置返回短信供应商。"""
    providers: dict[str, SmsProvider] = {
        "disabled": DisabledSmsProvider(),
        "mock": MockSmsProvider(),
        "tencent_cloud": TencentCloudSmsProvider(),
    }
    configured = settings.sms_provider.lower()
    provider = providers.get("tencent_cloud" if configured == "disabled" else configured)
    if provider is None:
        raise HTTPException(status_code=503, detail="短信服务供应商未实现")
    return provider

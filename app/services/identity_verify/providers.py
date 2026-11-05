"""实名二要素核验供应商实现与工厂。

aliyun 使用官方 CloudAuth Id2MetaVerify；real 是其他厂商占位，必须等厂商 API
文档、签名方式和数据授权确认后实现，不允许用猜测的接口协议上线。
"""

from __future__ import annotations

from fastapi import HTTPException

from app.core.config import settings
from app.services.identity_verify.aliyun import AliyunCloudAuthIdentityProvider
from app.services.identity_verify.base import IdentityProvider, IdentityVerifyRequest, IdentityVerifyResult


class DisabledIdentityProvider:
    """未接入在线核验：调用方转人工审核。"""

    name = "disabled"

    async def verify(self, request: IdentityVerifyRequest) -> IdentityVerifyResult:
        raise HTTPException(status_code=503, detail="实名核验服务未配置")


class MockIdentityProvider:
    """仅 development/testing：按 ``IDENTITY_MOCK_PASS`` 返回固定结果。"""

    name = "mock"

    async def verify(self, request: IdentityVerifyRequest) -> IdentityVerifyResult:
        if not settings.is_test_mode:
            raise HTTPException(status_code=503, detail="实名核验 Mock 服务仅允许测试环境使用")
        passed = bool(settings.identity_mock_pass)
        return IdentityVerifyResult(
            verified=passed,
            provider="mock",
            reason=None if passed else "mock_identity_rejected",
        )


class UnimplementedRealIdentityProvider:
    """真实厂商适配器占位：未接入前保持 fail-closed，由调用方转人工审核。"""

    name = "real"

    async def verify(self, request: IdentityVerifyRequest) -> IdentityVerifyResult:
        raise HTTPException(status_code=503, detail="实名核验在线适配器待接入")


def get_identity_provider() -> IdentityProvider:
    """按 ``IDENTITY_PROVIDER`` 返回实现；未知值直接 503。"""
    providers: dict[str, IdentityProvider] = {
        "disabled": DisabledIdentityProvider(),
        "mock": MockIdentityProvider(),
        "aliyun": AliyunCloudAuthIdentityProvider(),
        "real": UnimplementedRealIdentityProvider(),
    }
    provider = providers.get((settings.identity_provider or "disabled").lower())
    if provider is None:
        raise HTTPException(status_code=503, detail="实名核验供应商未实现")
    return provider

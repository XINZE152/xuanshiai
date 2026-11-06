"""学历核验供应商实现与工厂。

aliyun_market 走阿里云云市场第三方学历接口，具体请求/响应字段依赖服务商；
服务商确认前保持显式占位，不用猜测协议上线。
"""

from __future__ import annotations

from fastapi import HTTPException

from app.core.config import settings
from app.services.education.base import EducationProvider, EducationVerifyRequest, EducationVerifyResult


class DisabledEducationProvider:
    """未接入在线核验：调用方保持人工审核。"""

    name = "disabled"

    async def verify(self, request: EducationVerifyRequest) -> EducationVerifyResult:
        raise HTTPException(status_code=503, detail="学历核验服务未配置")


class MockEducationProvider:
    """仅 development/testing：按 ``EDUCATION_MOCK_PASS`` 返回固定结果。"""

    name = "mock"

    async def verify(self, request: EducationVerifyRequest) -> EducationVerifyResult:
        if not settings.is_test_mode:
            raise HTTPException(status_code=503, detail="学历核验 Mock 服务仅允许测试环境使用")
        passed = bool(settings.education_mock_pass)
        return EducationVerifyResult(
            verified=passed,
            provider="mock",
            reason=None if passed else "mock_education_rejected",
        )


class UnimplementedAliyunMarketEducationProvider:
    """阿里云云市场第三方学历接口占位；服务商确认后替换为具体适配器。"""

    name = "aliyun_market"

    async def verify(self, request: EducationVerifyRequest) -> EducationVerifyResult:
        raise HTTPException(status_code=503, detail="阿里云云市场学历核验服务商待确认")


class UnimplementedRealEducationProvider:
    """真实厂商适配器占位：未接入前保持 fail-closed，由调用方转人工审核。"""

    name = "real"

    async def verify(self, request: EducationVerifyRequest) -> EducationVerifyResult:
        raise HTTPException(status_code=503, detail="学历核验在线适配器待接入")


def get_education_provider() -> EducationProvider:
    """按 ``EDUCATION_PROVIDER`` 返回实现；未知值直接 503。"""
    providers: dict[str, EducationProvider] = {
        "disabled": DisabledEducationProvider(),
        "mock": MockEducationProvider(),
        "aliyun_market": UnimplementedAliyunMarketEducationProvider(),
        "real": UnimplementedRealEducationProvider(),
    }
    provider = providers.get((settings.education_provider or "disabled").lower())
    if provider is None:
        raise HTTPException(status_code=503, detail="学历核验供应商未实现")
    return provider

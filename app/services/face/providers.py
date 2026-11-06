"""人脸认证实现与工厂（D-14）。"""

from __future__ import annotations

from fastapi import HTTPException

from app.core.config import settings
from app.services.face.aliyun import AliyunCloudAuthFaceProvider
from app.services.face.base import FaceProvider, FaceVerifyResult


class DisabledFaceProvider:
    """未接入人脸服务时的实现：fail-closed，拒绝一切核验请求。"""

    name = "disabled"

    async def verify(
        self,
        *,
        user_id: int,
        image_ref: str,
        scene: str = "realname",
        real_name: str | None = None,
        id_card_number: str | None = None,
    ) -> FaceVerifyResult:
        raise HTTPException(status_code=503, detail="人脸认证服务未配置")


class MockFaceProvider:
    """仅用于 development/testing 的人脸实现，不调用外部服务。

    返回结果由 ``FACE_MOCK_PASS`` 决定，便于测试覆盖通过与拒绝两条分支。
    """

    name = "mock"

    async def verify(
        self,
        *,
        user_id: int,
        image_ref: str,
        scene: str = "realname",
        real_name: str | None = None,
        id_card_number: str | None = None,
    ) -> FaceVerifyResult:
        if not settings.is_test_mode:
            raise HTTPException(status_code=503, detail="人脸 Mock 服务仅允许测试环境使用")
        passed = bool(settings.face_mock_pass)
        return FaceVerifyResult(
            verified=passed,
            score=98.0 if passed else 20.0,
            provider="mock",
            raw={"scene": scene, "user_id": user_id},
        )


def get_face_provider() -> FaceProvider:
    """根据配置返回人脸认证供应商。"""
    providers: dict[str, FaceProvider] = {
        "disabled": DisabledFaceProvider(),
        "mock": MockFaceProvider(),
        "aliyun": AliyunCloudAuthFaceProvider(),
    }
    provider = providers.get((settings.face_provider or "disabled").lower())
    if provider is None:
        raise HTTPException(status_code=503, detail="人脸认证供应商未实现")
    return provider

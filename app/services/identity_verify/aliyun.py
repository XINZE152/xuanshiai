"""阿里云金融级实人认证：身份二要素核验（Id2MetaVerify）。"""

from __future__ import annotations

from fastapi import HTTPException

from app.core.config import settings
from app.services.aliyun_cloudauth.client import (
    AliyunCloudAuthClient,
    AliyunCloudAuthError,
    result_object,
)
from app.services.identity_verify.base import (
    IdentityProvider,
    IdentityVerifyRequest,
    IdentityVerifyResult,
)

_BUSINESS_REASONS = {
    "2": "identity_mismatch",
    "3": "identity_not_found",
}


class AliyunCloudAuthIdentityProvider(IdentityProvider):
    """身份二要素：姓名 + 身份证号，BizCode=1 表示一致。"""

    name = "aliyun"

    def __init__(self, client: AliyunCloudAuthClient | None = None) -> None:
        self._client = client

    def _get_client(self) -> AliyunCloudAuthClient:
        if self._client is not None:
            return self._client
        secret = settings.identity_access_key_secret
        if not (settings.identity_access_key_id or "").strip() or secret is None:
            raise HTTPException(status_code=503, detail="阿里云实名核验未配置 AccessKey")
        self._client = AliyunCloudAuthClient(
            access_key_id=settings.identity_access_key_id,
            access_key_secret=secret.get_secret_value(),
            endpoint=settings.identity_endpoint,
            timeout_seconds=settings.identity_timeout_seconds,
        )
        return self._client

    async def verify(self, request: IdentityVerifyRequest) -> IdentityVerifyResult:
        try:
            data = await self._get_client().call(
                "Id2MetaVerify",
                {
                    "ParamType": "normal",
                    "IdentifyNum": request.id_card_number,
                    "UserName": request.real_name,
                },
            )
        except HTTPException:
            raise
        except AliyunCloudAuthError as exc:
            raise HTTPException(status_code=503, detail="阿里云实名核验暂时不可用") from exc

        result = result_object(data)
        biz_code = str(result.get("BizCode") or "")
        verified = biz_code == "1"
        return IdentityVerifyResult(
            verified=verified,
            provider="aliyun_cloudauth",
            reason=None if verified else _BUSINESS_REASONS.get(biz_code, "identity_unknown"),
            raw={
                "request_id": data.get("RequestId"),
                "biz_code": biz_code,
            },
        )

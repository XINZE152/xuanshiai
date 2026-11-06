"""阿里云金融级实人认证：照片实人认证（ContrastFaceVerify）。"""

from __future__ import annotations

import base64
import uuid
from pathlib import Path

from fastapi import HTTPException

from app.core.config import settings
from app.services.aliyun_cloudauth.client import (
    AliyunCloudAuthClient,
    AliyunCloudAuthError,
    result_object,
)
from app.services.face.base import FaceProvider, FaceVerifyResult

_STORAGE_PREFIX = "/storage/uploads/"
_MAX_FACE_IMAGE_BYTES = 1_000_000


class AliyunCloudAuthFaceProvider(FaceProvider):
    """姓名 + 身份证号 + 本人照片，调用阿里云照片实人认证。"""

    name = "aliyun"

    def __init__(
        self,
        client: AliyunCloudAuthClient | None = None,
        *,
        scene_id: int | None = None,
    ) -> None:
        self._client = client
        self._scene_id = scene_id

    def _get_client(self) -> AliyunCloudAuthClient:
        if self._client is not None:
            return self._client
        secret = settings.face_access_key_secret
        if not (settings.face_access_key_id or "").strip() or secret is None:
            raise HTTPException(status_code=503, detail="阿里云人脸核验未配置 AccessKey")
        self._client = AliyunCloudAuthClient(
            access_key_id=settings.face_access_key_id,
            access_key_secret=secret.get_secret_value(),
            endpoint=settings.face_endpoint,
            timeout_seconds=settings.face_timeout_seconds,
        )
        return self._client

    def _read_face_image(self, image_ref: str) -> str:
        if not image_ref.startswith(_STORAGE_PREFIX):
            raise HTTPException(status_code=422, detail="仅支持平台内上传的头像文件")
        relative = image_ref[len(_STORAGE_PREFIX):].split("?", 1)[0]
        root = Path(settings.upload_dir).resolve()
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise HTTPException(status_code=422, detail="头像路径不合法")
        if not path.is_file():
            raise HTTPException(status_code=422, detail="头像文件不存在")
        if path.stat().st_size > _MAX_FACE_IMAGE_BYTES:
            raise HTTPException(status_code=422, detail="头像文件过大，无法进行人脸核验")
        return base64.b64encode(path.read_bytes()).decode("ascii")

    async def verify(
        self,
        *,
        user_id: int,
        image_ref: str,
        scene: str = "realname",
        real_name: str | None = None,
        id_card_number: str | None = None,
    ) -> FaceVerifyResult:
        if not real_name or not id_card_number:
            raise HTTPException(status_code=503, detail="请先完成实名认证后再进行人脸核验")
        scene_id = self._scene_id or settings.face_scene_id
        if not scene_id:
            raise HTTPException(status_code=503, detail="阿里云人脸核验未配置场景 ID")

        picture = self._read_face_image(image_ref)
        try:
            data = await self._get_client().call(
                "ContrastFaceVerify",
                {
                    "SceneId": str(scene_id),
                    "OuterOrderNo": uuid.uuid4().hex[:32],
                    "UserId": str(user_id),
                    "CertName": real_name,
                    "CertNo": id_card_number,
                    "FaceContrastPicture": picture,
                },
            )
        except HTTPException:
            raise
        except AliyunCloudAuthError as exc:
            raise HTTPException(status_code=503, detail="阿里云人脸核验暂时不可用") from exc

        result = result_object(data)
        passed = str(result.get("Passed") or "").upper() == "T"
        score: float | None = None
        try:
            if result.get("VerifyScore") is not None:
                score = float(result["VerifyScore"])
        except (TypeError, ValueError):
            score = None
        return FaceVerifyResult(
            verified=passed,
            score=score,
            provider="aliyun_cloudauth",
            raw={
                "request_id": data.get("RequestId"),
                "passed": result.get("Passed"),
                "sub_code": result.get("SubCode"),
            },
        )

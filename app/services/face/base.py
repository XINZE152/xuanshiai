"""人脸认证统一接口（D-14）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class FaceVerifyResult:
    """人脸核验结果。

    ``verified`` 为 True 时才允许写 ``user_auth.face_verified = 1``；
    ``score`` 为供应商返回的相似度（0~100，可为空）。
    """

    verified: bool
    score: float | None = None
    provider: str = "unknown"
    raw: dict[str, Any] = field(default_factory=dict)


class FaceProvider(Protocol):
    """人脸认证供应商必须实现的接口。"""

    name: str

    async def verify(
        self,
        *,
        user_id: int,
        image_ref: str,
        scene: str = "realname",
        real_name: str | None = None,
        id_card_number: str | None = None,
    ) -> FaceVerifyResult:
        """比对用户实名照与活体照；失败时抛出异常。"""

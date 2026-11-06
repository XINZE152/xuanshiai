"""实名二要素核验统一接口。

供应商实现只返回 ``verified`` 与安全原因码；原始响应仅供调用方做短期审计，
不得写入日志或持久化明文。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class IdentityVerifyRequest:
    """一次实名二要素核验请求。``id_card_number`` 为敏感信息，禁止记录日志。"""

    user_id: int
    real_name: str
    id_card_number: str


@dataclass(frozen=True, slots=True)
class IdentityVerifyResult:
    """核验结果。``reason`` 必须是可安全展示的短原因码/文案，不得包含证件原文。"""

    verified: bool
    provider: str
    reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class IdentityProvider(Protocol):
    """实名核验供应商必须实现的接口。"""

    name: str

    async def verify(self, request: IdentityVerifyRequest) -> IdentityVerifyResult:
        """执行核验；供应商不可用时抛出 ``HTTPException(503)``。"""

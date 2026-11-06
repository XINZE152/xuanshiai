"""学历认证核验统一接口。

学历数据通常来自学信网或授权数据服务；供应商适配器必须自行保证数据源合法性，
本接口只接收已脱敏的必要字段和材料引用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class EducationVerifyRequest:
    """一次学历核验请求。``image_ref`` 是平台内部材料路径，不得直接下发给外部。"""

    user_id: int
    education: str
    school: str
    image_ref: str
    real_name: str | None = None


@dataclass(frozen=True, slots=True)
class EducationVerifyResult:
    """核验结果。``reason`` 必须是可安全展示的短原因码/文案。"""

    verified: bool
    provider: str
    reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class EducationProvider(Protocol):
    """学历核验供应商必须实现的接口。"""

    name: str

    async def verify(self, request: EducationVerifyRequest) -> EducationVerifyResult:
        """执行核验；供应商不可用时抛出 ``HTTPException(503)``。"""

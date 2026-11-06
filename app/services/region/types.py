"""属地解析的公共类型与异常。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class RegionKind(StrEnum):
    """属地类型。值同时用作敏感操作命名与目标列选择。"""

    IP = "ip"
    PHONE = "phone"

    @property
    def column(self) -> str:
        """``users`` 表的展示列名（内部固定，不接受外部输入拼接）。"""
        return "ip_region" if self is RegionKind.IP else "phone_region"

    @property
    def source_column(self) -> str:
        """解析源列（内部固定）。"""
        return "register_ip" if self is RegionKind.IP else "phone"


class RegionDataNotReady(RuntimeError):
    """解析数据文件或查询绑定缺失/不可读。

    这是"能力未就绪"而非"请求错误"，路由层统一转 503，且**不写任何数据**。
    """


class RegionApiNotConfigured(RegionDataNotReady):
    """在线 provider 已启用但 Endpoint/Key/AuthCode 等配置不完整。"""


class RegionUpstreamError(RuntimeError):
    """第三方 API 超时、限流、5xx 或响应不可解析；路由层转 502。"""

    def __init__(self, message: str, *, code: str = "upstream_error") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ResolveOutcome:
    """一次批量解析的结果。

    - ``resolved``：源值 → 属地展示串（如「江苏省南京市」）。
    - ``skipped``：源值不可解析（空、非法、回环/内网、IPv6 未启用等），未送解析。
    - ``unknown``：送进解析但查不到（号段未收录、IP 库无记录），按约定**不写库**。
    - ``failed``：调用第三方 API 失败（超时、429、5xx、响应格式错误），不写库。
    """

    resolved: dict[str, str] = field(default_factory=dict)
    skipped: int = 0
    unknown: int = 0
    failed: int = 0
    provider: str = ""

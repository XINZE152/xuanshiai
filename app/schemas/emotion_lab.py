"""情感实验室（MBTI）请求/响应模型。

E1.5 只覆盖「把端侧测出的结果同步进资料」这一步，因此这里只有 profile-source 的
出入参；会话/答题/合拍（E2）等到对应接口落地时再加。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.auth import MbtiType


class EmotionLabProfileSourceRequest(BaseModel):
    """同步 MBTI 到资料。

    前端 ``api/emotionLab.uts`` 已按 camelCase（``mbtiType`` / ``resultId``）定稿，
    这里用别名同时接受 camelCase 与 snake_case，避免为了命名再改一次客户端。
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    mbti_type: MbtiType = Field(alias="mbtiType", description="16 型之一，大小写敏感")
    source: Literal["assessment", "self_reported"] = Field(
        default="self_reported",
        description="assessment=测评结果；self_reported=用户手选",
    )
    confirmed: bool = Field(default=False, description="必须显式为 true，否则拒绝写入资料")
    result_id: str | None = Field(
        default=None,
        alias="resultId",
        max_length=64,
        description="source=assessment 时必填，端侧测评结果 id（E1.5 仅透传，不校验）",
    )


class EmotionLabProfileSourceResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    mbti_type: MbtiType = Field(alias="mbtiType")
    source: Literal["assessment", "self_reported"]
    assessment_version: str | None = Field(
        default=None,
        alias="assessmentVersion",
        description="E1.5 未服务端计分，恒为 null；E2 由 submit 返回版本号",
    )
    result_id: str | None = Field(default=None, alias="resultId")
    confirmed_at: datetime | None = Field(
        default=None,
        alias="confirmedAt",
        description="本次确认时间；资料内容未变化时返回上次变更时间（幂等）",
    )

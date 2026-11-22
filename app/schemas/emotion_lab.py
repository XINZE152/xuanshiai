"""情感实验室（MBTI）请求/响应模型。

会话、答案和结果都由后端保存。客户端只负责呈现题目和动画，不能自行
伪造结果或把未完成的答案标记为完成。
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
        description="source=assessment 时必填，服务端已完成测评结果 id",
    )


class EmotionLabProfileSourceResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    mbti_type: MbtiType = Field(alias="mbtiType")
    source: Literal["assessment", "self_reported"]
    assessment_version: str | None = Field(
        default=None,
        alias="assessmentVersion",
        description="服务端测评结果版本；手动填写时为空",
    )
    result_id: str | None = Field(default=None, alias="resultId")
    confirmed_at: datetime | None = Field(
        default=None,
        alias="confirmedAt",
        description="本次确认时间；资料内容未变化时返回上次变更时间（幂等）",
    )


class EmotionLabMatchItem(BaseModel):
    """「合拍的人」条目：算法与首页卡片同源（discovery._candidate_score）。"""

    model_config = ConfigDict(populate_by_name=True)

    user_id: int = Field(alias="userId", description="候选人 user_id")
    nickname: str | None = Field(default=None, description="昵称；空表示未设置")
    avatar: str | None = Field(default=None, description="头像 URL；空表示未上传")
    mbti: MbtiType | None = Field(default=None, description="候选人 MBTI；空表示资料未填")
    match_percent: int = Field(alias="matchPercent", ge=0, le=100, description="合拍度百分比")
    match_reason: str = Field(alias="matchReason", description="合拍理由，可直接展示")


class EmotionLabMatchesResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    items: list[EmotionLabMatchItem] = Field(default_factory=list)
    hint: str | None = Field(
        default=None, description="无法给出结果时的可展示提示（如尚未完成 MBTI 测试）"
    )


class EmotionLabAnswer(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    question_id: str = Field(alias="questionId", min_length=1, max_length=64)
    value: int = Field(ge=1, le=7)


class EmotionLabAnswersRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    answers: list[EmotionLabAnswer] = Field(default_factory=list, max_length=100)


class EmotionLabAssessment(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    version: str
    kind: Literal["mbti"] = "mbti"
    title: str
    authorization: dict
    can_start: bool = Field(alias="canStart")
    question_count: int = Field(alias="questionCount")
    scale: dict
    dimensions: list[str]
    tie_break: Literal["first_pole"] = Field(default="first_pole", alias="tieBreak")
    result_copy_version: str = Field(alias="resultCopyVersion")


class EmotionLabSession(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_version: int = Field(alias="schemaVersion")
    id: str
    definition_id: str = Field(alias="definitionId")
    definition_version: str = Field(alias="definitionVersion")
    result_copy_version: str = Field(alias="resultCopyVersion")
    kind: Literal["mbti"] = "mbti"
    status: Literal["in_progress", "completed", "discarded"]
    question_ids: list[str] = Field(alias="questionIds")
    questions: list[dict]
    answers: list[EmotionLabAnswer]
    result: dict | None = None
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class EmotionLabProfileSource(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    mbti_type: MbtiType = Field(alias="mbtiType")
    source: Literal["assessment", "self_reported"]
    assessment_version: str | None = Field(default=None, alias="assessmentVersion")
    result_id: str | None = Field(default=None, alias="resultId")
    confirmed_at: datetime | None = Field(default=None, alias="confirmedAt")


class EmotionLabSummaryResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    assessments: list[EmotionLabAssessment] = Field(default_factory=list)
    active_session: EmotionLabSession | None = Field(default=None, alias="activeSession")
    profile_source: EmotionLabProfileSource | None = Field(default=None, alias="profileSource")
    disclaimer: str
    disclaimer_version: str = Field(alias="disclaimerVersion")

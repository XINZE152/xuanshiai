"""成稿反哺资料卡草稿的请求/响应模型。

与墨相 ``ai_profile_draft`` 隔离：本模块只描述资料卡开放文本草稿，
不复用画像草稿 PATCH / publish 契约。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.ai_common import AiTaskStatus
from app.schemas.auth import ProfileQaAnswer

ProfileCardDraftStatus = Literal[
    "queued",
    "running",
    "ready",
    "partial",
    "applied",
    "discarded",
    "failed",
]


class ProfileCardSummarizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force: bool = False


class ProfileCardSummarizeAccepted(BaseModel):
    task_id: str
    draft_id: str | None = Field(default=None, description="本次任务对应的稳定草稿 ID；旧任务可能为空，客户端不得猜最新草稿")
    status: AiTaskStatus
    poll_url: str
    replayed: bool = False
    poll_after_ms: int = Field(default=1000, ge=0)


class ProfileCardDraftField(BaseModel):
    value: str = ""
    candidates: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source_ref: str | None = None


class ProfileCardDraftFields(BaseModel):
    self_intro: ProfileCardDraftField = Field(default_factory=ProfileCardDraftField)
    qa_1_partner: ProfileCardDraftField = Field(default_factory=ProfileCardDraftField)
    qa_3_love: ProfileCardDraftField = Field(default_factory=ProfileCardDraftField)
    qa_2_sports_candidates: ProfileCardDraftField = Field(
        default_factory=ProfileCardDraftField
    )
    interest_tag_candidates: ProfileCardDraftField = Field(
        default_factory=ProfileCardDraftField
    )


class ProfileCardDraftRead(BaseModel):
    draft_id: str
    status: ProfileCardDraftStatus
    expected_revision: int
    source_revision_id: int | None = None
    base_profile_revision: int = Field(..., ge=0, description="读取草稿时的资料版本，采用时原样提交")
    prompt_version: str | None = None
    schema_version: str | None = None
    fields: ProfileCardDraftFields
    task_id: str | None = None
    generated_at: datetime | None = None
    applied_at: datetime | None = None
    applied_meta: dict[str, Any] | None = None


class ProfileCardReplaceExisting(BaseModel):
    model_config = ConfigDict(extra="forbid")

    self_intro: bool = False


class ProfileCardAcceptedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    self_intro: str | None = Field(default=None, max_length=500)
    qa_answers: list[ProfileQaAnswer] | None = None
    # 超限由资料卡服务层返回稳定的 AI_INPUT_INVALID，而不是入口静默截断。
    personal_tags: list[str] | None = None

    @field_validator("self_intro")
    @classmethod
    def strip_intro(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()


class ProfileCardApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_id: str | None = Field(default=None, min_length=1, max_length=64, description="精确采用本人指定草稿；省略仅为旧客户端兼容")
    expected_revision: int = Field(..., ge=0, description="GET 返回的草稿版本，采用时原样提交")
    source_revision_id: int | None = Field(default=None, ge=0, description="GET 返回的画像来源 ID；提供时校验相等，且草稿必须仍来自最新画像")
    base_profile_revision: int | None = Field(default=None, ge=0, description="GET 返回的个人资料版本；提供时必须等于采用时的当前版本")
    tag_apply_mode: Literal["merge", "replace_selection"] = Field(default="merge", description="默认合并标签；replace_selection 必须提交 personal_tags 完整选择")
    accepted: ProfileCardAcceptedPayload = Field(default_factory=ProfileCardAcceptedPayload)
    rejected: list[str] = Field(default_factory=list)
    replace_existing: ProfileCardReplaceExisting = Field(
        default_factory=ProfileCardReplaceExisting
    )

    @model_validator(mode="after")
    def reject_unknown_fact_fields(self) -> ProfileCardApplyRequest:
        forbidden = {
            "height",
            "education",
            "income",
            "occupation",
            "city",
            "education_level",
            "city_code",
        }
        extra_rejected = [item for item in self.rejected if item in forbidden]
        if extra_rejected:
            # 事实列只能被丢弃，出现在 rejected 列表也视为明确不写。
            return self
        return self


class ProfileCardApplyResponse(BaseModel):
    status: Literal["applied"]
    replayed: bool = False
    draft_id: str
    expected_revision: int
    written_fields: list[str] = Field(default_factory=list)
    skipped_fields: list[str] = Field(default_factory=list)
    profile: dict[str, Any] | None = None


class ProfileCardPublicPayload(BaseModel):
    """海报可公开内容白名单：仅已采用的公开介绍与标签，不含心理洞察。"""

    persona_title: str
    persona_tags: list[str] = Field(default_factory=list)
    self_intro: str = ""


class ProfileCardExportResponse(BaseModel):
    """``GET /profile-card/export`` 响应：只导出当前正式版本的公开字段。

    ``revision_id`` 为服务端当前 personal 正式版本；调用方传入的
    ``revision_id`` 与之不一致时返回 409，避免迟到页面导出错误版本。
    """

    status: Literal["ready"]
    subject: Literal["personal"]
    revision_id: int
    source_revision_id: int
    public: ProfileCardPublicPayload

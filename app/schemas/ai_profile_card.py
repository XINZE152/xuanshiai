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
    personal_tags: list[str] | None = Field(default=None, max_length=10)

    @field_validator("self_intro")
    @classmethod
    def strip_intro(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()


class ProfileCardApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(..., ge=0)
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
    written_fields: list[str] = Field(default_factory=list)
    skipped_fields: list[str] = Field(default_factory=list)
    profile: dict[str, Any] | None = None

"""Request and response contracts for AI-avatar conversations."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AiAvatarMessageRequest(BaseModel):
    """One visitor question sent to a target user's AI avatar."""

    content: str = Field(min_length=1, max_length=300)

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("问题不能为空")
        return normalized


class AiAvatarProfileResponse(BaseModel):
    """Privacy-filtered profile context visible to the current viewer."""

    id: int
    name: str
    avatar: str | None = None
    age: int | None = None
    city: str | None = None
    job: str | None = None
    education: str | None = None
    tags: list[str] = Field(default_factory=list)
    bio: str | None = None
    interests: list[str] = Field(default_factory=list)
    expectations: list[str] = Field(default_factory=list)
    allowExpectations: bool = True
    restricted: bool = False
    aiMode: Literal["real"] = "real"


class AiAvatarMessageResponse(BaseModel):
    """Message shape consumed by the existing uni-app chat bubble UI."""

    model_config = ConfigDict(populate_by_name=True)

    id: int
    type: Literal["text"] = "text"
    content: str
    time: int
    showTime: bool = False
    isMine: bool
    avatar: str | None = None
    source: Literal["user", "real-ai", "owner-answer", "system"]
    category: Literal["basic", "interest", "expectation", "platform", "general"] = "general"
    handoffRequired: bool = False
    handoffStatus: Literal["not_requested", "pending", "answered"] = "not_requested"


class AiAvatarConversationResponse(BaseModel):
    """Independent AI conversation history for one viewer-target pair."""

    targetUserId: int
    messages: list[AiAvatarMessageResponse]


class AiAvatarReplyResult(BaseModel):
    """Normalized real-provider result retained for frontend compatibility."""

    reply: str
    category: Literal["basic", "interest", "expectation", "platform", "general"]
    source: Literal["real-ai"] = "real-ai"
    handoffRequired: bool = False
    handoffStatus: Literal["not_requested"] = "not_requested"


class AiAvatarSendResponse(BaseModel):
    """Conversation snapshot returned after a successful AI reply."""

    messages: list[AiAvatarMessageResponse]
    result: AiAvatarReplyResult


class AiAvatarClearResponse(BaseModel):
    """Deletion result for the current viewer's AI conversation."""

    targetUserId: int
    deleted: bool = True


class AiAvatarOwnerAnswerRequest(BaseModel):
    """An owner's reusable answer for one visitor question."""

    answer: str = Field(min_length=1, max_length=500)

    @field_validator("answer")
    @classmethod
    def normalize_answer(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("鍥炵瓟涓嶈兘涓虹┖")
        return normalized


class AiAvatarOwnerAnswerCreateRequest(AiAvatarOwnerAnswerRequest):
    """Create or update a reusable owner answer."""

    question: str = Field(min_length=1, max_length=300)

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("闂涓嶈兘涓虹┖")
        return normalized


class AiAvatarOwnerQuestionResponse(BaseModel):
    """Question or reusable answer shown in the owner's AI-avatar dashboard."""

    id: int
    question: str
    answer: str | None = None
    status: Literal["pending", "answered"]
    created_at: datetime
    answered_at: datetime | None = None


class AiAvatarOwnerDashboardResponse(BaseModel):
    """Owner-only AI-avatar questions and answers."""

    pending_questions: list[AiAvatarOwnerQuestionResponse]
    answers: list[AiAvatarOwnerQuestionResponse]

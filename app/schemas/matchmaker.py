"""Request and response contracts for the first-phase matchmaker module."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class MatchmakerCard(BaseModel):
    user_id: int
    nickname: str | None
    avatar: str | None
    application_type: Literal["service_matchmaker"]
    intro: str
    certification_tags: list[str]
    success_count: int
    rating_score: float
    rating_count: int
    is_available: bool


class MatchmakerPage(BaseModel):
    items: list[MatchmakerCard]
    page: int
    page_size: int
    total: int
    has_more: bool


class MatchmakerServiceRequestCreate(BaseModel):
    matchmaker_id: int = Field(ge=1)
    requirement: str = Field(min_length=10, max_length=2000)


class MatchmakerServiceRequestUpdate(BaseModel):
    status: Literal[1, 2, 3]
    feedback: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_feedback(self) -> "MatchmakerServiceRequestUpdate":
        if self.status in (2, 3) and not self.feedback:
            raise ValueError("完成或取消服务时必须填写处理说明")
        return self


class MatchmakerServiceRequestResponse(BaseModel):
    id: int
    user_id: int
    matchmaker_id: int | None
    service_type: Literal[1, 2, 3]
    status: Literal[0, 1, 2, 3]
    requirement: str
    feedback: str | None
    created_at: datetime
    updated_at: datetime
    start_at: datetime | None
    end_at: datetime | None


class MatchmakerServiceRequestPage(BaseModel):
    items: list[MatchmakerServiceRequestResponse]
    page: int
    page_size: int
    total: int
    has_more: bool


class MatchmakerAdminServiceRequestUpdate(BaseModel):
    matchmaker_id: int | None = Field(default=None, ge=1)
    status: Literal[0, 1, 2, 3] | None = None
    feedback: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_update(self) -> "MatchmakerAdminServiceRequestUpdate":
        if self.matchmaker_id is None and self.status is None and self.feedback is None:
            raise ValueError("至少提供一个需要修改的字段")
        if self.status in (2, 3) and not self.feedback:
            raise ValueError("完成或取消服务时必须填写处理说明")
        return self

"""线下约见接口契约。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class MeetingRequestCreate(BaseModel):
    target_user_id: int = Field(ge=1)
    note: str = Field(min_length=1, max_length=2000)


class MatchmakerMeetingRequestCreate(BaseModel):
    service_id: int = Field(ge=1)
    target_user_id: int = Field(ge=1)
    note: str = Field(min_length=1, max_length=2000)


class MeetingRequestResponse(BaseModel):
    id: int
    user_id: int
    target_user_id: int
    service_id: int | None
    matchmaker_id: int | None
    organization_id: int | None
    status: Literal["SUBMITTED", "CONTACTED", "ACCEPTED", "DECLINED", "CLOSED"]
    note: str
    created_at: datetime
    updated_at: datetime


class MeetingScheduleCreate(BaseModel):
    organizer_id: int = Field(ge=1)
    organization_id: int | None = Field(default=None, ge=1)
    scheduled_at: datetime
    location: str = Field(min_length=1, max_length=255)


class MeetingRecordResponse(BaseModel):
    id: int
    request_id: int
    organizer_id: int
    organization_id: int | None
    scheduled_at: datetime
    location: str
    status: Literal["SCHEDULED", "REMINDED", "CHECKED_IN", "COMPLETED", "CANCELLED", "NO_SHOW"]
    cancel_reason: str | None
    created_at: datetime
    updated_at: datetime


class MeetingStatusUpdate(BaseModel):
    status: Literal["CONTACTED", "ACCEPTED", "DECLINED", "CLOSED"]
    reason: str | None = Field(default=None, max_length=255)


class MeetingFeedbackCreate(BaseModel):
    target_rating: int | None = Field(default=None, ge=1, le=5)
    matchmaker_rating: int | None = Field(default=None, ge=1, le=5)
    continue_intent: Literal[1, 2, 3] | None = None
    private_feedback: str | None = Field(default=None, max_length=2000)

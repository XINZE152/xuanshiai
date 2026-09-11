"""直播相亲接口模型。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


LiveStatus = Literal["DRAFT", "SCHEDULED", "CHECK_IN", "WARMUP", "OPEN", "INTRO", "MATCHMAKER_QA", "HEART_LIGHT", "SELECT", "GUIDED_EXCHANGE", "DOUBLE_CONFIRM", "APPLY_KNOW", "CLOSED"]


class LiveSessionCreate(BaseModel):
    title: str = Field(min_length=2, max_length=128)
    city_code: str | None = Field(default=None, max_length=32)
    scheduled_at: datetime
    host_user_id: int = Field(ge=1)
    max_stage_seats: int = Field(default=8, ge=1, le=8)
    recording_enabled: bool = False
    rules_text: str | None = Field(default=None, max_length=5000)


class LiveSessionResponse(BaseModel):
    id: int
    title: str
    city_code: str | None = None
    scheduled_at: datetime
    status: str
    state_version: int
    host_user_id: int
    max_stage_seats: int
    recording_enabled: bool
    rules_text: str | None = None
    created_at: datetime
    updated_at: datetime


class LiveSessionPage(BaseModel):
    items: list[LiveSessionResponse]
    total: int


class LiveRegistrationResponse(BaseModel):
    id: int
    session_id: int
    user_id: int
    status: str
    device_check_passed: bool
    checked_in_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class LiveActionResponse(BaseModel):
    session_id: int
    user_id: int
    status: str


class LiveTransitionRequest(BaseModel):
    to_status: LiveStatus
    expected_version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=255)


class LiveDeviceCheckRequest(BaseModel):
    passed: bool


class LiveInviteRequest(BaseModel):
    user_id: int = Field(ge=1)
    seat_no: int = Field(ge=1, le=8)


class LiveInviteResponse(BaseModel):
    session_id: int
    seat_no: int
    user_id: int
    status: str
    invitation_token: str
    invitation_expires_at: datetime


class LiveInviteDecision(BaseModel):
    invitation_token: str = Field(min_length=32, max_length=32)
    decision: Literal["accept", "reject"]


class LiveRoleAssignment(BaseModel):
    user_id: int = Field(ge=1)
    role_code: Literal["MATCHMAKER", "MODERATOR"]
    enabled: bool = True


class LiveRoleAssignmentResponse(BaseModel):
    session_id: int
    user_id: int
    role_code: str
    enabled: bool


class LiveSeatRemoveRequest(BaseModel):
    user_id: int = Field(ge=1)
    reason: str = Field(min_length=2, max_length=255)


class LiveInteractionRequest(BaseModel):
    target_user_id: int = Field(ge=1)
    interaction_type: Literal["HEART_LIGHT", "SELECT", "CONFIRM"]


class LiveInteractionResponse(BaseModel):
    id: int
    session_id: int
    actor_user_id: int
    target_user_id: int
    interaction_type: str
    status: str
    created_at: datetime


class LiveReportRequest(BaseModel):
    target_user_id: int = Field(ge=1)
    category: Literal["HARASSMENT", "PRIVACY", "CONTENT", "AUDIO_VIDEO", "OTHER"]
    description: str | None = Field(default=None, max_length=500)


class LiveReportResponse(BaseModel):
    id: int
    status: str


class LiveRtcTicketResponse(BaseModel):
    sdk_app_id: int
    room_id: int
    user_id: str
    user_sig: str
    expires_in: int
    role: str

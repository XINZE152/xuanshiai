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
    user_nickname: str | None = None
    user_member_code: str | None = None
    target_nickname: str | None = None
    target_member_code: str | None = None
    matchmaker_name: str | None = None


class MeetingScheduleCreate(BaseModel):
    organizer_id: int = Field(ge=1)
    organization_id: int | None = Field(default=None, ge=1)
    scheduled_at: datetime
    location: str = Field(min_length=1, max_length=255)
    member_visible: bool = Field(default=True, description="会员端是否可见")
    sms_remind: bool = Field(default=True, description="是否发送约会短信提醒")


class MeetingDirectCreate(BaseModel):
    """约会管理-添加约会：直接指定双方与服务红娘，无需先有约见申请。"""

    from_user_id: int = Field(ge=1, description="男方会员ID（提交人）")
    to_user_id: int = Field(ge=1, description="女方会员ID（被约见人）")
    organizer_id: int = Field(ge=1, description="本次约见服务红娘用户ID")
    organization_id: int | None = Field(default=None, ge=1)
    scheduled_at: datetime | None = Field(default=None, description="见面时间，留空表示待确定")
    location: str | None = Field(default=None, max_length=255, description="见面地点，留空表示待确定")
    member_visible: bool = True
    sms_remind: bool = True
    met: bool = Field(default=False, description="是否已见面；true 计为服务成功")


class MeetingRecordResponse(BaseModel):
    id: int
    request_id: int
    organizer_id: int
    organization_id: int | None
    scheduled_at: datetime
    location: str
    status: Literal["SCHEDULED", "REMINDED", "CHECKED_IN", "COMPLETED", "CANCELLED", "NO_SHOW"]
    cancel_reason: str | None
    member_visible: int = 1
    sms_remind: int = 1
    created_at: datetime
    updated_at: datetime
    from_user_id: int | None = None
    from_nickname: str | None = None
    to_user_id: int | None = None
    to_nickname: str | None = None
    organizer_name: str | None = None
    feedback_count: int = 0


class MeetingStatistics(BaseModel):
    """约会管理顶部 6 张统计卡。"""

    total_arranged: int = 0
    total_met: int = 0
    month_arranged: int = 0
    month_waiting: int = 0
    month_met: int = 0
    month_not_met: int = 0


class MeetingStatusUpdate(BaseModel):
    status: Literal["CONTACTED", "ACCEPTED", "DECLINED", "CLOSED"]
    reason: str | None = Field(default=None, max_length=255)


class MeetingFeedbackCreate(BaseModel):
    target_rating: int | None = Field(default=None, ge=1, le=5)
    matchmaker_rating: int | None = Field(default=None, ge=1, le=5)
    continue_intent: Literal[1, 2, 3] | None = None
    private_feedback: str | None = Field(default=None, max_length=2000)


class MeetingRequestAdminPage(BaseModel):
    items: list[MeetingRequestResponse]
    page: int
    page_size: int
    total: int
    has_more: bool


class MeetingRecordAdminPage(BaseModel):
    items: list[MeetingRecordResponse]
    page: int
    page_size: int
    total: int
    has_more: bool


class MeetingRecordAdminUpdate(BaseModel):
    scheduled_at: datetime | None = None
    location: str | None = Field(default=None, min_length=1, max_length=255)
    status: Literal["SCHEDULED", "REMINDED", "CHECKED_IN", "COMPLETED", "CANCELLED", "NO_SHOW"] | None = None
    cancel_reason: str | None = Field(default=None, max_length=255)
    member_visible: bool | None = None
    sms_remind: bool | None = None


class MeetingFeedbackAdminItem(BaseModel):
    id: int
    meeting_id: int
    user_id: int
    target_rating: int | None
    matchmaker_rating: int | None
    continue_intent: int | None
    private_feedback: str | None
    created_at: datetime

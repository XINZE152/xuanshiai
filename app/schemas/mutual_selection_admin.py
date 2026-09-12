"""Mutual-selection (互选) activity contracts for the back office (M7-A)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class MutualActivityBase(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    cover: str | None = Field(default=None, max_length=255)
    start_time: datetime
    end_time: datetime
    pick_limit: int = Field(default=5, ge=0, le=1000)
    virtual_signup: int = Field(default=0, ge=0, le=1000000)
    price_male: float = Field(default=0, ge=0, le=1000000)
    price_female: float = Field(default=0, ge=0, le=1000000)
    price_vip: float = Field(default=0, ge=0, le=1000000)
    reward_promoter: float = Field(default=0, ge=0, le=1000000)
    reward_service: float = Field(default=0, ge=0, le=1000000)
    require_realname: bool = False
    require_avatar: bool = False
    require_three_photo: bool = False
    intro: str | None = Field(default=None, max_length=20000)
    share_title: str | None = Field(default=None, max_length=80)
    share_desc: str | None = Field(default=None, max_length=500)
    share_icon: str | None = Field(default=None, max_length=255)
    success_mode: Literal["show_wechat", "contact_matchmaker"] = "show_wechat"
    notice_html: str | None = Field(default=None, max_length=10000)
    success_notice: str | None = Field(default=None, max_length=10000)

    @model_validator(mode="after")
    def validate_times(self) -> "MutualActivityBase":
        if self.end_time <= self.start_time:
            raise ValueError("end_time 必须晚于 start_time")
        return self


class MutualActivityCreate(MutualActivityBase):
    visible: bool = True
    sort: int = Field(default=0, ge=-1000000, le=1000000)


class MutualActivityUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=80)
    cover: str | None = Field(default=None, max_length=255)
    start_time: datetime | None = None
    end_time: datetime | None = None
    pick_limit: int | None = Field(default=None, ge=0, le=1000)
    virtual_signup: int | None = Field(default=None, ge=0, le=1000000)
    price_male: float | None = Field(default=None, ge=0, le=1000000)
    price_female: float | None = Field(default=None, ge=0, le=1000000)
    price_vip: float | None = Field(default=None, ge=0, le=1000000)
    reward_promoter: float | None = Field(default=None, ge=0, le=1000000)
    reward_service: float | None = Field(default=None, ge=0, le=1000000)
    require_realname: bool | None = None
    require_avatar: bool | None = None
    require_three_photo: bool | None = None
    intro: str | None = Field(default=None, max_length=20000)
    share_title: str | None = Field(default=None, max_length=80)
    share_desc: str | None = Field(default=None, max_length=500)
    share_icon: str | None = Field(default=None, max_length=255)
    success_mode: Literal["show_wechat", "contact_matchmaker"] | None = None
    notice_html: str | None = Field(default=None, max_length=10000)
    success_notice: str | None = Field(default=None, max_length=10000)
    visible: bool | None = None
    sort: int | None = Field(default=None, ge=-1000000, le=1000000)

    @model_validator(mode="after")
    def require_update(self) -> "MutualActivityUpdate":
        if not self.model_dump(exclude_unset=True):
            raise ValueError("至少提供一个需要修改的字段")
        return self


class MutualActivityItem(MutualActivityBase):
    id: int
    status: int = 1
    visible: bool = True
    sort: int = 0
    male_count: int = 0
    female_count: int = 0
    participant_count: int = 0
    created_at: datetime | None = None
    link_url: str | None = None


class MutualActivityPage(BaseModel):
    items: list[MutualActivityItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class MutualParticipant(BaseModel):
    signup_id: int | None = None
    user_id: int
    nickname: str | None
    gender: str | None
    avatar: str | None
    created_at: datetime | None = None


class MutualParticipantAdd(BaseModel):
    user_id: int = Field(ge=1)


class MutualRecordItem(BaseModel):
    id: int
    activity_id: int
    activity_title: str | None
    from_user_id: int
    from_nickname: str | None
    action: str
    action_label: str
    to_user_id: int
    to_nickname: str | None
    is_success: bool
    result: str
    result_label: str
    created_at: datetime | None = None


class MutualRecordPage(BaseModel):
    items: list[MutualRecordItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class MutualOption(BaseModel):
    id: int
    label: str

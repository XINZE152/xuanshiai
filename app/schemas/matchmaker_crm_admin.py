"""Member CRM contracts for the independent matchmaker back office."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class MemberListItem(BaseModel):
    id: int
    nickname: str | None
    phone: str | None
    gender: int | None
    status: int
    is_vip: bool
    vip_end_at: datetime | None
    matchmaker_id: int | None
    created_at: datetime
    avatar: str | None = None
    birthday: date | None = None
    is_married: int | None = None
    height: int | None = None
    weight: int | None = None
    zodiac: str | None = None
    household: str | None = None
    ethnicity: str | None = None
    house: str | None = None
    car: str | None = None
    smoking: str | None = None
    hometown_province_code: str | None = None
    hometown_city_code: str | None = None
    hometown_district_code: str | None = None
    residence_province_code: str | None = None
    residence_city_code: str | None = None
    residence_district_code: str | None = None
    household_province_code: str | None = None
    household_city_code: str | None = None
    household_district_code: str | None = None
    income: float | None = None
    hometown: str | None = None
    residence: str | None = None
    education: str | None = None
    job: str | None = None
    auth_status: int | None = None
    intention_level: int | None = None
    last_follow_at: datetime | None = None
    next_follow_at: datetime | None = None


class MemberPage(BaseModel):
    items: list[MemberListItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class MemberDetail(MemberListItem):
    avatar: str | None
    birthday: date | None
    is_married: int | None
    residence_city_code: str | None
    education: str | None = None
    job: str | None = None
    tags: dict[str, list[str]] | list[str] | None = None
    self_intro: str | None = None
    ideal_partner: str | None = None
    wechat: str | None = None
    last_login_at: datetime | None = None
    ip_location: str | None = None


class MemberStatusUpdate(BaseModel):
    status: Literal[1, 2, 3]
    reason: str = Field(min_length=1, max_length=255)


class MemberStatusResponse(BaseModel):
    id: int
    status: Literal[1, 2, 3]
    reason: str


class MemberStatistics(BaseModel):
    total: int
    male: int
    female: int
    vip: int
    active: int
    unassigned: int = 0
    never_followed: int = 0
    follow_due_today: int = 0


class MemberAssignmentUpdate(BaseModel):
    matchmaker_id: int | None = Field(default=None, ge=1, description="服务红娘用户 ID；传 null 表示取消分派")


class MemberAssignmentResponse(BaseModel):
    user_id: int
    matchmaker_id: int | None

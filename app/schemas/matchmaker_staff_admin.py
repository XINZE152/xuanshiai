"""Contracts for platform matchmaker staff administration."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CommissionLevelItem(BaseModel):
    id: int
    code: str
    name: str
    rate_percent: Decimal
    sort: int
    status: Literal[1, 2]


class StoreDictItem(BaseModel):
    id: int
    code: str
    name: str
    display_name: str | None
    status: Literal[1, 2, 3]


class MatchmakerStaffItem(BaseModel):
    id: int
    avatar: str | None
    display_name: str
    username: str | None
    store_id: int | None
    store_name: str | None
    role_tag: Literal["super", "normal"]
    role_label: str
    phone: str | None
    wechat: str | None
    wechat_qr: str | None = None
    commission_level_id: int | None
    commission_level_name: str | None
    commission_rate: Decimal | None
    success_count: int = 0
    commission_amount: Decimal = Decimal("0.00")
    locked: bool = False
    visible: bool = True
    menu_permission_count: int = 0
    description: str | None = None
    slogan: str | None = None
    sort: int = 0
    contact_editable: bool = True
    lock_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class MatchmakerStaffDetail(MatchmakerStaffItem):
    account_id: int | None = None
    data_scope: Literal["SELF", "STORE", "ORGANIZATION", "ALL"] | None = None
    intro: str | None = None


class MatchmakerStaffPage(BaseModel):
    items: list[MatchmakerStaffItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class MatchmakerUserCandidate(BaseModel):
    id: int
    nickname: str | None = None
    phone: str | None = None
    avatar: str | None = None


class MatchmakerStaffCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: int | None = Field(default=None, ge=1)
    lookup: str | None = Field(default=None, min_length=1, max_length=100)
    lookup_by: Literal["nickname", "phone"] = "nickname"
    avatar: str | None = Field(default=None, max_length=500)
    display_name: str = Field(min_length=1, max_length=32)
    phone: str = Field(min_length=11, max_length=20)
    wechat: str | None = Field(default=None, max_length=64)
    wechat_qr: str | None = Field(default=None, max_length=500)
    store_id: int | None = Field(default=None, ge=1)
    commission_level_id: int | None = Field(default=None, ge=1)
    role_tag: Literal["super", "normal"] = "normal"
    description: str | None = Field(default=None, max_length=2000)
    slogan: str | None = Field(default=None, max_length=64)
    sort: int = Field(default=0, ge=0)
    contact_editable: bool = True
    lock_at: datetime | None = None
    visible: bool = True

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        phone = value.strip()
        if not phone.isdigit() or len(phone) < 11:
            raise ValueError("手机号格式不正确")
        return phone


class MatchmakerStaffUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    avatar: str | None = Field(default=None, max_length=500)
    display_name: str | None = Field(default=None, min_length=1, max_length=32)
    phone: str | None = Field(default=None, min_length=11, max_length=20)
    wechat: str | None = Field(default=None, max_length=64)
    wechat_qr: str | None = Field(default=None, max_length=500)
    store_id: int | None = Field(default=None, ge=1)
    commission_level_id: int | None = Field(default=None, ge=1)
    role_tag: Literal["super", "normal"] | None = None
    description: str | None = Field(default=None, max_length=2000)
    slogan: str | None = None
    sort: int | None = None
    contact_editable: bool | None = None
    lock_at: datetime | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)
    visible: bool | None = None

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str | None) -> str | None:
        if value is None:
            return value
        phone = value.strip()
        if not phone.isdigit() or len(phone) < 11:
            raise ValueError("手机号格式不正确")
        return phone


class MatchmakerLockUpdate(BaseModel):
    locked: bool


class MatchmakerVisibilityUpdate(BaseModel):
    visible: bool


class MatchmakerDeleteResponse(BaseModel):
    id: int
    deleted: bool


class AdminMenuNode(BaseModel):
    id: int
    parent_id: int | None
    name: str
    path: str | None
    menu_type: Literal["directory", "menu", "button"]
    permission_code: str | None
    icon: str | None = None
    sort: int = 0
    children: list["AdminMenuNode"] = Field(default_factory=list)


class MatchmakerPermissions(BaseModel):
    matchmaker_id: int
    menu_ids: list[int] = Field(validation_alias="menuIds", serialization_alias="menuIds")

    model_config = ConfigDict(populate_by_name=True)


class MatchmakerPermissionsUpdate(BaseModel):
    menu_ids: list[int] = Field(default_factory=list, max_length=200, validation_alias="menuIds")

    model_config = ConfigDict(populate_by_name=True)


class MatchmakerWorkReport(BaseModel):
    matchmaker_id: int
    from_date: date
    to_date: date
    new_lead_count: int = 0
    matchmaking_count: int = 0
    success_count: int = 0
    commission_amount: Decimal = Decimal("0.00")
    follow_up_count: int = 0
    assigned_member_count: int = 0
    new_member_count: int = 0
    lead_follow_up_count: int = 0
    meeting_request_count: int = 0
    meeting_arranged_count: int = 0
    offline_income: Decimal = Decimal("0.00")


class FunnelItem(BaseModel):
    stage: str
    label: str
    count: int


class MonthlyTrendItem(BaseModel):
    month: str
    new_lead_count: int = 0
    matchmaking_count: int = 0
    success_count: int = 0
    commission_amount: Decimal = Decimal("0.00")


class MatchmakerDetailReport(BaseModel):
    matchmaker_id: int
    from_date: date
    to_date: date
    work_report: MatchmakerWorkReport
    funnel: list[FunnelItem]
    monthly_trends: list[MonthlyTrendItem]
    success_rate: Decimal = Decimal("0.00")
    platform_success_rate: Decimal = Decimal("0.00")
    export_url: str | None = None


class MatchmakerPosterResponse(BaseModel):
    matchmaker_id: int
    url: str
    qr_content: str


class MatchmakerPlatformTokenResponse(BaseModel):
    matchmaker_id: int
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    jump_url: str


class MatchmakerTutorial(BaseModel):
    title: str
    content: str
    link_url: str | None = None
    updated_at: datetime | None = None


class CommonUploadResponse(BaseModel):
    url: str
    content_type: str
    size: int
    purpose: str = "avatar"

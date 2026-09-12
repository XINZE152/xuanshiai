"""Independent back-office contracts for stores and resource assignments."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class StoreAdminItem(BaseModel):
    id: int
    code: str
    name: str
    display_name: str | None
    region_code: str | None
    link_url: str | None
    sort_order: int
    qr_code: str | None
    status: Literal[1, 2, 3]
    auto_redirect: bool
    member_count: int = 0
    matchmaker_count: int = 0
    created_at: datetime
    updated_at: datetime


class StoreAdminPage(BaseModel):
    items: list[StoreAdminItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class StoreAdminCreate(BaseModel):
    code: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=128)
    display_name: str | None = Field(default=None, max_length=128)
    region_code: str | None = Field(default=None, max_length=64)
    link_url: str | None = Field(default=None, max_length=255)
    sort_order: int = Field(default=0, ge=0, le=9999)
    qr_code: str | None = Field(default=None, max_length=500)
    auto_redirect: bool = False


class StoreSubsiteMode(BaseModel):
    """分站模式：all 全国模式 / region 指定地区。"""

    mode: Literal["all", "region"] = "all"
    updated_at: datetime | None = None


class StoreSubsiteModeUpdate(BaseModel):
    mode: Literal["all", "region"]


class StoreMemberAdminPage(BaseModel):
    items: list["StoreMemberAdminItem"]
    page: int
    page_size: int
    total: int
    has_more: bool


class AssignmentAdminPage(BaseModel):
    items: list["AssignmentAdminItem"]
    page: int
    page_size: int
    total: int
    has_more: bool


class StoreAdminUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    display_name: str | None = Field(default=None, max_length=128)
    region_code: str | None = Field(default=None, max_length=64)
    link_url: str | None = Field(default=None, max_length=255)
    sort_order: int | None = Field(default=None, ge=0, le=9999)
    qr_code: str | None = Field(default=None, max_length=500)
    auto_redirect: bool | None = None


class StoreStatusUpdate(BaseModel):
    status: Literal[1, 2, 3]
    reason: str | None = Field(default=None, max_length=255)


class StoreMemberAdminItem(BaseModel):
    id: int
    organization_id: int
    user_id: int
    nickname: str | None
    phone_masked: str | None
    role_code: str
    status: Literal[1, 2, 3]
    started_at: datetime
    ended_at: datetime | None


class StoreReport(BaseModel):
    store_id: int
    active_member_count: int
    active_assignment_count: int
    total_assignment_count: int


class AssignmentAdminItem(BaseModel):
    id: int
    user_id: int
    nickname: str | None
    organization_id: int | None
    organization_name: str | None
    matchmaker_id: int | None
    matchmaker_name: str | None
    source: str
    status: Literal[1, 2]
    effective_at: datetime
    ended_at: datetime | None
    end_reason: str | None


class AssignmentAdminUpdate(BaseModel):
    organization_id: int | None = Field(default=None, ge=1)
    matchmaker_id: int | None = Field(default=None, ge=1)
    reason: str = Field(min_length=1, max_length=255)


# ------------------------- M5 分店报表 -------------------------
class StoreReportSummary(BaseModel):
    store_id: int
    store_name: str | None
    lead_count: int
    member_count: int
    online_match_count: int
    online_vip_count: int
    offline_vip_count: int
    meeting_arranged_count: int
    online_commission: Decimal
    offline_performance: Decimal
    meeting_rank: int | None = None
    online_commission_rank: int | None = None
    offline_performance_rank: int | None = None


class StoreReportMonthlyRow(BaseModel):
    month: str
    new_male_members: int
    new_female_members: int
    new_leads: int
    new_online_vip: int
    new_match_requests: int
    new_offline_meetings: int
    new_offline_vip: int
    online_commission: Decimal
    offline_performance: Decimal


class StoreReportMonthly(BaseModel):
    store_id: int
    months: list[StoreReportMonthlyRow]

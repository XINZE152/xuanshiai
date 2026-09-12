"""Activity management contracts for the back office."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ActivityAdminCreate(BaseModel):
    title: str = Field(min_length=1, max_length=128)
    cover: str | None = Field(default=None, max_length=255)
    type: str | None = Field(default=None, max_length=64)
    city: str | None = Field(default=None, max_length=64)
    address: str | None = Field(default=None, max_length=255)
    start_time: datetime
    end_time: datetime
    signup_deadline: datetime | None = None
    max_people: int = Field(default=0, ge=0, le=100000)
    price: float = Field(default=0, ge=0, le=1000000)
    description: str | None = Field(default=None, max_length=10000)
    time_text: str | None = Field(default=None, max_length=128)
    # ── 后台「发布活动」抽屉补充字段 ──
    organizer: str | None = Field(default=None, max_length=128)
    cover_small: str | None = Field(default=None, max_length=255)
    fee_name: str | None = Field(default="报名费", max_length=64)
    price_male: float = Field(default=0, ge=0, le=1000000)
    price_female: float = Field(default=0, ge=0, le=1000000)
    signup_mode: Literal["anyone", "member"] = "anyone"
    require_realname: bool = False
    limit_mode: Literal["gender", "total"] = "gender"
    max_male: int = Field(default=0, ge=0, le=100000)
    max_female: int = Field(default=0, ge=0, le=100000)
    virtual_people: int = Field(default=0, ge=0, le=1000000)
    virtual_female: int = Field(default=0, ge=0, le=1000000)
    hide_signup_count: bool = False
    reward_promoter: float = Field(default=0, ge=0, le=1000000)
    reward_service: float = Field(default=0, ge=0, le=1000000)
    reward_partner: float = Field(default=0, ge=0, le=1000000)
    reminder_html: str | None = Field(default=None, max_length=10000)
    service_wechat: str | None = Field(default=None, max_length=64)
    service_qr: str | None = Field(default=None, max_length=255)
    virtual_views: int = Field(default=0, ge=0, le=100000000)
    sort_order: int = Field(default=0, ge=-1000000, le=1000000)
    custom_share: bool = False
    manager_ids: str | None = Field(default=None, max_length=255)
    notify_phones: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_times(self) -> "ActivityAdminCreate":
        if self.end_time <= self.start_time:
            raise ValueError("end_time 必须晚于 start_time")
        if self.signup_deadline and self.signup_deadline > self.start_time:
            raise ValueError("signup_deadline 不能晚于 start_time")
        return self


class ActivityAdminUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=128)
    cover: str | None = Field(default=None, max_length=255)
    type: str | None = Field(default=None, max_length=64)
    city: str | None = Field(default=None, max_length=64)
    address: str | None = Field(default=None, max_length=255)
    start_time: datetime | None = None
    end_time: datetime | None = None
    signup_deadline: datetime | None = None
    max_people: int | None = Field(default=None, ge=0, le=100000)
    price: float | None = Field(default=None, ge=0, le=1000000)
    description: str | None = Field(default=None, max_length=10000)
    time_text: str | None = Field(default=None, max_length=128)
    organizer: str | None = Field(default=None, max_length=128)
    cover_small: str | None = Field(default=None, max_length=255)
    fee_name: str | None = Field(default=None, max_length=64)
    price_male: float | None = Field(default=None, ge=0, le=1000000)
    price_female: float | None = Field(default=None, ge=0, le=1000000)
    signup_mode: Literal["anyone", "member"] | None = None
    require_realname: bool | None = None
    limit_mode: Literal["gender", "total"] | None = None
    max_male: int | None = Field(default=None, ge=0, le=100000)
    max_female: int | None = Field(default=None, ge=0, le=100000)
    virtual_people: int | None = Field(default=None, ge=0, le=1000000)
    virtual_female: int | None = Field(default=None, ge=0, le=1000000)
    hide_signup_count: bool | None = None
    reward_promoter: float | None = Field(default=None, ge=0, le=1000000)
    reward_service: float | None = Field(default=None, ge=0, le=1000000)
    reward_partner: float | None = Field(default=None, ge=0, le=1000000)
    reminder_html: str | None = Field(default=None, max_length=10000)
    service_wechat: str | None = Field(default=None, max_length=64)
    service_qr: str | None = Field(default=None, max_length=255)
    virtual_views: int | None = Field(default=None, ge=0, le=100000000)
    sort_order: int | None = Field(default=None, ge=-1000000, le=1000000)
    custom_share: bool | None = None
    manager_ids: str | None = Field(default=None, max_length=255)
    notify_phones: str | None = Field(default=None, max_length=128)
    audit_status: Literal["pending", "approved", "rejected"] | None = None
    online: bool | None = None

    @model_validator(mode="after")
    def require_update(self) -> "ActivityAdminUpdate":
        if not self.model_dump(exclude_unset=True):
            raise ValueError("至少提供一个需要修改的字段")
        return self


class ActivityStatusUpdate(BaseModel):
    status: Literal[1, 2, 3, 4, 5]
    reason: str | None = Field(default=None, max_length=255)


class ActivityAdminItem(BaseModel):
    id: int
    title: str
    cover: str | None
    type: str | None
    city: str | None
    address: str | None
    start_time: datetime
    end_time: datetime
    signup_deadline: datetime | None
    max_people: int
    current_people: int
    price: float
    status: int
    description: str | None
    created_by: int | None
    created_at: datetime
    # M7 后台新增字段（旧行 NULL 时由服务层回退默认值）
    organizer: str | None = None
    time_text: str | None = None
    cover_small: str | None = None
    fee_name: str = "报名费"
    price_male: float = 0
    price_female: float = 0
    signup_mode: str = "anyone"
    require_realname: bool = False
    limit_mode: str = "gender"
    max_male: int = 0
    max_female: int = 0
    virtual_people: int = 0
    virtual_female: int = 0
    hide_signup_count: bool = False
    reward_promoter: float = 0
    reward_service: float = 0
    reward_partner: float = 0
    reminder_html: str | None = None
    service_wechat: str | None = None
    service_qr: str | None = None
    virtual_views: int = 0
    sort_order: int = 0
    custom_share: bool = False
    manager_ids: str | None = None
    notify_phones: str | None = None
    online: bool = True
    audit_status: str = "approved"
    male_count: int = 0
    female_count: int = 0
    link_url: str | None = None


class ActivityAdminPage(BaseModel):
    items: list[ActivityAdminItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class ActivitySignupAdminItem(BaseModel):
    id: int
    activity_id: int
    activity_title: str | None = None
    user_id: int
    nickname: str | None
    real_name: str | None
    phone: str | None
    remark: str | None
    status: Literal[0, 1, 2, 3]
    cancel_reason: str | None
    created_at: datetime
    updated_at: datetime
    # M7 报名资料快照与运营字段
    gender: str | None = None
    age: int | None = None
    height: int | None = None
    education: str | None = None
    income: str | None = None
    marriage_status: str | None = None
    company: str | None = None
    avatar: str | None = None
    id_card: str | None = None
    is_member: bool = False
    is_realname: bool = False
    signup_times: int = 1
    pay_status: str = "free"
    pay_amount: float = 0
    checked_in: bool = False
    in_crm: bool = False
    promoter_id: int | None = None
    promoter_name: str | None = None


class ActivitySignupAdminPage(BaseModel):
    items: list[ActivitySignupAdminItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class ActivitySignupStatistics(BaseModel):
    total: int = 0
    first_signup: int = 0
    pending: int = 0
    approved: int = 0
    rejected: int = 0
    fee_amount: str = "0.00"
    not_checked_in: int = 0
    checked_in: int = 0
    in_crm: int = 0


class ActivitySignupUpdate(BaseModel):
    real_name: str | None = Field(default=None, max_length=64)
    phone: str | None = Field(default=None, max_length=20)
    gender: str | None = Field(default=None, max_length=8)
    age: int | None = Field(default=None, ge=0, le=120)
    height: int | None = Field(default=None, ge=0, le=300)
    education: str | None = Field(default=None, max_length=32)
    income: str | None = Field(default=None, max_length=32)
    marriage_status: str | None = Field(default=None, max_length=32)
    company: str | None = Field(default=None, max_length=128)
    avatar: str | None = Field(default=None, max_length=255)
    id_card: str | None = Field(default=None, max_length=32)
    remark: str | None = Field(default=None, max_length=255)
    status: Literal[0, 1, 2, 3] | None = None
    pay_status: Literal["free", "paid", "unpaid"] | None = None
    pay_amount: float | None = Field(default=None, ge=0, le=1000000)
    checked_in: bool | None = None
    in_crm: bool | None = None
    promoter_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def require_update(self) -> "ActivitySignupUpdate":
        if not self.model_dump(exclude_unset=True):
            raise ValueError("至少提供一个需要修改的字段")
        return self


class ActivityOption(BaseModel):
    id: int
    title: str


class ActivityLinkInfo(BaseModel):
    link_url: str
    qr_code: str | None = None


class ActivitySignupStatusUpdate(BaseModel):
    status: Literal[1, 2, 3]
    reason: str | None = Field(default=None, max_length=255)


from datetime import datetime

from pydantic import BaseModel, Field


class MembershipDailyLimits(BaseModel):
    apply: int = Field(description="每日申请认识上限")
    browse: int = Field(description="每日可查看的不同用户上限")
    superlike: int = Field(description="每日爆灯上限")


class MembershipPackage(BaseModel):
    code: str
    name: str
    duration_days: int
    price: float
    original_price: float | None
    daily_price: float | None
    badge: str | None
    rights: dict[str, int | bool | str | None]
    daily_limits: MembershipDailyLimits = Field(description="套餐生效后的每日额度预览")


class MembershipPeriod(BaseModel):
    is_vip: bool
    package_type: str | None
    start_at: datetime | None
    end_at: datetime | None
    rights: dict[str, int | bool | str | None]


class MembershipStatus(MembershipPeriod):
    daily_limits: MembershipDailyLimits = Field(description="当前账号的实际每日额度上限")


class MembershipHistoryItem(MembershipPeriod):
    id: int
    amount: float | None
    order_no: str | None
    status: int


class MembershipHistoryPage(BaseModel):
    items: list[MembershipHistoryItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class CreateMembershipOrderRequest(BaseModel):
    package_code: str = Field(min_length=1, max_length=32)


class MembershipOrderResponse(BaseModel):
    order_no: str
    package_code: str
    product_name: str
    amount: float
    pay_type: int
    status: int
    expire_at: datetime | None
    payment_required: bool


class WechatPaymentCallback(BaseModel):
    order_no: str = Field(min_length=1, max_length=64)
    transaction_id: str = Field(min_length=1, max_length=64)
    signature: str = Field(min_length=1, max_length=512)
    # D-21：金额一致性校验所需字段（real 模式必填；mock 模式可省略）。
    amount: float | None = Field(default=None, ge=0)

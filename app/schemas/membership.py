from datetime import datetime

from pydantic import BaseModel, Field


class MembershipPackage(BaseModel):
    code: str
    name: str
    duration_days: int
    price: float
    original_price: float | None
    daily_price: float | None
    badge: str | None
    rights: dict[str, int | bool | str | None]


class MembershipStatus(BaseModel):
    is_vip: bool
    package_type: str | None
    start_at: datetime | None
    end_at: datetime | None
    rights: dict[str, int | bool | str | None]


class MembershipHistoryItem(MembershipStatus):
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

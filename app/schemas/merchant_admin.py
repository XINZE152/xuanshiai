"""Merchant alliance (商家联盟) contracts for the back office (M7-B)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ─────────────────────────── 商家分类 ───────────────────────────


class MerchantCategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    icon_url: str | None = Field(default=None, max_length=255)
    sort: int = Field(default=0, ge=-1000000, le=1000000)
    status: int = Field(default=1, ge=0, le=1)


class MerchantCategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    icon_url: str | None = Field(default=None, max_length=255)
    sort: int | None = Field(default=None, ge=-1000000, le=1000000)
    status: int | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def require_update(self) -> "MerchantCategoryUpdate":
        if not self.model_dump(exclude_unset=True):
            raise ValueError("至少提供一个需要修改的字段")
        return self


class MerchantCategoryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    icon_url: str | None = None
    sort: int = 0
    status: int = 1
    merchant_count: int = 0


class MerchantCategoryOrder(BaseModel):
    ids: list[int] = Field(min_length=1)


# ─────────────────────────── 商家 ───────────────────────────


class MerchantBase(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    cover: str | None = Field(default=None, max_length=255)
    gallery: list[str] = Field(default_factory=list)
    category_id: int | None = Field(default=None, ge=1)
    tags: list[str] = Field(default_factory=list)
    province: str | None = Field(default=None, max_length=64)
    city: str | None = Field(default=None, max_length=64)
    address: str | None = Field(default=None, max_length=255)
    contact_phone: str | None = Field(default=None, max_length=32)
    business_hours: str | None = Field(default=None, max_length=128)
    intro: str | None = Field(default=None, max_length=20000)
    admin_user_id: int | None = Field(default=None, ge=1)
    sort: int = Field(default=0, ge=-1000000, le=1000000)
    visible: bool = True


class MerchantCreate(MerchantBase):
    pass


class MerchantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    cover: str | None = Field(default=None, max_length=255)
    gallery: list[str] | None = None
    category_id: int | None = Field(default=None, ge=1)
    tags: list[str] | None = None
    province: str | None = Field(default=None, max_length=64)
    city: str | None = Field(default=None, max_length=64)
    address: str | None = Field(default=None, max_length=255)
    contact_phone: str | None = Field(default=None, max_length=32)
    business_hours: str | None = Field(default=None, max_length=128)
    intro: str | None = Field(default=None, max_length=20000)
    admin_user_id: int | None = Field(default=None, ge=1)
    sort: int | None = Field(default=None, ge=-1000000, le=1000000)
    visible: bool | None = None

    @model_validator(mode="after")
    def require_update(self) -> "MerchantUpdate":
        if not self.model_dump(exclude_unset=True):
            raise ValueError("至少提供一个需要修改的字段")
        return self


class MerchantItem(MerchantBase):
    id: int
    category_name: str | None = None
    admin_user_nickname: str | None = None
    product_total: int = 0
    product_online: int = 0
    sales_amount: str = "0.00"
    verify_staff: str | None = None
    created_at: datetime | None = None
    link_url: str | None = None
    qr_code: str | None = None


class MerchantPage(BaseModel):
    items: list[MerchantItem]
    page: int
    page_size: int
    total: int
    has_more: bool


# ─────────────────────────── 商品 ───────────────────────────

SplitMode = Literal["fixed", "by_level"]


class MerchantProductBase(BaseModel):
    merchant_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=128)
    cover: str | None = Field(default=None, max_length=255)
    original_price: float = Field(default=0, ge=0, le=10000000)
    sale_price: float = Field(default=0, ge=0, le=10000000)
    settle_amount: float = Field(default=0, ge=0, le=10000000)
    promote_split_mode: SplitMode = "fixed"
    promote_amount: float = Field(default=0, ge=0, le=10000000)
    partner_split_mode: SplitMode = "fixed"
    partner_amount: float = Field(default=0, ge=0, le=10000000)
    service_amount: float = Field(default=0, ge=0, le=10000000)
    buy_limit_mode: Literal["account", "order"] = "account"
    account_limit: int = Field(default=0, ge=0, le=100000)
    order_limit: int = Field(default=0, ge=0, le=100000)
    notice_mode: Literal["default", "custom"] = "default"
    notice_text: str | None = Field(default=None, max_length=5000)
    intro: str | None = Field(default=None, max_length=20000)
    status: int = Field(default=1, ge=1, le=2)
    sort: int = Field(default=0, ge=-1000000, le=1000000)


class MerchantProductCreate(MerchantProductBase):
    pass


class MerchantProductUpdate(BaseModel):
    merchant_id: int | None = Field(default=None, ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    cover: str | None = Field(default=None, max_length=255)
    original_price: float | None = Field(default=None, ge=0, le=10000000)
    sale_price: float | None = Field(default=None, ge=0, le=10000000)
    settle_amount: float | None = Field(default=None, ge=0, le=10000000)
    promote_split_mode: SplitMode | None = None
    promote_amount: float | None = Field(default=None, ge=0, le=10000000)
    partner_split_mode: SplitMode | None = None
    partner_amount: float | None = Field(default=None, ge=0, le=10000000)
    service_amount: float | None = Field(default=None, ge=0, le=10000000)
    buy_limit_mode: Literal["account", "order"] | None = None
    account_limit: int | None = Field(default=None, ge=0, le=100000)
    order_limit: int | None = Field(default=None, ge=0, le=100000)
    notice_mode: Literal["default", "custom"] | None = None
    notice_text: str | None = Field(default=None, max_length=5000)
    intro: str | None = Field(default=None, max_length=20000)
    status: int | None = Field(default=None, ge=1, le=2)
    sort: int | None = Field(default=None, ge=-1000000, le=1000000)

    @model_validator(mode="after")
    def require_update(self) -> "MerchantProductUpdate":
        if not self.model_dump(exclude_unset=True):
            raise ValueError("至少提供一个需要修改的字段")
        return self


class MerchantProductItem(MerchantProductBase):
    id: int
    merchant_name: str | None = None
    sales_count: int = 0
    sales_amount: str = "0.00"
    create_time: datetime | None = None
    created_at: datetime | None = None
    link_url: str | None = None
    qr_code: str | None = None


class MerchantProductPage(BaseModel):
    items: list[MerchantProductItem]
    page: int
    page_size: int
    total: int
    has_more: bool


# ─────────────────────────── 订单 ───────────────────────────


class MerchantOrderItem(BaseModel):
    id: int
    order_no: str
    product_id: int
    product_name: str | None = None
    product_cover: str | None = None
    merchant_id: int
    merchant_name: str | None = None
    buyer_user_id: int
    buyer_nickname: str | None = None
    buyer_phone: str | None = None
    quantity: int = 1
    amount: str = "0.00"
    pay_status: str = "unpaid"
    pay_method: str | None = None
    paid_at: datetime | None = None
    verify_status: str = "pending"
    verify_code: str | None = None
    verified_at: datetime | None = None
    status: str = "pending"
    remark: str | None = None
    created_at: datetime | None = None


class MerchantOrderPage(BaseModel):
    items: list[MerchantOrderItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class MerchantOrderStatusUpdate(BaseModel):
    status: Literal["pending", "paid", "used", "cancelled"]
    remark: str | None = Field(default=None, max_length=255)


class MerchantOption(BaseModel):
    id: int
    label: str

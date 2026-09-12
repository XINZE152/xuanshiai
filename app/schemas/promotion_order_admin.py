"""会员服务-推广管理（推广服务订单）后台契约。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

PayStatus = Literal["unpaid", "paid", "refunded"]
OrderStatus = Literal["pending", "processing", "done", "cancelled"]


class PromotionOrder(BaseModel):
    id: int
    order_no: str
    user_id: int
    user_nickname: str | None = None
    product_name: str
    amount: str = "0.00"
    pay_status: PayStatus = "unpaid"
    pay_method: str | None = None
    status: OrderStatus = "pending"
    remark: str | None = None
    paid_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class PromotionOrderPage(BaseModel):
    items: list[PromotionOrder]
    page: int
    page_size: int
    total: int
    has_more: bool


class PromotionOrderUpdate(BaseModel):
    pay_status: PayStatus | None = None
    pay_method: str | None = Field(default=None, max_length=32)
    status: OrderStatus | None = None
    remark: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def require_change(self) -> "PromotionOrderUpdate":
        if all(value is None for value in (self.pay_status, self.pay_method, self.status, self.remark)):
            raise ValueError("至少提供一个需要修改的字段")
        return self


class PromotionOrderStatistics(BaseModel):
    total: int = 0
    paid_count: int = 0
    paid_amount: str = "0.00"
    processing_count: int = 0

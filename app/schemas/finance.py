"""订单、分成、账本和提现接口契约。"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class CommissionRuleCreate(BaseModel):
    beneficiary_type: Literal["service_matchmaker", "store", "promoter", "partner"]
    name: str = Field(min_length=1, max_length=128)
    mode: Literal["fixed", "rate"]
    fixed_amount: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    rate_percent: Decimal | None = Field(default=None, ge=0, le=100, decimal_places=4)
    priority: int = Field(default=0, ge=0, le=100000)

    @model_validator(mode="after")
    def validate_mode(self) -> "CommissionRuleCreate":
        if self.mode == "fixed" and self.fixed_amount is None:
            raise ValueError("固定金额规则必须填写 fixed_amount")
        if self.mode == "rate" and self.rate_percent is None:
            raise ValueError("比例规则必须填写 rate_percent")
        return self


class CommissionRuleResponse(BaseModel):
    id: int
    beneficiary_type: str
    name: str
    mode: str
    fixed_amount: Decimal | None
    rate_percent: Decimal | None
    priority: int
    version: int
    status: Literal[1, 2]
    created_at: datetime


class ProductCommissionConfigCreate(BaseModel):
    beneficiary_type: Literal["service_matchmaker", "store", "promoter", "partner"]
    mode: Literal["fixed", "rate"]
    fixed_amount: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    rate_percent: Decimal | None = Field(default=None, ge=0, le=100, decimal_places=4)

    @model_validator(mode="after")
    def validate_mode(self) -> "ProductCommissionConfigCreate":
        if self.mode == "fixed" and self.fixed_amount is None:
            raise ValueError("固定金额配置必须填写 fixed_amount")
        if self.mode == "rate" and self.rate_percent is None:
            raise ValueError("比例配置必须填写 rate_percent")
        return self


class ProductCommissionConfigResponse(BaseModel):
    id: int
    product_id: int
    beneficiary_type: str
    mode: str
    fixed_amount: Decimal | None
    rate_percent: Decimal | None
    version: int
    status: Literal[1, 2]
    created_at: datetime


class FinanceOrderCreate(BaseModel):
    product_type: int = Field(ge=1, le=32)
    product_name: str = Field(min_length=1, max_length=128)
    amount: Decimal = Field(gt=0, le=1000000, decimal_places=2)


class PaymentOrderResponse(BaseModel):
    id: int
    order_no: str
    user_id: int
    product_type: int
    product_name: str
    amount: Decimal
    status: Literal[0, 1, 2, 3]
    pay_time: datetime | None
    created_at: datetime


class CommissionEntryResponse(BaseModel):
    id: int
    order_id: int
    beneficiary_type: str
    beneficiary_id: int
    base_amount: Decimal
    amount: Decimal
    status: str
    created_at: datetime


class FinanceReportRow(BaseModel):
    beneficiary_type: str
    beneficiary_id: int
    order_count: int
    total_amount: Decimal
    pending_amount: Decimal
    available_amount: Decimal


class PaymentOrderAdminPage(BaseModel):
    items: list["PaymentOrderResponse"]
    page: int
    page_size: int
    total: int
    has_more: bool


class WithdrawalAdminPage(BaseModel):
    items: list["WithdrawalResponse"]
    page: int
    page_size: int
    total: int
    has_more: bool


class LedgerEntryResponse(BaseModel):
    id: int
    account_type: str
    account_id: int
    direction: str
    amount: Decimal
    state: str
    source_type: str
    source_id: int
    idempotency_key: str
    created_at: datetime


class LedgerEntryPage(BaseModel):
    items: list[LedgerEntryResponse]
    page: int
    page_size: int
    total: int
    has_more: bool


class AccountBalanceResponse(BaseModel):
    account_type: str
    account_id: int
    pending_amount: Decimal
    available_amount: Decimal


class WithdrawalCreate(BaseModel):
    amount: Decimal = Field(gt=0, le=1000000, decimal_places=2)
    payee_masked: str = Field(min_length=1, max_length=128)


class WithdrawalResponse(BaseModel):
    id: int
    account_type: str
    account_id: int
    amount: Decimal
    status: str
    payee_masked: str | None
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime


class WithdrawalReview(BaseModel):
    status: Literal["APPROVED", "REJECTED", "PROCESSING", "SUCCEEDED", "FAILED"]
    failure_reason: str | None = Field(default=None, max_length=255)


class FinanceRefundRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=255)


# ============================================
# 后台「红娘线上分成明细」页契约
# ============================================


class FinanceDailyRow(BaseModel):
    """按日聚合的订单收入/退款报表行（依据 payment_order 的支付时间）。"""

    date: str
    pay_count: int
    income_amount: Decimal
    refund_count: int
    refund_amount: Decimal


class CommissionEntryDetailItem(BaseModel):
    """详情页单条明细：含关联订单/会员/红娘/事件，金额字段以 str 序列化避免精度丢失。"""

    id: int
    created_at: datetime
    store_name: str = Field(default="总店", description="总店红娘后台统一显示『总店』")
    matchmaker_id: int
    matchmaker_name: str
    matchmaker_avatar: str | None = None
    consumer_id: int
    consumer_name: str
    consumer_phone: str | None = None
    consumer_avatar: str | None = None
    event_name: str
    beneficiary_type: str
    order_id: int
    order_no: str | None = None
    consumer_amount: Decimal
    commission_amount: Decimal
    status: str


class CommissionEntryDetailPage(BaseModel):
    items: list[CommissionEntryDetailItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class MatchmakerOption(BaseModel):
    """详情页筛选用的红娘下拉项。"""

    id: int
    name: str
    avatar: str | None = None


class EventOption(BaseModel):
    """详情页筛选用的事件下拉项。"""

    id: int = Field(description="commission_rule.id")
    name: str
    beneficiary_type: str


class CommissionEntryDetailOptions(BaseModel):
    """详情页一次性返回筛选下拉选项。"""

    matchmakers: list[MatchmakerOption] = Field(default_factory=list)
    events: list[EventOption] = Field(default_factory=list)

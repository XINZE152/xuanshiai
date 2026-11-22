from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class PointsSummary(BaseModel):
    balance: int
    total_earned: int
    total_spent: int


class PointLedgerItem(BaseModel):
    id: int
    type: int
    amount: int
    balance: int
    description: str | None
    created_at: datetime


class PointLedgerPage(BaseModel):
    items: list[PointLedgerItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class CheckinResponse(BaseModel):
    checked_in: bool
    points: int
    balance: int
    checkin_date: str


class TaskItem(BaseModel):
    task_code: str
    title: str
    reward: int
    status: int
    completed_at: datetime | None


class ClaimTaskResponse(BaseModel):
    task_code: str
    claimed: bool
    points: int
    balance: int


class InviteItem(BaseModel):
    id: int
    invitee_id: int
    status: int
    register_rewarded: bool
    realname_rewarded: bool
    created_at: datetime


class InvitePage(BaseModel):
    items: list[InviteItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class PointProduct(BaseModel):
    code: str
    name: str
    product_type: str
    points_cost: int
    value: str | None
    stock: int | None


class RedeemRequest(BaseModel):
    product_code: str
    count: int = Field(
        default=1,
        ge=1,
        le=10,
        description="本次兑换数量，1~10；总价 = 单价 × count",
    )


class RedeemResponse(BaseModel):
    order_no: str
    product_code: str
    product_name: str
    points_cost: int
    count: int = 1
    status: int
    balance: int


class PointRechargePackage(BaseModel):
    """人民币积分充值商品（admin_recharge_item，resource_type='points'）。"""

    id: int
    name: str
    points: int = Field(description="到账积分数")
    price: Decimal = Field(description="售价（元），服务端定价，客户端不可传")


class AdminPointRechargePackage(BaseModel):
    id: int | None = Field(default=None, ge=1)
    name: str = Field(min_length=1, max_length=128)
    points: int = Field(ge=1, le=2_147_483_647)
    price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    enabled: bool = True
    sort: int = Field(default=0, ge=0, le=100000)


class AdminPointRechargePackageList(BaseModel):
    items: list[AdminPointRechargePackage]


class CreatePointRechargeOrderRequest(BaseModel):
    package_id: int = Field(ge=1, description="充值商品 ID；数量固定 1，金额由服务端按目录计算")

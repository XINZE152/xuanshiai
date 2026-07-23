from datetime import datetime

from pydantic import BaseModel


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


class RedeemResponse(BaseModel):
    order_no: str
    product_code: str
    product_name: str
    points_cost: int
    status: int
    balance: int

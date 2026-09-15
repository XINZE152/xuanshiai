"""账号注销申请 后台契约。

前端 /reg-user-cancel 页面（账号管理 → 注销申请）使用。注销申请的真实业务
流是「用户申请 → 后台审核 → 批准或撤销」，期间 `users.status` 始终保持 1
正常状态，仅在后台批准时才真正置 3 注销并写入 `users.deletion_*` 字段。
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


CancellationStatus = Literal["pending", "approved", "cancelled"]
LinkKind = Literal["member_profile", "promoter", "partner", "matchmaker"]


class CancellationLinkItem(BaseModel):
    """单条关联信息快照，避免列表里再次联表。"""

    kind: LinkKind
    label: str = Field(min_length=1, max_length=32)
    detail: str | None = Field(default=None, max_length=128)


class UserCancellationItem(BaseModel):
    id: int
    user_id: int
    nickname: str | None = None
    phone: str | None = None
    requested_ip: str | None = None
    reason: str | None = None
    status: CancellationStatus
    has_member_profile: bool = False
    has_promoter_link: bool = False
    has_partner_link: bool = False
    has_matchmaker_link: bool = False
    reviewed_by: int | None = None
    reviewer_name: str | None = None
    reviewed_at: datetime | None = None
    review_note: str | None = None
    created_at: datetime
    updated_at: datetime


class UserCancellationPage(BaseModel):
    items: list[UserCancellationItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class UserCancellationReview(BaseModel):
    """后台审核动作：批准（注销）或取消注销。

    approve=true → 走注销流；approve=false → 走取消注销流。
    reason 字段可填后台批注（前端显示在「批注」列）。
    """

    approve: bool
    note: str | None = Field(default=None, max_length=500)


class UserCancellationStatistics(BaseModel):
    pending: int = 0
    approved_today: int = 0
    cancelled_today: int = 0

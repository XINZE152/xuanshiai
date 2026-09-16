"""后台账号注销申请 契约。

前端「平台账号 → 账号管理 → 删除账号」提交的注销申请，以及「注销申请」
审核页（/reg-user-cancel）消费。业务流：删除账号（DELETE）→ 生成 pending
申请并暂停该账号登录 → 审核页「确定注销」真正注销 /「取消注销」恢复登录。
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


CancellationStatus = Literal["pending", "approved", "cancelled"]


class AdminAccountCancellationItem(BaseModel):
    id: int
    account_id: int
    username: str
    display_name: str
    linked_user_id: int | None = None
    requested_ip: str | None = None
    reason: str | None = None
    status: CancellationStatus
    previous_status: int = 1
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


class AdminAccountCancellationPage(BaseModel):
    items: list[AdminAccountCancellationItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class AdminAccountCancellationReview(BaseModel):
    """审核动作：approve=true 确定注销；approve=false 取消注销。"""

    approve: bool
    note: str | None = Field(default=None, max_length=500)

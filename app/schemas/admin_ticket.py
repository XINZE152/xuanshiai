"""平台工单反馈 后台契约。

前端 /system-feedback 页面使用。后台账号提交 BUG/咨询/建议，进入
处理池；管理员可回复并标记处理状态。前端表格展示「工单号 / 类型 / 反馈时间 /
状态 / 答复时间」，点击查看弹出详情/回复面板。
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


FeedbackType = Literal["BUG", "咨询", "建议"]
TicketStatus = Literal["待处理", "处理中", "已处理"]


class AdminTicketItem(BaseModel):
    id: int
    ticket_no: str
    submitter_user_id: int | None = None
    submitter_name: str | None = None
    feedback_type: FeedbackType
    title: str
    content: str
    status: TicketStatus
    reply_content: str | None = None
    replied_by: int | None = None
    replier_name: str | None = None
    replied_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AdminTicketPage(BaseModel):
    items: list[AdminTicketItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class AdminTicketCreate(BaseModel):
    """后台账号提交工单。"""

    feedback_type: FeedbackType = "咨询"
    title: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=4000)


class AdminTicketReply(BaseModel):
    """回复工单（同时把 status 推到「已处理」）。"""

    reply_content: str = Field(min_length=1, max_length=4000)


class AdminTicketStatusUpdate(BaseModel):
    """仅更新状态（用于「处理中」流转）。"""

    status: TicketStatus

    @model_validator(mode="after")
    def _validate(self) -> "AdminTicketStatusUpdate":
        if self.status not in {"待处理", "处理中", "已处理"}:
            raise ValueError("不支持的工单状态")
        return self


class AdminTicketStatistics(BaseModel):
    pending: int = 0
    processing: int = 0
    done: int = 0

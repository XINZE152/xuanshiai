"""Member CRM follow-up and behavior contracts."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class MemberFollowUpCreate(BaseModel):
    method: Literal["PHONE", "WECHAT", "VISIT", "OTHER"]
    content: str = Field(min_length=1, max_length=2000)
    next_follow_at: datetime | None = None


class MemberFollowUp(BaseModel):
    id: int
    user_id: int
    method: str
    content: str
    next_follow_at: datetime | None
    created_by: int
    created_at: datetime


class MemberFollowUpPage(BaseModel):
    items: list[MemberFollowUp]
    page: int
    page_size: int
    total: int
    has_more: bool


class MemberFollowUpRow(BaseModel):
    """跟进全览列表行：与前端「跟进全览」表格列（跟进会员/跟进红娘/跟进时间/跟进内容/操作）对齐。"""

    id: int
    user_id: int
    member_code: str = Field(description="会员编号：G + user_id 左补零到 6 位")
    nickname: str | None = None
    avatar: str | None = None
    matchmaker_name: str = Field(description="跟进红娘称呼，取后台账号 display_name，取不到回退 admin")
    method: str = Field(description="PHONE/WECHAT/VISIT/OTHER")
    method_label: str = Field(description="跟进方式中文：电话/微信/面谈/其他")
    content: str
    note: str | None = Field(default=None, description="系统提示文案；当前数据源无标记列，恒为 null")
    intention_level: int | None = Field(default=None, description="客户意向 1低 2中 3高")
    created_at: datetime | None = None


class MemberFollowUpListPage(BaseModel):
    items: list[MemberFollowUpRow]
    page: int
    page_size: int
    total: int
    has_more: bool


class MemberFollowUpSummary(BaseModel):
    """跟进全览 8 个时间统计卡计数，与前端统计卡顺序一一对应。"""

    all: int = 0
    today: int = 0
    yesterday: int = 0
    three_days: int = 0
    this_week: int = 0
    last_week: int = 0
    this_month: int = 0
    last_month: int = 0


class MemberFollowUpImportResult(BaseModel):
    """历史跟进批量导入结果。"""

    created: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = Field(default_factory=list)


class MemberBehaviorItem(BaseModel):
    event_type: Literal["login", "browse", "favorite", "swipe", "apply"]
    event_id: int
    target_user_id: int | None
    target_nickname: str | None
    detail: str | None
    occurred_at: datetime


class MemberBehaviorPage(BaseModel):
    items: list[MemberBehaviorItem]
    page: int
    page_size: int
    total: int
    has_more: bool


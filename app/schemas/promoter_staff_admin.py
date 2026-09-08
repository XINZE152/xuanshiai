"""Contracts for the 推广红娘 management back-office page.

身份模型：推广红娘复用 `user_matchmaker_apply (application_type='promoter')` 记录，
列表/详情以 `users.id`（user_id）为主键，展示昵称/头像/手机号取 `users` 表，
推广渠道/红娘类型/口号/级别/3 个权限开关取 `user_matchmaker_apply`，
推广会员数/分享码数分别聚合 `promotion_attribution` / `promotion_touch`。
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

MatchmakerType = Literal["part_time", "full_time"]


class PromoterUserCandidate(BaseModel):
    """添加推广红娘时搜索的普通用户候选人。"""

    id: int
    nickname: str | None = None
    phone: str | None = None
    avatar: str | None = None


class PromoterStaffItem(BaseModel):
    """列表行：与前端列（编号/姓名/手机号/推广渠道/推广会员数/入职时间/状态）对齐。"""

    id: int = Field(description="行主键，等于 user_id（普通用户 ID）")
    user_id: int
    avatar: str | None = None
    display_name: str = Field(description="展示名：user_matchmaker_apply.real_name 优先，回退 users.nickname")
    phone: str | None = None
    channel: str | None = Field(default=None, description="推广渠道")
    member_count: int = Field(description="推广会员数（有效推广归属数）")
    touch_count: int = Field(default=0, description="推广分享码数")
    status: Literal[1, 2] = Field(description="1 在职 2 离职")
    status_label: str
    reviewed_at: datetime | None = Field(default=None, description="入职时间（审核通过时间）")
    intro: str | None = None
    created_at: datetime | None = None


class PromoterStaffPage(BaseModel):
    items: list[PromoterStaffItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class PromoterStaffDetail(PromoterStaffItem):
    """详情/编辑回显：列表字段 + 审核相关 + 业务配置（类型/口号/级别/3 权限）。"""

    real_name: str | None = None
    suspension_reason: str | None = Field(default=None, description="离职原因（status=2 时）")
    matchmaker_type: MatchmakerType | None = Field(default=None, description="part_time 兼职 / full_time 全职")
    slogan: str | None = None
    commission_level_id: int | None = Field(default=None, description="推广红娘分成级别（1 初级 / 2 推广大师 / 3 推广大使 / 4 推广天使）")
    can_view_lead_follow: bool = True
    can_write_lead_follow: bool = True
    can_view_member_crm_follow: bool = True


class PromoterStaffCreate(BaseModel):
    """后台添加推广红娘：绑定普通用户（必填其一：user_id 或 lookup）。"""

    user_id: int | None = Field(default=None, ge=1)
    lookup: str | None = Field(default=None, min_length=2, max_length=100)
    lookup_by: Literal["nickname", "phone"] = "nickname"
    channel: str | None = Field(default=None, max_length=64)
    intro: str | None = Field(default=None, max_length=500)
    matchmaker_type: MatchmakerType | None = None
    slogan: str | None = Field(default=None, max_length=128)
    commission_level_id: int | None = Field(default=None, ge=1, le=4, description="1 初级 / 2 推广大师 / 3 推广大使 / 4 推广天使")
    can_view_lead_follow: bool = True
    can_write_lead_follow: bool = True
    can_view_member_crm_follow: bool = True


class PromoterStaffUpdate(BaseModel):
    """编辑推广红娘。status=2 表示离职，status=1 表示复职。"""

    channel: str | None = Field(default=None, max_length=64)
    intro: str | None = Field(default=None, max_length=500)
    display_name: str | None = Field(default=None, min_length=1, max_length=64)
    phone: str | None = Field(default=None, max_length=20)
    status: Literal[1, 2] | None = None
    reason: str | None = Field(default=None, max_length=255, description="离职/复职原因")
    matchmaker_type: MatchmakerType | None = None
    slogan: str | None = Field(default=None, max_length=128)
    commission_level_id: int | None = Field(default=None, ge=1, le=4)
    can_view_lead_follow: bool | None = None
    can_write_lead_follow: bool | None = None
    can_view_member_crm_follow: bool | None = None


class PromoterStatusUpdate(BaseModel):
    """行内快捷操作：离职(status=2) / 复职(status=1)。"""

    status: Literal[1, 2]
    reason: str | None = Field(default=None, max_length=255)

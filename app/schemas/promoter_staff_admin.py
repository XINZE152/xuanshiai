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
    real_name: str | None = None
    phone: str | None = None
    avatar: str | None = None
    wechat_bound: bool = False
    is_service_matchmaker: bool = False
    is_promoter: bool = False
    has_team: bool = False
    unavailable: bool = False
    unavailable_reason: str | None = None


class PromoterStaffItem(BaseModel):
    """列表行：与前端列（编号/推广红娘/隶属团队/分成级别/加入时间/名下会员/录入客源线索/开单明细/是否展示）对齐。"""

    id: int = Field(description="行主键，等于 user_id（普通用户 ID）")
    user_id: int
    avatar: str | None = None
    display_name: str = Field(description="展示名：user_matchmaker_apply.real_name 优先，回退 users.nickname")
    account: str | None = Field(default=None, description="后台账号（users.nickname）")
    phone: str | None = None
    channel: str | None = Field(default=None, description="推广渠道")
    matchmaker_type: MatchmakerType | None = Field(default=None, description="part_time 兼职 / full_time 全职")
    matchmaker_type_label: str = Field(default="—", description="兼职/全职展示文案")
    commission_level_id: int | None = Field(default=None, description="分成级别（1 初级 / 2 推广大师 / 3 推广大使 / 4 推广天使）")
    commission_level_name: str | None = Field(default=None, description="分成级别名称")
    team_id: int | None = Field(default=None, description="隶属团队 ID（partner_team.id）")
    team_name: str | None = Field(default=None, description="隶属团队名称")
    member_count: int = Field(description="名下会员总数（有效推广归属数）")
    member_month: int = Field(default=0, description="本月新增名下会员数")
    lead_total: int = Field(default=0, description="累计录入客源线索数")
    lead_month: int = Field(default=0, description="本月录入客源线索数")
    order_amount: str = Field(default="0.00", description="开单明细金额合计（Decimal 字符串化）")
    touch_count: int = Field(default=0, description="推广分享码数")
    status: Literal[1, 2] = Field(description="1 在职 2 离职")
    status_label: str
    visible: bool = Field(default=True, description="前台是否展示")
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
    slogan: str | None = None
    can_view_lead_follow: bool = True
    can_write_lead_follow: bool = True
    can_view_member_crm_follow: bool = True


class PromoterStatistics(BaseModel):
    """推广红娘统计卡（仅统计在职推广红娘名下数据）。"""

    part_time_count: int = 0
    full_time_count: int = 0
    member_total: int = 0
    member_month: int = 0
    member_last_month: int = 0
    lead_total: int = 0
    lead_month: int = 0
    lead_last_month: int = 0


class PromoterTeamItem(BaseModel):
    """合伙团队下拉项（partner_team）。"""

    id: int
    name: str
    owner_user_id: int | None = None
    owner_name: str | None = None
    status: int | None = None


class PromoterTeamUpdate(BaseModel):
    """变更团队入参：team_id 为空表示移出团队。"""

    team_id: int | None = Field(default=None, ge=1, description="目标团队 ID，传 null 表示移出团队")
    reason: str | None = Field(default=None, max_length=255)


class PromoterCommissionEntryItem(BaseModel):
    """推广红娘分成流水行。"""

    id: int
    created_at: datetime | None = None
    promoter_id: int
    promoter_name: str | None = None
    promoter_avatar: str | None = None
    consumer_id: int | None = None
    consumer_name: str | None = None
    consumer_phone: str | None = None
    event_name: str | None = None
    order_id: int | None = None
    order_no: str | None = None
    base_amount: str = "0.00"
    amount: str = "0.00"
    status: str = "PENDING"


class PromoterCommissionEntryPage(BaseModel):
    items: list[PromoterCommissionEntryItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class PromoterCommissionPromoterOption(BaseModel):
    id: int
    name: str
    avatar: str | None = None


class PromoterCommissionEventOption(BaseModel):
    id: int
    name: str


class PromoterCommissionEntryOptions(BaseModel):
    """分成明细筛选下拉。"""

    promoters: list[PromoterCommissionPromoterOption] = Field(default_factory=list)
    events: list[PromoterCommissionEventOption] = Field(default_factory=list)


class PromoterPosterResponse(BaseModel):
    promoter_id: int
    url: str
    qr_content: str


class PromoterPlatformTokenResponse(BaseModel):
    promoter_id: int
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    jump_url: str


class PromoterDeleteResponse(BaseModel):
    id: int
    deleted: bool


class PromoterStaffCreate(BaseModel):
    """后台添加推广红娘：绑定普通用户（必填其一：user_id 或 lookup）。"""

    user_id: int | None = Field(default=None, ge=1)
    lookup: str | None = Field(default=None, min_length=2, max_length=100)
    lookup_by: Literal["nickname", "phone"] = "nickname"
    phone: str | None = Field(default=None, max_length=20, description="推广红娘联系电话，不传回退绑定用户的手机号")
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
    visible: bool | None = None
    can_view_lead_follow: bool | None = None
    can_write_lead_follow: bool | None = None
    can_view_member_crm_follow: bool | None = None


class PromoterStatusUpdate(BaseModel):
    """行内快捷操作：离职(status=2) / 复职(status=1)。"""

    status: Literal[1, 2]
    reason: str | None = Field(default=None, max_length=255)

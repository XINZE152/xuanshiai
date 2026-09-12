"""Contracts for the 合伙红娘 back-office pages: 合伙人管理 / 团队关系 / 分成明细.

身份模型：合伙人 = `partner_team`（一个 owner_user_id 一个团队，团队名称即合伙人的团队），
其团队成员来自 `partner_membership`（推广红娘 partner_membership.promoter_id）。
分成为 `commission_entry.beneficiary_type='partner'` + `beneficiary_id = 团队 owner_user_id`。
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

PARTNER_LEVELS = (1, 2, 3)

# ─── 合伙人管理 ────────────────────────────────────────────────


class PartnerStaffItem(BaseModel):
    id: int = Field(description="团队 ID（partner_team.id）")
    team_id: int
    user_id: int = Field(description="合伙人 owner user id")
    account: str | None = Field(default=None, description="账号昵称")
    display_name: str | None = None
    avatar: str | None = None
    phone: str | None = None
    team_name: str
    level_id: Literal[1, 2, 3] = 1
    level_name: str | None = None
    member_count: int = Field(default=0, description="团队成员（推广红娘）数")
    performance_amount: str = Field(default="0.00", description="团队业绩：团队成员发展会员的平台消费总额")
    effective_member_count: int = Field(default=0, description="团队有效会员：资料审核通过的会员总数")
    commission_amount: str = Field(default="0.00", description="累积分成")
    status: Literal[1, 2, 3] = 1
    status_label: str = "正常"
    open_mode: str = "manual"
    created_at: datetime | None = None


class PartnerStaffPage(BaseModel):
    items: list[PartnerStaffItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class PartnerStatistics(BaseModel):
    total_partners: int = 0
    active_partners: int = 0
    total_members: int = 0
    total_effective_members: int = 0
    total_performance: str = "0.00"
    total_commission: str = "0.00"


class PartnerUserCandidate(BaseModel):
    id: int
    nickname: str | None = None
    real_name: str | None = None
    phone: str | None = None
    avatar: str | None = None
    is_promoter: bool = False
    has_team: bool = False


class PartnerStaffCreate(BaseModel):
    """添加合伙人：绑定一个已注册用户，建团队 + 授予 partner 角色。"""

    user_id: int | None = Field(default=None, ge=1)
    lookup: str | None = Field(default=None, max_length=64, description="按昵称/手机号搜索绑定")
    lookup_by: Literal["nickname", "phone"] = "nickname"
    team_name: str = Field(min_length=1, max_length=128)
    level_id: Literal[1, 2, 3] = 1
    open_mode: Literal["manual", "paid"] = "manual"

    @model_validator(mode="after")
    def require_target(self) -> "PartnerStaffCreate":
        if self.user_id is None and not (self.lookup and self.lookup.strip()):
            raise ValueError("请先选择或搜索要绑定的用户账号")
        return self


class PartnerStaffUpdate(BaseModel):
    team_name: str | None = Field(default=None, min_length=1, max_length=128)
    level_id: Literal[1, 2, 3] | None = None
    status: Literal[1, 2, 3] | None = None
    open_mode: Literal["manual", "paid"] | None = None

    @model_validator(mode="after")
    def require_update(self) -> "PartnerStaffUpdate":
        if all(value is None for value in (self.team_name, self.level_id, self.status, self.open_mode)):
            raise ValueError("至少提供一个需要修改的字段")
        return self


class PartnerStaffDetail(PartnerStaffItem):
    owner_nickname: str | None = None
    owner_phone: str | None = None
    invite_code: str | None = Field(default=None, description="团队邀请码（promotion_touch）")


# ─── 团队关系 ──────────────────────────────────────────────────


class PartnerRelationItem(BaseModel):
    id: int = Field(description="partner_membership.id")
    promoter_id: int
    promoter_name: str | None = None
    promoter_avatar: str | None = None
    promoter_phone: str | None = None
    team_id: int
    team_name: str | None = None
    joined_at: datetime | None = None
    left_at: datetime | None = None
    member_count: int = Field(default=0, description="该推广红娘发展的会员数")
    performance_amount: str = Field(default="0.00", description="团队业绩贡献")
    status: Literal[1, 2, 3] = 1
    status_label: str = "正常"
    change_reason: str | None = None


class PartnerRelationPage(BaseModel):
    items: list[PartnerRelationItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class PartnerTeamOption(BaseModel):
    id: int
    name: str
    owner_user_id: int | None = None
    owner_name: str | None = None
    level_id: Literal[1, 2, 3] | None = None
    level_name: str | None = None


class PartnerRelationBind(BaseModel):
    """人工绑定团队关系：如果推广红娘已在其它团队，则先移出再绑定。"""

    promoter_user_id: int | None = Field(default=None, ge=1)
    promoter_lookup: str | None = Field(default=None, max_length=64, description="按推广红娘昵称搜索")
    team_id: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def require_promoter(self) -> "PartnerRelationBind":
        if self.promoter_user_id is None and not (self.promoter_lookup and self.promoter_lookup.strip()):
            raise ValueError("请先选择或搜索要绑定的推广红娘")
        return self


class PartnerRelationRemove(BaseModel):
    reason: str = Field(min_length=1, max_length=255, description="移出/变更原因")


class PartnerRelationResult(BaseModel):
    promoter_id: int
    team_id: int | None = None
    team_name: str | None = None
    status: Literal["BOUND", "REMOVED"] = "BOUND"
    message: str = ""


# ─── 分成明细 ──────────────────────────────────────────────────


class PartnerCommissionEntryItem(BaseModel):
    id: int
    created_at: datetime | None = None
    partner_id: int = Field(description="合伙人（团队 owner）user id")
    partner_name: str | None = None
    team_id: int | None = None
    team_name: str | None = None
    promoter_id: int | None = Field(default=None, description="团队推广红娘 user id")
    promoter_name: str | None = None
    event_type: str | None = Field(default=None, description="分成类型")
    event_name: str | None = Field(default=None, description="分成事件描述")
    consumer_id: int | None = None
    consumer_name: str | None = None
    order_id: int | None = None
    order_no: str | None = None
    base_amount: str = "0.00"
    amount: str = "0.00"
    status: str = "PENDING"
    source: str = "order"
    remark: str | None = None


class PartnerCommissionEntryPage(BaseModel):
    items: list[PartnerCommissionEntryItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class PartnerCommissionPartnerOption(BaseModel):
    id: int = Field(description="合伙人（团队 owner）user id")
    name: str
    team_id: int | None = None
    team_name: str | None = None
    avatar: str | None = None


class PartnerCommissionEventOption(BaseModel):
    id: int
    name: str


class PartnerCommissionEntryOptions(BaseModel):
    partners: list[PartnerCommissionPartnerOption] = Field(default_factory=list)
    events: list[PartnerCommissionEventOption] = Field(default_factory=list)


class PartnerCommissionEntryCreate(BaseModel):
    """后台手工录入一笔合伙人分成；自动写 commission_entry + account_ledger。"""

    partner_user_id: int = Field(ge=1, description="合伙人（团队 owner）user id")
    consumer_user_id: int | None = Field(default=None, ge=1, description="购买账号 user id")
    rule_id: int | None = Field(default=None, ge=1, description="分成事件（commission_rule.id）")
    amount: Decimal = Field(gt=0, le=1000000, decimal_places=2, description="分成金额(元)")
    base_amount: Decimal | None = Field(default=None, ge=0, le=100000000, decimal_places=2)
    remark: str | None = Field(default=None, max_length=255)


class PartnerCommissionEntryCreateResult(BaseModel):
    entry: PartnerCommissionEntryItem
    ledger_id: int
    balance_after: str = "0.00"

"""Independent authentication and management contracts for the matchmaker back office."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.organization import ResourceAssignmentResponse


class MatchmakerAdminLoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=128)


class MatchmakerAdminAccount(BaseModel):
    id: int
    username: str
    display_name: str
    matchmaker_user_id: int | None
    data_scope: Literal["SELF", "STORE", "ORGANIZATION", "ALL"] = "SELF"
    organization_id: int | None = None
    status: Literal[1, 2, 3]
    last_login_at: datetime | None


class MatchmakerAdminTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    account: MatchmakerAdminAccount


class MatchmakerAdminRefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=512)


class MatchmakerAdminMeResponse(BaseModel):
    account: MatchmakerAdminAccount
    permissions: list[str]


class MatchmakerStatusUpdate(BaseModel):
    status: Literal[1, 2]
    reason: str | None = Field(default=None, max_length=255)


class MatchmakerStatusResponse(BaseModel):
    matchmaker_id: int
    status: Literal[1, 2]
    reason: str | None


class MatchmakerStatistics(BaseModel):
    total: int
    available: int
    pending_services: int
    active_services: int
    completed_services: int
    cancelled_services: int


class ResourceAssignmentPage(BaseModel):
    items: list[ResourceAssignmentResponse]
    page: int
    page_size: int
    total: int
    has_more: bool


class RewardRule(BaseModel):
    id: int
    task_code: str
    task_name: str
    task_type: Literal[1, 2, 3]
    reward_type: Literal[1, 2, 3, 4, 5]
    reward_value: int
    daily_limit: int
    is_active: Literal[0, 1]
    sort: int
    created_at: datetime | None
    updated_at: datetime | None


class RewardRuleCreate(BaseModel):
    task_code: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    task_name: str = Field(min_length=1, max_length=64)
    task_type: Literal[1, 2, 3] = 1
    reward_type: Literal[1, 2, 3, 4, 5] = 1
    reward_value: int = Field(default=0, ge=0, le=2147483647)
    daily_limit: int = Field(default=0, ge=0, le=2147483647)
    is_active: Literal[0, 1] = 1
    sort: int = Field(default=0, ge=0, le=2147483647)


class RewardRuleUpdate(BaseModel):
    task_name: str | None = Field(default=None, min_length=1, max_length=64)
    task_type: Literal[1, 2, 3] | None = None
    reward_type: Literal[1, 2, 3, 4, 5] | None = None
    reward_value: int | None = Field(default=None, ge=0, le=2147483647)
    daily_limit: int | None = Field(default=None, ge=0, le=2147483647)
    is_active: Literal[0, 1] | None = None
    sort: int | None = Field(default=None, ge=0, le=2147483647)


class RewardRuleDeleteResponse(BaseModel):
    task_code: str
    deleted: bool


# =====================================================================
# 总店红娘分派配置（assign/abandon × member_crm/customer_lead）
# =====================================================================

ApportionScope = Literal["member_crm", "customer_lead"]
ApportionConfigType = Literal["assign", "abandon"]
ApportionStrategy = Literal[
    "designated",
    "round_robin_random",
    "by_region",
    "by_promoter",
    "none",
]


class ApportionConfig(BaseModel):
    """分派配置单块记录（assign 或 abandon 中的一个）。"""

    id: int
    scope: ApportionScope
    config_type: ApportionConfigType
    # 仅 assign 块使用
    strategy: ApportionStrategy | None = None
    target_matchmaker_id: int | None = None
    target_matchmaker_name: str | None = None
    # 仅 abandon 块使用
    auto_abandon_days: int | None = Field(default=None, ge=0, le=90)
    daily_pickup_limit: int | None = Field(default=None, ge=0, le=100000)
    show_admin_abandoned_in_pool: bool = True
    show_store_abandoned_in_pool: bool = True
    is_enabled: bool = True
    updated_by: int | None = None
    remark: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ApportionAssignUpdate(BaseModel):
    """分配配置（assign 块）的 PUT 入参。"""

    strategy: ApportionStrategy = Field(..., description="分配策略字典")
    target_matchmaker_id: int | None = Field(default=None, ge=1)
    is_enabled: bool = True
    remark: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def _designated_requires_target(self) -> "ApportionAssignUpdate":
        if self.strategy == "designated" and self.target_matchmaker_id is None:
            raise ValueError("strategy=designated 时必须指定 target_matchmaker_id")
        if self.strategy != "designated" and self.target_matchmaker_id is not None:
            raise ValueError("仅 strategy=designated 才允许传入 target_matchmaker_id")
        return self


class ApportionAbandonUpdate(BaseModel):
    """弃海配置（abandon 块）的 PUT 入参。"""

    auto_abandon_days: int = Field(..., description="0=不启用；3/7/15/30/45/60/90")
    daily_pickup_limit: int = Field(default=0, ge=0, le=100000, description="0=不限")
    show_admin_abandoned_in_pool: bool = True
    show_store_abandoned_in_pool: bool = True
    is_enabled: bool = True
    remark: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def _days_in_whitelist(self) -> "ApportionAbandonUpdate":
        allowed = {0, 3, 7, 15, 30, 45, 60, 90}
        if self.auto_abandon_days not in allowed:
            raise ValueError(f"auto_abandon_days 必须在 {sorted(allowed)} 之内")
        return self


class ApportionToggleUpdate(BaseModel):
    """PATCH 切换 is_enabled 用。"""

    is_enabled: bool
    remark: str | None = Field(default=None, max_length=255)


class ApportionConfigAuditLog(BaseModel):
    id: int
    actor_user_id: int | None
    action: str
    resource_type: str
    resource_id: int | None
    before_json: str | None
    after_json: str | None
    reason: str | None
    created_at: datetime | None


class ApportionConfigAuditLogPage(BaseModel):
    items: list[ApportionConfigAuditLog]
    page: int
    page_size: int
    total: int
    has_more: bool


# =====================================================================
# 服务红娘分成级别（commission_level：初级 / 中级 / 高级 / 合伙）
# =====================================================================

CommissionLevelMode = Literal["rate", "fixed"]


class CommissionLevel(BaseModel):
    """一条红娘分成级别配置。"""

    id: int
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=64)
    mode: CommissionLevelMode = "rate"
    rate_percent: Decimal = Field(ge=Decimal("0"), le=Decimal("100"))
    fixed_amount: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("10000000"))
    platform_extra_amount: Decimal = Field(default=Decimal("0"), ge=Decimal("0"), le=Decimal("10000000"))
    promotion_condition: str | None = Field(default=None, max_length=255)
    sort: int = 0
    status: Literal[1, 2] = 1
    applicable_matchmaker_count: int = 0
    updated_by: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CommissionLevelUpdate(BaseModel):
    """编辑分成级别（仅业务字段可改，code/status 由 seed 锁定）。"""

    name: str | None = Field(default=None, min_length=1, max_length=64)
    mode: CommissionLevelMode | None = None
    rate_percent: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("100"))
    fixed_amount: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("10000000"))
    platform_extra_amount: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("10000000"))
    promotion_condition: str | None = Field(default=None, max_length=255)
    sort: int | None = None
    status: Literal[1, 2] | None = None

    @model_validator(mode="after")
    def _verify_mode_amount(self) -> "CommissionLevelUpdate":
        if self.mode == "fixed" and self.fixed_amount is None:
            raise ValueError("mode=fixed 时必须填写 fixed_amount")
        return self

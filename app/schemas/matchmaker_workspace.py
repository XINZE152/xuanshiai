"""Contracts for the mobile service-matchmaker workspace.

The routes in this module deliberately use the normal mobile user session.  They
are not aliases for the independent ``/admin`` matchmaker console.
"""

from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


WorkspaceLevel = Literal["NORMAL", "SUPER"]
WorkspaceScope = Literal["SELF", "ORGANIZATION"]
ReviewStatus = Literal["PENDING", "PASSED", "REJECTED"]
LeadStatus = Literal["NEW", "CONTACTED", "INTENDED", "CONVERTED", "LOST", "CLOSED"]
FollowUpMethod = Literal["PHONE", "WECHAT", "VISIT", "OTHER"]
IntroductionStatus = Literal["PENDING", "IN_PROGRESS", "SUCCEEDED", "FAILED"]


class MatchmakerWorkspaceAccess(BaseModel):
    can_access: bool
    role_status: Literal["active", "inactive"]
    payment_status: Literal["not_required"] = "not_required"
    scope: WorkspaceScope | None = None
    message: str


class MatchmakerWorkspaceProfile(BaseModel):
    user_id: int
    display_name: str
    level: WorkspaceLevel
    scope: WorkspaceScope
    organization_id: int | None


class MatchmakerWorkspaceProfileUpdate(BaseModel):
    display_name: str = Field(min_length=1, max_length=64)


class WorkspaceMetric(BaseModel):
    key: str
    label: str
    value: str
    action: str
    value_prefix: str | None = Field(default=None, max_length=8)
    badge: str | None = Field(default=None, max_length=32)


class MatchmakerWorkspaceIdentity(BaseModel):
    greeting: str
    name: str
    role_name: str


class MatchmakerWorkspaceDashboard(BaseModel):
    identity: MatchmakerWorkspaceIdentity
    store_metrics: list[WorkspaceMetric] = Field(min_length=8, max_length=8)


def _strip_contact(value: str | None) -> str | None:
    """联系方式归一化：去首尾空白，空串视为未提供（None）。"""
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None


class WorkspaceLeadCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    phone: str | None = Field(default=None, max_length=32)
    wechat: str | None = Field(default=None, max_length=128)
    source: str = Field(min_length=1, max_length=64)
    intention_level: Literal[1, 2, 3] = 1
    remark: str | None = Field(default=None, max_length=2000)

    @field_validator("phone", "wechat", mode="before")
    @classmethod
    def normalize_contact(cls, value: object) -> object:
        if isinstance(value, str):
            return _strip_contact(value)
        return value

    @model_validator(mode="after")
    def require_contact(self) -> "WorkspaceLeadCreate":
        if not self.phone and not self.wechat:
            raise ValueError("phone 或 wechat 至少提供一个")
        return self


class WorkspaceLeadUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    phone: str | None = Field(default=None, max_length=32)
    wechat: str | None = Field(default=None, max_length=128)
    intention_level: Literal[1, 2, 3] | None = None
    status: LeadStatus | None = None
    remark: str | None = Field(default=None, max_length=2000)
    next_follow_at: datetime | None = None

    @field_validator("phone", "wechat", mode="before")
    @classmethod
    def normalize_contact(cls, value: object) -> object:
        if isinstance(value, str):
            return _strip_contact(value)
        return value

    @model_validator(mode="after")
    def require_update(self) -> "WorkspaceLeadUpdate":
        if not self.model_fields_set:
            raise ValueError("至少提供一个需要修改的字段")
        return self


class WorkspaceLead(BaseModel):
    id: int
    name: str
    phone: str | None
    wechat: str | None
    source: str
    intention_level: Literal[1, 2, 3]
    status: LeadStatus
    next_follow_at: datetime | None
    created_at: datetime
    updated_at: datetime
    matchmaker_id: int | None = None
    matchmaker_name: str | None = None
    remark: str | None = None
    last_follow_up_at: datetime | None = None
    converted_user_id: int | None = None


class WorkspaceLeadPage(BaseModel):
    items: list[WorkspaceLead]
    page: int
    page_size: int
    total: int
    has_more: bool


class WorkspaceLeadFollowUpCreate(BaseModel):
    method: FollowUpMethod
    content: str = Field(min_length=1, max_length=2000)
    intention_level: Literal[1, 2, 3] | None = None
    next_follow_at: datetime | None = None


class WorkspaceLeadFollowUp(BaseModel):
    id: int
    lead_id: int
    method: FollowUpMethod
    content: str
    intention_level: Literal[1, 2, 3] | None
    next_follow_at: datetime | None
    created_at: datetime


class WorkspaceLeadFollowUpPage(BaseModel):
    items: list[WorkspaceLeadFollowUp]
    page: int
    page_size: int
    total: int
    has_more: bool


class WorkspaceAbandonRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class WorkspaceAssignRequest(BaseModel):
    """把客源线索分派给同组织内的另一位服务红娘跟进。"""

    matchmaker_id: int = Field(ge=1)


class WorkspaceLeadConvertRequest(BaseModel):
    """一键入库：客源线索转为平台正式用户并进入公开资料审核队列。"""

    gender: Literal[1, 2] | None = None
    remark: str | None = Field(default=None, max_length=500)


class WorkspaceLeadConvertResult(BaseModel):
    lead: "WorkspaceLead"
    user_id: int
    review_status: ReviewStatus = "PENDING"


class WorkspaceLeadFollower(BaseModel):
    """同组织内可被分派跟进线索的服务红娘。"""

    user_id: int
    display_name: str
    is_self: bool = False


class WorkspaceMember(BaseModel):
    user_id: int
    nickname: str
    avatar: str | None
    gender: Literal[1, 2] | None
    birthday: date | None
    hometown: str | None
    residence: str | None
    education: str | None
    job: str | None
    is_married: int | None = None
    height: int | None = None
    realname_status: int | None = None
    matchmaker_id: int | None = None
    matchmaker_name: str | None = None
    profile_completion_score: float
    review_status: ReviewStatus
    review_reason: str | None
    reviewed_at: datetime | None
    next_follow_at: datetime | None
    created_at: datetime


class WorkspaceMemberPage(BaseModel):
    items: list[WorkspaceMember]
    page: int
    page_size: int
    total: int
    has_more: bool


class WorkspaceMemberReviewUpdate(BaseModel):
    status: Literal["PASSED", "REJECTED"]
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def require_rejection_reason(self) -> "WorkspaceMemberReviewUpdate":
        if self.status == "REJECTED" and not self.reason:
            raise ValueError("驳回资料时必须填写原因")
        return self


class WorkspaceMemberMatchmakerUpdate(BaseModel):
    """修改会员的跟进服务红娘；置 null 表示改为待分派。"""

    matchmaker_id: int | None = Field(default=None, ge=1)


class WorkspaceMemberNoteUpdate(BaseModel):
    """红娘说：红娘对会员的公开评价。"""

    note: str = Field(min_length=0, max_length=200)


class WorkspaceMemberNote(BaseModel):
    user_id: int
    note: str
    updated_at: datetime | None = None


class WorkspaceMemberFollowUpCreate(BaseModel):
    method: FollowUpMethod
    content: str = Field(min_length=1, max_length=2000)
    next_follow_at: datetime | None = None


class WorkspaceMemberFollowUp(BaseModel):
    id: int
    user_id: int
    method: FollowUpMethod
    content: str
    next_follow_at: datetime | None
    created_at: datetime


class WorkspaceMemberFollowUpPage(BaseModel):
    items: list[WorkspaceMemberFollowUp]
    page: int
    page_size: int
    total: int
    has_more: bool


class WorkspaceMemberContact(BaseModel):
    user_id: int
    phone: str | None


class WorkspaceMatchCandidate(BaseModel):
    user_id: int
    nickname: str
    avatar: str | None
    gender: Literal[1, 2] | None
    birthday: date | None
    residence: str | None
    matchmaker_name: str | None = None


class MemberPartnerPreference(BaseModel):
    """会员择偶要求摘要，空字段由前端展示为“不限”。"""

    age_min: int | None = None
    age_max: int | None = None
    height_min: int | None = None
    height_max: int | None = None
    income_min: float | None = None
    education_min: int | None = None


class WorkspaceMatchCandidatePage(BaseModel):
    items: list[WorkspaceMatchCandidate]
    page: int
    page_size: int
    total: int
    has_more: bool
    preference: MemberPartnerPreference | None = None


class WorkspaceMatchHistoryItem(BaseModel):
    id: int
    counterpart_user_id: int
    counterpart_nickname: str
    status: Literal[0, 1, 2, 3]
    direction: Literal["OUTGOING", "INCOMING"]
    created_at: datetime
    responded_at: datetime | None


class WorkspaceMatchHistoryPage(BaseModel):
    items: list[WorkspaceMatchHistoryItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class WorkspaceIntroductionPerson(BaseModel):
    user_id: int
    nickname: str
    avatar: str | None
    gender: Literal[1, 2] | None


class WorkspaceIntroduction(BaseModel):
    id: int
    status: IntroductionStatus
    failure_reason: str | None
    note: str | None
    created_at: datetime
    updated_at: datetime
    from_user: WorkspaceIntroductionPerson
    to_user: WorkspaceIntroductionPerson


class WorkspaceIntroductionPage(BaseModel):
    items: list[WorkspaceIntroduction]
    page: int
    page_size: int
    total: int
    has_more: bool


class WorkspaceMeetingRecordSummary(BaseModel):
    id: int
    status: Literal["SCHEDULED", "REMINDED", "CHECKED_IN", "COMPLETED", "CANCELLED", "NO_SHOW"]
    scheduled_at: datetime
    location: str
    cancel_reason: str | None


class WorkspaceMeetingRequest(BaseModel):
    id: int
    status: Literal["SUBMITTED", "CONTACTED", "ACCEPTED", "DECLINED", "CLOSED"]
    matchmaker_name: str = "待分派"
    note: str
    created_at: datetime
    updated_at: datetime
    applicant: WorkspaceIntroductionPerson
    target: WorkspaceIntroductionPerson
    meeting: WorkspaceMeetingRecordSummary | None


class WorkspaceMeetingRequestPage(BaseModel):
    items: list[WorkspaceMeetingRequest]
    page: int
    page_size: int
    total: int
    has_more: bool


class PartnerTeamUpdate(BaseModel):
    name: str = Field(min_length=2, max_length=128)


class PartnerTeamMember(BaseModel):
    user_id: int
    nickname: str
    joined_at: datetime
    effective_count: int = 0


class PartnerEffectiveMember(BaseModel):
    user_id: int
    nickname: str
    gender: Literal[1, 2] | None
    register_at: datetime
    promoter_name: str


class PartnerLevelInfo(BaseModel):
    """合伙级别按团队业绩与有效会员数双阈值实时计算；level_progress 为到下一级的差距。"""

    level: Literal["初级合伙", "中级合伙", "战略合伙"]
    team_performance: float
    effective_member_count: int
    next_level: str | None = None
    performance_gap: float | None = None
    member_gap: int | None = None


class PartnerCenterProfile(BaseModel):
    nickname: str
    team_name: str | None
    invite_code: str | None
    phone: str | None = None
    wechat_bound: bool = False
    register_at: datetime | None = None


class PromoterRewardItem(BaseModel):
    """注册奖励展示项；合伙人侧与推广红娘侧共用同一展示口径。"""

    user_id: int
    nickname: str
    gender: Literal[1, 2] | None = None
    avatar: str | None = None
    register_at: datetime
    review_status: ReviewStatus
    reward_amount: float = 0


class PartnerCenterSnapshot(BaseModel):
    profile: PartnerCenterProfile
    team_members: list[PartnerTeamMember]
    effective_members: list[PartnerEffectiveMember]
    registration_rewards: list[PromoterRewardItem] = []
    level: PartnerLevelInfo | None = None
    finance_available: Literal[False] = False


class PromoterLeadCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    phone: str | None = Field(default=None, max_length=32)
    wechat: str | None = Field(default=None, max_length=128)
    source: str = Field(min_length=1, max_length=64)
    remark: str | None = Field(default=None, max_length=2000)

    @field_validator("phone", "wechat", mode="before")
    @classmethod
    def normalize_contact(cls, value: object) -> object:
        if isinstance(value, str):
            return _strip_contact(value)
        return value

    @model_validator(mode="after")
    def require_contact(self) -> "PromoterLeadCreate":
        if not self.phone and not self.wechat:
            raise ValueError("phone 或 wechat 至少提供一个")
        return self


class PromoterMember(BaseModel):
    user_id: int
    nickname: str
    gender: Literal[1, 2] | None
    register_at: datetime
    review_status: ReviewStatus


class PromoterCenterProfile(BaseModel):
    nickname: str
    promotion_code: str | None
    phone: str | None = None
    wechat_bound: bool = False
    register_at: datetime | None = None
    team_name: str | None = None
    team_status: Literal["NONE", "PENDING", "ACTIVE"] = "NONE"
    pending_request_id: int | None = None
    level: Literal["初级", "推广大师", "推广大使", "推广天使"] = "初级"
    next_level: str | None = None
    member_gap: int | None = None


class PromoterCenterSnapshot(BaseModel):
    profile: PromoterCenterProfile
    leads: list[WorkspaceLead]
    effective_members: list[PromoterMember]
    incomplete_members: list[PromoterMember]
    rewards: list[PromoterRewardItem] = []
    finance_available: Literal[False] = False


class PartnerJoinRequestCreate(BaseModel):
    """推广红娘凭团队邀请码提交入团申请；合伙人确认后才真正加入团队。"""

    invite_code: str = Field(min_length=4, max_length=64)


class PartnerJoinRequestSummary(BaseModel):
    id: int
    team_id: int
    team_name: str
    promoter_id: int
    promoter_nickname: str | None = None
    promoter_avatar: str | None = None
    status: Literal["PENDING", "APPROVED", "REJECTED", "CANCELLED"]
    created_at: datetime
    reviewed_at: datetime | None = None
    reject_reason: str | None = None


class PartnerJoinRequestReview(BaseModel):
    status: Literal["APPROVED", "REJECTED"]
    reason: str | None = Field(default=None, max_length=200)


class PromotionCode(BaseModel):
    code: str
    created_at: datetime


class WorkspaceMemberEntryCreate(BaseModel):
    """服务红娘工作台录入会员；基本信息入库，扩展字段按 user_profile 实际列落库。"""

    phone: str = Field(pattern=r"^1[3-9]\d{9}$")
    nickname: str = Field(min_length=1, max_length=64)
    real_name: str | None = Field(default=None, max_length=64)
    gender: Literal[1, 2]
    birthday: date | None = None
    is_married: Literal[1, 2, 3] | None = None
    wechat: str | None = Field(default=None, max_length=128)
    height: int | None = Field(default=None, ge=100, le=250)
    weight: int | None = Field(default=None, ge=40, le=120)
    hometown: str | None = Field(default=None, max_length=64)
    residence: str | None = Field(default=None, max_length=64)
    household: str | None = Field(default=None, max_length=64)
    ethnicity: str | None = Field(default=None, max_length=32)
    zodiac: str | None = Field(default=None, max_length=16)
    education: str | None = Field(default=None, max_length=32)
    job: str | None = Field(default=None, max_length=64)
    income: float | None = Field(default=None, ge=0, le=1_000_000)
    house: str | None = Field(default=None, max_length=16)
    car: str | None = Field(default=None, max_length=16)
    smoking: str | None = Field(default=None, max_length=16)
    drinking: str | None = Field(default=None, max_length=16)
    religion: str | None = Field(default=None, max_length=32)
    marriage_plan: str | None = Field(default=None, max_length=64)
    tags: str | None = Field(default=None, max_length=128)
    remark: str | None = Field(default=None, max_length=2000)


class WorkspaceMemberEntryResult(BaseModel):
    member_id: int
    review_status: ReviewStatus
    matchmaker_id: int


class WorkspaceMeetingStatusUpdate(BaseModel):
    """约见申请处理状态流转；待处理=SUBMITTED，已处理=ACCEPTED/DECLINED/CLOSED。"""

    status: Literal["SUBMITTED", "CONTACTED", "ACCEPTED", "DECLINED", "CLOSED"]


class WorkspaceMeetingScheduleCreate(BaseModel):
    """服务红娘为双方接受的约见申请创建约见安排；时间必须晚于当前时刻。"""

    scheduled_at: datetime
    location: str = Field(min_length=1, max_length=128)

    @field_validator("scheduled_at")
    @classmethod
    def require_future_time(cls, value: datetime) -> datetime:
        now = datetime.now(UTC) if value.tzinfo is not None else datetime.now()
        if value <= now:
            raise ValueError("约见时间必须晚于当前时间")
        return value


class WorkspaceMeetingRecordStatusUpdate(BaseModel):
    """约见安排状态流转：进行中=REMINDED/CHECKED_IN，完成=COMPLETED，结束=CANCELLED/NO_SHOW。"""

    status: Literal["REMINDED", "CHECKED_IN", "COMPLETED", "CANCELLED", "NO_SHOW"]
    cancel_reason: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def require_cancel_reason(self) -> "WorkspaceMeetingRecordStatusUpdate":
        if self.status in {"CANCELLED", "NO_SHOW"} and not (
            self.cancel_reason and str(self.cancel_reason).strip()
        ):
            raise ValueError("取消或爽约必须填写原因")
        if self.status not in {"CANCELLED", "NO_SHOW"} and self.cancel_reason:
            raise ValueError("仅取消或爽约需要填写原因")
        return self


class WorkspaceIntroductionStatusUpdate(BaseModel):
    """牵线状态流转；失败必须给出失败原因，其他状态不允许携带备注。"""

    status: Literal["PENDING", "IN_PROGRESS", "SUCCEEDED", "FAILED"]
    failure_reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def require_failure_reason(self) -> "WorkspaceIntroductionStatusUpdate":
        if self.status == "FAILED" and not (self.failure_reason and str(self.failure_reason).strip()):
            raise ValueError("牵线失败必须填写失败原因")
        if self.status != "FAILED" and self.failure_reason:
            raise ValueError("仅牵线失败需要填写失败原因")
        return self


class WorkspaceIntroductionCreate(BaseModel):
    """服务红娘手工添加牵线记录；双方必须是工作范围内会员。"""

    from_user_id: int = Field(ge=1)
    to_user_id: int = Field(ge=1)
    applied_at: date | None = None
    finished_at: date | None = None
    result: Literal["SUCCEEDED", "FAILED"]
    failure_reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_pairs(self) -> "WorkspaceIntroductionCreate":
        if self.from_user_id == self.to_user_id:
            raise ValueError("牵线会员与被牵线会员不能是同一人")
        if self.result == "FAILED" and not (self.failure_reason and str(self.failure_reason).strip()):
            raise ValueError("牵线失败必须填写失败原因")
        if self.result == "SUCCEEDED" and self.finished_at is None:
            raise ValueError("牵线成功必须选择牵线完成时间")
        return self


class WorkspaceIntroductionCreated(BaseModel):
    id: int
    status: Literal["PENDING", "IN_PROGRESS", "SUCCEEDED", "FAILED"]

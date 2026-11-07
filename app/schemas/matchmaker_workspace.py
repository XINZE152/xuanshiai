"""Contracts for the mobile service-matchmaker workspace.

The routes in this module deliberately use the normal mobile user session.  They
are not aliases for the independent ``/admin`` matchmaker console.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


WorkspaceLevel = Literal["NORMAL", "SUPER"]
WorkspaceScope = Literal["SELF", "ORGANIZATION"]
ReviewStatus = Literal["PENDING", "PASSED", "REJECTED"]
LeadStatus = Literal["NEW", "CONTACTED", "INTENDED", "CONVERTED", "LOST", "CLOSED"]
LeadIntentionLevel = Literal[1, 2, 3, 4, 5, 6, 7]
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


class MatchmakerHomepageCase(BaseModel):
    description: str = Field(min_length=1, max_length=1000)
    images: list[str] = Field(default_factory=list, max_length=3)


class MatchmakerHomepageCaseInput(MatchmakerHomepageCase):
    """保存口径：案例说明 50 字以内。读取仍用宽松模型，改口径之前入库的长案例要能读出来。"""

    description: str = Field(min_length=1, max_length=50)


class MatchmakerHomepage(BaseModel):
    """红娘本人可见的主页编辑资料；字段与公开主页同源，落库在入驻申请表。"""

    user_id: int
    nickname: str
    avatar: str | None
    slogan: str | None
    specialties: list[str]
    introduction: str
    success_cases: list[MatchmakerHomepageCase]


class MatchmakerHomepageUpdate(BaseModel):
    nickname: str | None = Field(default=None, min_length=1, max_length=64)
    slogan: str | None = Field(default=None, max_length=64)
    specialties: list[str] | None = Field(default=None, max_length=5)
    introduction: str | None = Field(default=None, min_length=10, max_length=2000)
    success_cases: list[MatchmakerHomepageCaseInput] | None = Field(default=None, max_length=3)

    @field_validator("nickname", "introduction")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("内容不能为空")
        return cleaned

    @field_validator("slogan")
    @classmethod
    def normalize_slogan(cls, value: str | None) -> str | None:
        # 空串表示清空签名，与「未填」在主页上是同一种展示。
        if value is None or not value.strip():
            return None
        return value.strip()

    @field_validator("specialties")
    @classmethod
    def validate_specialties(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = [item.strip() for item in value if item.strip()]
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("擅长领域不能重复")
        return cleaned

    @field_validator("success_cases")
    @classmethod
    def validate_success_cases(
        cls, value: list[MatchmakerHomepageCaseInput] | None
    ) -> list[MatchmakerHomepageCaseInput] | None:
        if value is None:
            return None
        return [
            case.model_copy(
                update={"description": case.description.strip(), "images": [image.strip() for image in case.images if image.strip()]}
            )
            for case in value
        ]


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
    avatar: str | None = None


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
    real_name: str | None = Field(default=None, max_length=64)
    phone: str | None = Field(default=None, pattern=r"^1[3-9]\d{9}$")
    wechat: str | None = Field(default=None, max_length=128)
    source: str = Field(min_length=1, max_length=64)
    intention_level: LeadIntentionLevel = 1
    remark: str | None = Field(default=None, max_length=2000)
    photo: str | None = Field(default=None, max_length=512)
    gender: Literal[1, 2] | None = None
    birthday: date | None = None

    @field_validator("phone", "wechat", mode="before")
    @classmethod
    def normalize_contact(cls, value: object) -> object:
        if isinstance(value, str):
            return _strip_contact(value)
        return value

    @model_validator(mode="after")
    def require_phone(self) -> "WorkspaceLeadCreate":
        # 业务口径：线索必须能联系到人，且只有手机号能一键入库成会员（微信号只是文本备注）。
        if not self.phone:
            raise ValueError("必须登记 11 位手机号")
        return self


class WorkspaceLeadUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    real_name: str | None = Field(default=None, max_length=64)
    phone: str | None = Field(default=None, pattern=r"^1[3-9]\d{9}$")
    wechat: str | None = Field(default=None, max_length=128)
    intention_level: LeadIntentionLevel | None = None
    status: LeadStatus | None = None
    remark: str | None = Field(default=None, max_length=2000)
    next_follow_at: datetime | None = None
    photo: str | None = Field(default=None, max_length=512)
    gender: Literal[1, 2] | None = None
    birthday: date | None = None

    @field_validator("phone", "wechat", mode="before")
    @classmethod
    def normalize_contact(cls, value: object) -> object:
        if isinstance(value, str):
            return _strip_contact(value)
        return value

    @model_validator(mode="after")
    def require_update(self) -> "WorkspaceLeadUpdate":
        # 显式传 phone=null/"" 会被归一化成 None：那等于把线索改成联系不上人的死数据。
        if "phone" in self.model_fields_set and not self.phone:
            raise ValueError("手机号不能清空，只能改成有效的 11 位手机号")
        if not self.model_fields_set:
            raise ValueError("至少提供一个需要修改的字段")
        return self


class WorkspaceLead(BaseModel):
    id: int
    name: str
    real_name: str | None = None
    phone: str | None
    wechat: str | None
    source: str
    intention_level: LeadIntentionLevel
    status: LeadStatus
    next_follow_at: datetime | None
    created_at: datetime
    updated_at: datetime
    matchmaker_id: int | None = None
    matchmaker_name: str | None = None
    # 录入人 = customer_lead.created_by 对应的红娘；弃海池被捞取时改写为捞取人（谁捞回归谁）。
    creator_name: str | None = None
    remark: str | None = None
    last_follow_up_at: datetime | None = None
    # 弃海原因与时点取自当前生效的弃海记录（restored_at IS NULL）；未弃海为 null。
    abandon_reason: str | None = None
    abandoned_at: datetime | None = None
    converted_user_id: int | None = None
    photo: str | None = None
    gender: int | None = None
    birthday: date | None = None


class WorkspaceLeadPage(BaseModel):
    items: list[WorkspaceLead]
    page: int
    page_size: int
    total: int
    has_more: bool


class WorkspaceLeadFollowUpCreate(BaseModel):
    method: FollowUpMethod
    content: str = Field(min_length=1, max_length=2000)
    intention_level: LeadIntentionLevel | None = None
    next_follow_at: datetime | None = None


class WorkspaceLeadFollowUp(BaseModel):
    id: int
    lead_id: int
    method: FollowUpMethod
    content: str
    intention_level: LeadIntentionLevel | None
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
    filter_version: int = 1


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


class WorkspaceMemberPickupResult(BaseModel):
    member_id: int
    matchmaker_id: int
    review_status: Literal["PENDING"] = "PENDING"


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
    """服务红娘受审计读取的会员联系方式。

    ``scope`` 说明本次读取依据的权限范围：``ALL`` 表示超级红娘可查看所有会员，
    ``SERVED`` 表示普通红娘仅能查看自己服务范围内的会员。
    """

    user_id: int
    nickname: str | None = None
    real_name: str | None = None
    phone: str | None = None
    wechat: str | None = None
    scope: Literal["ALL", "SERVED"] = "SERVED"


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
    """会员的一条牵线记录（matchmaker_introduction），对方视角。"""

    id: int
    counterpart_user_id: int
    counterpart_nickname: str
    status: IntroductionStatus
    direction: Literal["OUTGOING", "INCOMING"]
    created_at: datetime
    updated_at: datetime


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
    is_vip: bool = False


class WorkspaceIntroduction(BaseModel):
    id: int
    status: IntroductionStatus
    failure_reason: str | None
    note: str | None
    created_at: datetime
    updated_at: datetime
    from_user: WorkspaceIntroductionPerson
    to_user: WorkspaceIntroductionPerson
    application_response: Literal["UNANSWERED", "ACCEPTED", "REJECTED"] = Field(default="UNANSWERED", description="会员对认识申请的回应，与红娘跟进结果分开；过期仍为未回应")
    response_reason: str | None = Field(default=None, description="对方拒绝时的附言，不是红娘牵线失败原因")
    from_phone_viewed: bool = Field(default=False, description="当前红娘在本轮待牵线阶段已成功查看申请人电话")
    to_phone_viewed: bool = Field(default=False, description="当前红娘在本轮待牵线阶段已成功查看被牵线方电话")


class WorkspaceIntroductionPage(BaseModel):
    items: list[WorkspaceIntroduction]
    page: int
    page_size: int
    total: int
    has_more: bool
    workflow_version: Literal[2] = Field(default=2, description="2=双方电话已查看后进入牵线中，确认已联系后判断结果")


class WorkspaceIntroductionContactView(BaseModel):
    side: Literal["from", "to"] = Field(description="from=申请人，to=被牵线方；禁止传入任意会员ID")
    contacts_obtained: bool = Field(default=False, description="红娘明确确认已获得双方联系方式；双方电话均查看后才可推进牵线中")


class WorkspaceIntroductionContactViewed(BaseModel):
    introduction_id: int = Field(description="本次查看所对应的牵线记录ID")
    status: IntroductionStatus = Field(description="权威阶段；双方电话均已查看且红娘确认已获得联系方式后进入牵线中")
    contact: WorkspaceMemberContact = Field(description="仅本次指定方的电话，微信固定null；电话缺失不计入成功查看")
    from_phone_viewed: bool = Field(description="当前红娘在本条、本轮已查看申请人有效电话")
    to_phone_viewed: bool = Field(description="当前红娘在本条、本轮已查看被牵线方有效电话")


class WorkspaceCommissionEntry(BaseModel):
    """红娘本人的一笔线上分成：消费会员与分成事件由订单、规则回带。"""

    id: int
    created_at: datetime
    consumer_id: int
    consumer_name: str
    consumer_avatar: str | None
    event_name: str
    order_no: str | None
    base_amount: str
    amount: str
    status: Literal["PENDING", "AVAILABLE", "FROZEN", "REVERSED"]


class WorkspaceCommissionEntryPage(BaseModel):
    items: list[WorkspaceCommissionEntry]
    page: int
    page_size: int
    total: int
    has_more: bool
    # 与工作台概览 online_commission 同口径：本人全部分成之和，已剔除冲正行。
    total_amount: str


class WorkspaceOfflineIncome(BaseModel):
    """红娘本人的一笔线下业绩：线下 VIP 已收款订单，会员信息按订单回带。"""

    id: int
    paid_at: datetime
    consumer_id: int
    consumer_name: str
    consumer_avatar: str | None
    item_name: str
    order_no: str | None
    amount: str


class WorkspaceOfflineIncomePage(BaseModel):
    items: list[WorkspaceOfflineIncome]
    page: int
    page_size: int
    total: int
    has_more: bool
    # 与工作台概览 offline_performance 同口径：本人全部线下业绩之和。
    total_amount: str


class WorkspaceMeetingRecordSummary(BaseModel):
    id: int
    status: Literal["SCHEDULED", "REMINDED", "CHECKED_IN", "COMPLETED", "CANCELLED", "NO_SHOW"]
    scheduled_at: datetime
    location: str
    cancel_reason: str | None
    follow_up_content: str | None = None


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
    workflow_version: int = 2


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
    phone: str | None = Field(default=None, pattern=r"^1[3-9]\d{9}$")
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
    def require_phone(self) -> "PromoterLeadCreate":
        # 业务口径同录入线索：必须能联系到人，且只有手机号能一键入库成会员。
        if not self.phone:
            raise ValueError("必须登记 11 位手机号")
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
    # 与会员端资料编辑页同一套婚况档位（1 未婚 / 2 离异 / 3 丧偶）；库里 4~7 属历史值，只读不再写入。
    is_married: Literal[1, 2, 3] | None = None
    wechat: str | None = Field(default=None, max_length=128)
    height: int | None = Field(default=None, ge=100, le=250)
    weight: int | None = Field(default=None, ge=30, le=150)
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
    # 择偶要求（录入时一并写入 user_partner_preference）
    age_min: int | None = Field(default=None, ge=18, le=100)
    age_max: int | None = Field(default=None, ge=18, le=100)
    height_min: int | None = Field(default=None, ge=100, le=250)
    height_max: int | None = Field(default=None, ge=100, le=250)
    education_min: int | None = Field(default=None, ge=1, le=8)
    income_min: float | None = Field(default=None, ge=0, le=1_000_000)
    occupation_requirement: str | None = Field(default=None, max_length=64)
    marriage_accept: Literal["不接受离异", "可接受离异未育", "接受离异带娃", "视情况而定"] | None = None
    housing_requirement: Literal[0, 1, 2] | None = None
    smoking_requirement: Literal[0, 1, 2] | None = None
    wedding_plan: Literal["不限", "计划一年内", "计划两年内", "计划三年内", "时机成熟时结婚", "不婚"] | None = None

    @model_validator(mode="after")
    def validate_pref_ranges(self) -> "WorkspaceMemberEntryCreate":
        if self.age_min is not None and self.age_max is not None and self.age_min > self.age_max:
            raise ValueError("期望年龄下限不能大于上限")
        if self.height_min is not None and self.height_max is not None and self.height_min > self.height_max:
            raise ValueError("期望身高下限不能大于上限")
        return self


class WorkspaceMemberEntryResult(BaseModel):
    member_id: int
    review_status: ReviewStatus
    matchmaker_id: int


class WorkspaceMeetingStatusUpdate(BaseModel):
    """约见申请处理状态流转；待处理=SUBMITTED，已处理=ACCEPTED/DECLINED/CLOSED。"""

    status: Literal["SUBMITTED", "CONTACTED", "ACCEPTED", "DECLINED", "CLOSED"]
    contacts_confirmed: bool = False
    applicant_agreed: bool = False
    target_agreed: bool = False
    reason: str | None = Field(default=None, max_length=200)


class WorkspaceMeetingScheduleCreate(BaseModel):
    """服务红娘为双方接受的约见申请创建约见安排；时间必须晚于当前时刻。"""

    scheduled_at: datetime
    location: str = Field(min_length=1, max_length=128)

    @field_validator("location")
    @classmethod
    def require_location(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("请填写约会地点")
        return value.strip()

    @field_validator("scheduled_at")
    @classmethod
    def require_future_time(cls, value: datetime) -> datetime:
        china = timezone(timedelta(hours=8))
        local = value.astimezone(china).replace(tzinfo=None) if value.tzinfo else value
        if local <= datetime.now(china).replace(tzinfo=None):
            raise ValueError("约见时间必须晚于当前时间")
        return local


class WorkspaceMeetingDirectCreate(WorkspaceMeetingScheduleCreate):
    applicant_user_id: int = Field(ge=1)
    target_user_id: int = Field(ge=1)
    contacts_confirmed: bool
    applicant_agreed: bool
    target_agreed: bool

    @model_validator(mode="after")
    def require_both_consents(self) -> "WorkspaceMeetingDirectCreate":
        if self.applicant_user_id == self.target_user_id:
            raise ValueError("不能安排同一用户与自己约会")
        if not (self.contacts_confirmed and self.applicant_agreed and self.target_agreed):
            raise ValueError("请先联系双方并确认双方均同意约会")
        return self


class WorkspaceMeetingRecordStatusUpdate(BaseModel):
    """约见安排状态流转：进行中=REMINDED/CHECKED_IN，完成=COMPLETED，结束=CANCELLED/NO_SHOW。"""

    status: Literal["REMINDED", "CHECKED_IN", "COMPLETED", "CANCELLED", "NO_SHOW"]
    cancel_reason: str | None = Field(default=None, max_length=200)
    follow_up_content: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_cancel_reason(self) -> "WorkspaceMeetingRecordStatusUpdate":
        if self.status == "COMPLETED" and not (self.follow_up_content and self.follow_up_content.strip()):
            raise ValueError("完成约会前必须填写跟进记录")
        if self.status != "COMPLETED" and self.follow_up_content:
            raise ValueError("请在完成约会时提交跟进记录")
        if self.status in {"CANCELLED", "NO_SHOW"} and not (
            self.cancel_reason and str(self.cancel_reason).strip()
        ):
            raise ValueError("取消或爽约必须填写原因")
        if self.status not in {"CANCELLED", "NO_SHOW"} and self.cancel_reason:
            raise ValueError("仅取消或爽约需要填写原因")
        return self


class WorkspaceIntroductionStatusUpdate(BaseModel):
    """牵线中确认已联系后才能判定结果；待牵线由查看双方电话自动推进。"""

    status: Literal["PENDING", "IN_PROGRESS", "SUCCEEDED", "FAILED"]
    failure_reason: str | None = Field(default=None, max_length=500)
    contact_confirmed: bool = Field(default=False, description="红娘确认已联系双方并了解被牵线方态度；判定成功/失败时必须为true")

    @model_validator(mode="after")
    def require_failure_reason(self) -> "WorkspaceIntroductionStatusUpdate":
        if self.status == "FAILED" and not (self.failure_reason and str(self.failure_reason).strip()):
            raise ValueError("牵线失败必须填写失败原因")
        if self.status != "FAILED" and self.failure_reason:
            raise ValueError("仅牵线失败需要填写失败原因")
        return self


class WorkspaceIntroductionCreate(BaseModel):
    """服务红娘手工添加待牵线记录；不能通过录入绕过双方电话查看和联系确认。"""

    from_user_id: int = Field(ge=1)
    to_user_id: int = Field(ge=1)
    applied_at: date | None = None
    finished_at: date | None = Field(default=None, description="兼容保留，新建待牵线记录必须为null")
    result: Literal["PENDING"] = "PENDING"
    failure_reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_pairs(self) -> "WorkspaceIntroductionCreate":
        if self.from_user_id == self.to_user_id:
            raise ValueError("牵线会员与被牵线会员不能是同一人")
        if self.finished_at is not None or self.failure_reason:
            raise ValueError("新牵线记录必须从待牵线开始，不能填写完成时间或失败原因")
        return self


class WorkspaceIntroductionCreated(BaseModel):
    id: int
    status: Literal["PENDING", "IN_PROGRESS", "SUCCEEDED", "FAILED"]


class WorkspaceGoodNewsItem(BaseModel):
    """后台「运营工具-喜讯管理」录入的一条红娘喜讯（admin_content_item domain=good_news）。"""

    id: int
    stage: str
    from_user_id: int | None
    to_user_id: int | None
    from_name: str
    to_name: str
    from_avatar: str | None
    to_avatar: str | None
    matchmaker_name: str
    meet_duration: str
    images: list[str]
    content: str
    blessing: str
    blessing_count: int
    public: bool
    pending_review: bool
    created_at: datetime | None


class WorkspaceGoodNewsCreate(BaseModel):
    """红娘在「红娘喜讯」里代会员提交的反馈喜讯，落后台 good_news 内容域待审行。"""

    stage: str = Field(min_length=1, max_length=20, description="当前状态，取栏目配置的喜讯分类")
    partner_name: str = Field(min_length=1, max_length=40, description="对方称呼")
    meet_duration: str = Field("", max_length=20, description="相识时间，按月填数字或自由文本")
    content: str = Field("", max_length=1000, description="情感状态描述与对服务的反馈")
    images: list[str] = Field(default_factory=list, max_length=9, description="已上传图片 URL")
    feedback_name: str = Field("", max_length=40)
    consent_confirmed: bool | None = None
    client_request_id: str | None = Field(default=None, min_length=8, max_length=64)


class WorkspaceGoodNewsCreated(BaseModel):
    id: int
    pending_review: bool


class WorkspaceGoodNewsPage(BaseModel):
    items: list[WorkspaceGoodNewsItem]
    page: int
    page_size: int
    total: int
    has_more: bool
    workflow_version: int = 2


class WorkspacePennantItem(BaseModel):
    """锦旗墙的一条（admin_content_item domain=good_news_pennant）。"""

    id: int
    maker: str
    giver: str
    target: str
    words: str
    image_url: str | None
    created_at: datetime | None
    left_words: str = ""
    right_words: str = ""
    date_text: str = ""
    target_user_id: int | None = None
    pending_review: bool = False


class WorkspacePennantPage(BaseModel):
    items: list[WorkspacePennantItem]
    page: int
    page_size: int
    total: int
    has_more: bool
    workflow_version: int = 2


class WorkspacePennantCreate(BaseModel):
    giver: str = Field(min_length=1, max_length=40)
    left_words: str = Field(min_length=1, max_length=8)
    right_words: str = Field(min_length=1, max_length=8)
    date_text: str = Field("", max_length=24)
    image_url: str | None = Field(default=None, max_length=1024)
    consent_confirmed: bool
    client_request_id: str = Field(min_length=8, max_length=64)

    @model_validator(mode="after")
    def validate_gift(self) -> "WorkspacePennantCreate":
        if not self.consent_confirmed:
            raise ValueError("请确认已取得赠送人同意")
        if not all(value.strip() for value in (self.giver, self.left_words, self.right_words)):
            raise ValueError("赠送人与感谢语不能为空")
        if self.image_url and not self.image_url.startswith("/storage/uploads/good-news/"):
            raise ValueError("请使用已上传的锦旗图片")
        return self


class WorkspaceGoodNewsBlessingCreate(BaseModel):
    message: str = Field(min_length=1, max_length=100)

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("请填写祝福语")
        return value.strip()


class WorkspaceGoodNewsBlessingResult(BaseModel):
    blessing_count: int
    blessed: bool
    created: bool


class WorkspaceGoodNewsBlessingItem(BaseModel):
    id: int
    nickname: str
    avatar: str | None
    message: str
    created_at: datetime


class WorkspaceGoodNewsBlessingPage(BaseModel):
    items: list[WorkspaceGoodNewsBlessingItem]
    total: int
    page: int
    has_more: bool
    blessed: bool

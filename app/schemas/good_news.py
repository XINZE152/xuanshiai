"""红娘喜讯（good news）公共端、工作台与管理端契约。

内容主体仍存放在 ``admin_content_item``（domain=good_news），
审核/公开/来源/同意/祝福等流程数据在 good_news_meta 及关系表中。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# 锦旗是独立 Tab，不参与喜讯阶段筛选。
GOOD_NEWS_STAGES: tuple[str, ...] = (
    "牵手成功",
    "恋爱生活",
    "已见父母",
    "已订婚",
    "已领证",
    "已办婚礼",
    "婚后生活",
)

GoodNewsStage = Literal[
    "牵手成功",
    "恋爱生活",
    "已见父母",
    "已订婚",
    "已领证",
    "已办婚礼",
    "婚后生活",
]
ReviewStatus = Literal["PENDING", "APPROVED", "REJECTED"]
PublicStatus = Literal["DRAFT", "PUBLISHED", "UNPUBLISHED"]
DisplayMode = Literal["FULL", "MASKED", "ANONYMOUS"]
VerificationLevel = Literal["PLATFORM_VERIFIED", "MATCHMAKER_FOLLOWUP", "SELF_REPORTED"]
SourceType = Literal[
    "MATCHMAKER_SERVICE",
    "INTRODUCTION",
    "MEETING_REQUEST",
    "MEETING_RECORD",
    "OFFLINE_VIP",
    "SELF_REPORTED",
    "MANUAL",
]
SubmitterRole = Literal["MEMBER", "MATCHMAKER", "ADMIN"]
ConsentRole = Literal["FROM", "TO"]
ConsentSource = Literal["MEMBER", "MATCHMAKER_ATTESTED", "ADMIN"]


class GoodNewsReportCreate(BaseModel):
    """会员自助报喜 / 红娘代登记共用入参。

    会员路由会忽略 ``from_user_id``，一律使用当前登录用户；工作台路由使用
    ``from_user_id``（若提供）或仅登记 ``partner_name``。
    """

    stage: GoodNewsStage
    partner_name: str | None = Field(default=None, max_length=64)
    from_user_id: int | None = Field(default=None, ge=1)
    to_user_id: int | None = Field(default=None, ge=1)
    meet_duration: str | None = Field(default=None, max_length=32, description="兼容旧前端：如 '8'")
    meet_duration_months: int | None = Field(default=None, ge=0, le=1200)
    content: str = Field(min_length=1, max_length=2000)
    images: list[str] = Field(default_factory=list, max_length=9)
    blessing: str | None = Field(default=None, max_length=255)
    display_mode: DisplayMode = "MASKED"
    verification_level: VerificationLevel = "SELF_REPORTED"
    source_type: SourceType = "SELF_REPORTED"
    source_id: int | None = Field(default=None, ge=1)
    consent_from: bool = False
    consent_to: bool = False
    consent_version: str = Field(default="v1", min_length=1, max_length=32)
    consent_scopes: list[str] = Field(
        default_factory=lambda: ["name", "avatar", "photos", "content", "matchmaker"]
    )
    # 兼容旧前端：单个勾选表示会员代双方声明；显式字段优先。
    consent_confirmed: bool | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def apply_legacy_consent(self) -> "GoodNewsReportCreate":
        if self.consent_confirmed is True:
            # 显式传入的 consent_from/consent_to 优先；旧字段只补齐未传的一侧。
            if "consent_from" not in self.model_fields_set:
                self.consent_from = True
            if "consent_to" not in self.model_fields_set:
                self.consent_to = True
        return self

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("正文不能为空")
        return stripped

    @field_validator("partner_name")
    @classmethod
    def normalize_partner_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class GoodNewsItem(BaseModel):
    """公共/工作台共用的扁平 DTO，兼容现有前端字段名。"""

    id: int
    stage: str
    stage_label: str
    from_user_id: int | None = None
    to_user_id: int | None = None
    from_name: str | None = None
    to_name: str | None = None
    from_avatar: str | None = None
    to_avatar: str | None = None
    matchmaker_id: int | None = None
    matchmaker_name: str | None = None
    meet_duration: str | None = None
    meet_duration_months: int | None = None
    meet_duration_text: str | None = None
    images: list[str] = Field(default_factory=list)
    content: str = ""
    blessing: str | None = None
    blessing_count: int = 0
    blessed_by_me: bool = False
    verification_level: str = "SELF_REPORTED"
    display_mode: str = "MASKED"
    review_status: str = "PENDING"
    public_status: str = "DRAFT"
    pending_review: bool = True
    created_at: datetime | None = None
    published_at: datetime | None = None


class GoodNewsPage(BaseModel):
    items: list[GoodNewsItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class GoodNewsConfig(BaseModel):
    title: str = "脱单喜讯"
    description: str = "脱单喜讯"
    share_cover_url: str | None = None
    banner_url: str | None = None
    categories: list[dict] = Field(default_factory=list)
    blessings: list[str] = Field(default_factory=list)


class GoodNewsBlessingResponse(BaseModel):
    news_id: int
    blessing_count: int
    blessed_by_me: bool
    blessed: bool


class GoodNewsReportItem(BaseModel):
    id: int
    news_id: int | None = None
    stage: str
    partner_name: str | None = None
    meet_duration_months: int | None = None
    content: str
    images: list[str] = Field(default_factory=list)
    status: str
    reject_reason: str | None = None
    submitter_role: str = "MEMBER"
    created_at: datetime | None = None
    reviewed_at: datetime | None = None


class GoodNewsReportPage(BaseModel):
    items: list[GoodNewsReportItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class GoodNewsReviewRequest(BaseModel):
    action: Literal["APPROVE", "REJECT", "OFFLINE"]
    reason: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_reason(self) -> "GoodNewsReviewRequest":
        if self.action in {"REJECT", "OFFLINE"} and not (self.reason or "").strip():
            raise ValueError("驳回或下架必须填写原因")
        return self


class GoodNewsConsentRecordRequest(BaseModel):
    role: ConsentRole
    user_id: int | None = Field(default=None, ge=1)
    scopes: list[str] = Field(
        default_factory=lambda: ["name", "avatar", "photos", "content", "matchmaker"]
    )
    consent_version: str = Field(default="v1", min_length=1, max_length=32)
    source: ConsentSource = "ADMIN"


class GoodNewsConsentItem(BaseModel):
    id: int
    news_id: int
    user_id: int | None = None
    role: str
    scopes: list[str] = Field(default_factory=list)
    consent_version: str = ""
    consent_source: str = "MEMBER"
    consented_at: datetime | None = None
    revoked_at: datetime | None = None


class GoodNewsConsentPage(BaseModel):
    items: list[GoodNewsConsentItem]


class GoodNewsPhotoResponse(BaseModel):
    url: str
    content_type: str
    size: int


class GoodNewsPennantItem(BaseModel):
    id: int
    maker: str | None = None
    giver: str | None = None
    target: str | None = None
    words: str | None = None
    image_url: str | None = None
    created_at: datetime | None = None


class GoodNewsPennantPage(BaseModel):
    items: list[GoodNewsPennantItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class GoodNewsAdminItem(BaseModel):
    """管理端审核队列专用行；列表仍兼容 ContentItemPage。"""

    id: int
    title: str
    subtitle: str | None = None
    image_url: str | None = None
    stage: str = ""
    review_status: str = "PENDING"
    public_status: str = "DRAFT"
    display_mode: str = "MASKED"
    verification_level: str = "SELF_REPORTED"
    blessing_count: int = 0
    matchmaker_id: int | None = None
    from_user_id: int | None = None
    to_user_id: int | None = None
    reject_reason: str | None = None
    consent_complete: bool = False
    created_at: datetime | None = None
    published_at: datetime | None = None
    extra: dict = Field(default_factory=dict)

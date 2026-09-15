"""Request and response contracts for the MVP authentication module."""

from __future__ import annotations

from datetime import date
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.profile_tags import ALL_TAG_OPTIONS, TAG_OPTIONS_BY_CATEGORY, LEGACY_TAG_OPTIONS_BY_CATEGORY, PERSONALITY_OPTIONS, MAX_PERSONAL_TAGS, MAX_CUSTOM_TAGS, CUSTOM_TAG_MIN_LENGTH, CUSTOM_TAG_MAX_LENGTH, normalize_custom_tag, validate_personal_tag_selection


MbtiType = Literal[
    "INTJ", "INTP", "ENTJ", "ENTP", "INFJ", "INFP", "ENFJ", "ENFP",
    "ISTJ", "ISFJ", "ESTJ", "ESFJ", "ISTP", "ISFP", "ESTP", "ESFP",
]


class SmsSendRequest(BaseModel):
    phone: str = Field(pattern=r"^1[3-9]\d{9}$", examples=["13812345678"], description="11位大陆手机号")
    purpose: Literal["login", "bind_phone"] = "login"


class SmsSendResponse(BaseModel):
    message: str
    expires_in: int
    retry_after: int


class PhoneLoginRequest(SmsSendRequest):
    code: str = Field(
        min_length=6,
        max_length=6,
        pattern=r"^\d{6}$",
        examples=["123456"],
        description="短信验证码，必须为6位数字",
    )
    device_id: str | None = Field(default=None, max_length=128)
    platform: str | None = Field(default=None, max_length=32)
    app_version: str | None = Field(default=None, max_length=32)


class WechatLoginRequest(BaseModel):
    code: str = Field(min_length=1, max_length=512)
    nickname: str | None = Field(default=None, max_length=64)
    avatar: str | None = Field(default=None, max_length=255)
    device_id: str | None = Field(default=None, max_length=128)
    platform: str | None = Field(default=None, max_length=32)
    app_version: str | None = Field(default=None, max_length=32)


class ExistingAccountLoginRequest(BaseModel):
    """Login an existing local account with its phone and password."""

    phone: str = Field(pattern=r"^1[3-9]\d{9}$")
    password: str = Field(min_length=8, max_length=128)
    device_id: str | None = Field(default=None, max_length=128)
    platform: str | None = Field(default=None, max_length=32)
    app_version: str | None = Field(default=None, max_length=32)


class BindPhoneRequest(PhoneLoginRequest):
    pass


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=512)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    need_bind_phone: bool
    user_id: int


class AgreementAcceptRequest(BaseModel):
    agreement_type: Literal[
        "user_service", "privacy_policy", "safety_pledge", "community_rules"
    ]
    version: str = Field(min_length=1, max_length=32)
    content_hash: str | None = Field(default=None, min_length=64, max_length=64)
    scene: str | None = Field(default=None, max_length=32)


class RealNameRequest(BaseModel):
    real_name: str = Field(min_length=2, max_length=64)
    id_card: str = Field(pattern=r"^[1-9]\d{5}(18|19|20)\d{2}(0[1-9]|1[0-2])"
                               r"(0[1-9]|[12]\d|3[01])\d{3}[0-9Xx]$")


class ProfileUpdateRequest(BaseModel):
    # Avatar is a multipart upload handled by POST /users/me/avatar, not a profile field.
    model_config = ConfigDict(extra="forbid")

    gender: Literal[1, 2] | None = None
    birthday: date | None = None
    is_married: Literal[1, 2, 3] | None = None
    height: int | None = Field(default=None, ge=140, le=220)
    weight: int | None = Field(default=None, ge=40, le=120)
    occupation: str | None = Field(default=None, max_length=128)
    industry: str | None = Field(default=None, max_length=128)
    education_level: int | None = Field(default=None, ge=1, le=8)
    income: float | None = Field(default=None, ge=0, le=1_000_000)
    hometown_province_code: str | None = Field(default=None, max_length=32)
    hometown_city_code: str | None = Field(default=None, max_length=32)
    hometown_district_code: str | None = Field(default=None, max_length=32)
    residence_province_code: str | None = Field(default=None, max_length=32)
    residence_city_code: str | None = Field(default=None, max_length=32)
    residence_district_code: str | None = Field(default=None, max_length=32)
    self_intro: str | None = Field(default=None, max_length=500)
    personal_tags: list[str] | None = Field(
        default=None, max_length=MAX_PERSONAL_TAGS,
        description="兴趣标签，0～10 个不重复的系统或自定义选项；空数组清空，不可与旧标签字段混传",
    )
    custom_tag_categories: dict[str, str] | None = Field(
        default=None,
        description="自定义标签到当前兴趣分区 key 的映射；提交自定义标签时必填",
    )
    interest_tags: list[str] | None = Field(
        default=None,
        min_length=0,
        max_length=5,
        description="兼容字段：0～5 个兴趣选项，新客户端使用 personal_tags",
    )
    personality_tags: list[str] | None = Field(
        default=None,
        min_length=0,
        max_length=5,
        description="兼容字段：0～5 个性格选项，新客户端使用 personal_tags",
    )
    mbti: MbtiType | None = None
    tag_selections: dict[str, list[str]] | None = Field(
        default=None,
        description="扩展标签分类映射，不替代兴趣标签和性格标签",
    )

    @field_validator("personal_tags", "interest_tags", "personality_tags")
    @classmethod
    def validate_tags(cls, value: list[str] | None, info) -> list[str] | None:
        if value is not None:
            if info.field_name == "personal_tags":
                return validate_personal_tag_selection(value)
            if len(value) != len(set(value)):
                raise ValueError("标签不能重复")
            if any(tag not in ALL_TAG_OPTIONS for tag in value):
                raise ValueError("只能选择当前目录中的兴趣标签")
        return value

    @model_validator(mode="after")
    def validate_tag_fields(self) -> ProfileUpdateRequest:
        if "personal_tags" in self.model_fields_set:
            if self.personal_tags is None:
                raise ValueError("personal_tags 不能为 null，清空请传 []")
            if self.model_fields_set & {"interest_tags", "personality_tags", "tag_selections"}:
                raise ValueError("personal_tags 不能与旧标签字段同时提交")
            custom = [tag for tag in self.personal_tags if tag not in ALL_TAG_OPTIONS]
            raw_categories = self.custom_tag_categories or {}
            normalized_categories: dict[str, str] = {}
            for raw_label, category in raw_categories.items():
                label = normalize_custom_tag(raw_label)
                if label in ALL_TAG_OPTIONS:
                    raise ValueError("系统标签无需提交自定义分区")
                if category not in TAG_OPTIONS_BY_CATEGORY:
                    raise ValueError(f"不支持的自定义标签分区: {category}")
                if label.casefold() in {item.casefold() for item in normalized_categories}:
                    raise ValueError("自定义标签分区不能重复")
                normalized_categories[label] = category
            if {tag.casefold() for tag in custom} != {tag.casefold() for tag in normalized_categories}:
                raise ValueError("每个自定义标签都必须选择所属分区，且不能提交多余分区")
            self.custom_tag_categories = {
                tag: next(category for label, category in normalized_categories.items() if label.casefold() == tag.casefold())
                for tag in custom
            }
        elif "custom_tag_categories" in self.model_fields_set:
            raise ValueError("custom_tag_categories 必须与 personal_tags 同时提交")
        if any(tag in PERSONALITY_OPTIONS for tag in self.interest_tags or []):
            raise ValueError("性格标签请通过 personal_tags 或 personality_tags 提交")
        if any(tag not in PERSONALITY_OPTIONS for tag in self.personality_tags or []):
            raise ValueError("personality_tags 只能包含性格标签")
        return self

    @field_validator("tag_selections")
    @classmethod
    def validate_tag_selections(cls, value: dict[str, list[str]] | None) -> dict[str, list[str]] | None:
        if value is None:
            return value
        for category, selected in value.items():
            current = TAG_OPTIONS_BY_CATEGORY.get(category, frozenset())
            legacy = LEGACY_TAG_OPTIONS_BY_CATEGORY.get(category, frozenset())
            if not current and not legacy:
                raise ValueError(f"不支持的标签分类: {category}")
            options = current | legacy
            if not selected or len(selected) > 5:
                raise ValueError("每个标签分类最多选择5项")
            if len(selected) != len(set(selected)) or any(item not in options for item in selected):
                raise ValueError("标签必须来自对应分类且不能重复")
        return value


class NicknameUpdateRequest(BaseModel):
    nickname: str = Field(min_length=1, max_length=64, description="用户昵称，去除首尾空格后不能为空")

    @field_validator("nickname", mode="before")
    @classmethod
    def normalize_nickname(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("昵称必须是字符串")
        normalized = value.strip()
        if not normalized:
            raise ValueError("昵称不能为空或只包含空格")
        return normalized


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    phone_masked: str | None
    nickname: str | None
    avatar: str | None
    status: int
    phone_verified: bool
    realname_status: int
    face_verified: int = Field(
        description="人脸认证状态：0未认证、1通过、2失败",
        examples=[1],
    )
    need_bind_phone: bool


class ProfileResponse(BaseModel):
    user_id: int
    nickname: str | None
    gender: int | None
    birthday: date | None
    age: int | None
    is_married: int | None
    height: int | None
    weight: int | None
    occupation: str | None
    industry: str | None
    education_level: int | None
    income: float | None
    income_display: str | None = None
    hometown_province_code: str | None
    hometown_city_code: str | None
    hometown_district_code: str | None
    hometown_display: str | None = None
    residence_province_code: str | None
    residence_city_code: str | None
    residence_district_code: str | None
    residence_display: str | None = None
    self_intro: str | None
    personal_tags: list[str] = Field(default_factory=list, description="合并去重后的有效兴趣标签")
    custom_tags: list[str] = Field(default_factory=list, description="本人创建并通过校验的自定义标签")
    custom_tag_categories: dict[str, str] = Field(default_factory=dict, description="自定义标签到兴趣分区 key 的映射")
    legacy_tags: list[str] = Field(default_factory=list, description="仅本人可见的待整理旧标签，公开响应为空")
    interest_tags: list[str]
    personality_tags: list[str]
    mbti: MbtiType | None
    avatar: str | None
    background_wall: str | None
    tag_selections: dict[str, list[str]]
    photos: list[ProfileMediaResponse]
    video: ProfileMediaResponse | None
    completion_score: float


class NicknameUpdateResponse(BaseModel):
    user_id: int
    nickname: str
    updated_at: datetime


class CompletionResponse(BaseModel):
    score: float
    missing_items: list[str]
    items: list[CompletionItemResponse]
    can_browse: bool
    can_apply: bool
    can_chat: bool


class CertificationSummary(BaseModel):
    status: Literal[0, 1, 2, 3]
    label: str


class MembershipSummary(BaseModel):
    is_vip: bool
    package_type: str | None
    expires_at: datetime | None


class OverviewShortcuts(BaseModel):
    can_browse: bool
    can_apply: bool
    can_chat: bool
    can_edit_profile: bool = True
    can_manage_media: bool = True


class ProfileOverviewResponse(BaseModel):
    user_id: int
    nickname: str | None
    avatar: str | None
    account_status: Literal[1, 2, 3]
    completion_score: float
    certification: CertificationSummary
    membership: MembershipSummary
    unread_notification_count: int
    incoming_application_count: int
    outgoing_application_count: int
    match_count: int
    visitor_count: int
    favorite_count: int
    favorite_received_count: int
    superlike_sent_count: int
    superlike_received_count: int
    shortcuts: OverviewShortcuts


class CompletionItemResponse(BaseModel):
    key: str
    label: str
    weight: int
    completed: bool


class TagCategoryResponse(BaseModel):
    key: str
    label: str
    options: list[str]


class CustomTagPolicyResponse(BaseModel):
    enabled: bool = True
    max_tags: int = MAX_CUSTOM_TAGS
    min_length: int = CUSTOM_TAG_MIN_LENGTH
    max_length: int = CUSTOM_TAG_MAX_LENGTH
    category_required: bool = True


class TagOptionsResponse(BaseModel):
    max_tags: int = MAX_PERSONAL_TAGS
    recommended_min: int = 3
    version: str
    catalog_revision: str
    custom: CustomTagPolicyResponse = Field(default_factory=CustomTagPolicyResponse)
    categories: list[TagCategoryResponse]


class ProfileMediaResponse(BaseModel):
    id: int
    media_type: Literal["avatar", "background", "photo", "video"]
    file_url: str
    thumbnail_url: str | None
    sort_order: int
    is_primary: bool
    duration_seconds: int | None


class ProfilePreviewResponse(BaseModel):
    preview_notice: str
    profile: ProfileResponse


class IntroTemplateResponse(BaseModel):
    key: str
    title: str
    content: str


class PreferenceUpdateRequest(BaseModel):
    age_min: int | None = Field(default=None, ge=18, le=100)
    age_max: int | None = Field(default=None, ge=18, le=100)
    height_min: int | None = Field(default=None, ge=100, le=250)
    height_max: int | None = Field(default=None, ge=100, le=250)
    education_min: int | None = Field(default=None, ge=1, le=8)
    income_min: float | None = Field(default=None, ge=0, le=1_000_000)
    marriage_status: Literal[0, 1, 2, 3] | None = None
    preferred_province_code: str | None = Field(default=None, max_length=32)
    preferred_city_codes: list[str] | None = Field(default=None, max_length=20)
    accept_long_distance: bool | None = None
    accept_cross_province: bool | None = None
    housing_requirement: Literal[0, 1, 2] | None = None
    smoking_requirement: Literal[0, 1, 2] | None = None
    drinking_requirement: Literal[0, 1, 2] | None = None
    dating_goal: Literal["倾向恋爱", "倾向结婚"] | None = None
    meeting_pace: Literal["倾向尽快见面", "真诚高效", "了解清楚再见面", "都行看对方的见面意愿"] | None = None
    children_intention: Literal["想要孩子", "看情况决定是否要孩子", "不想要孩子"] | None = None
    extra_requirement: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_ranges(self):
        if self.age_min is not None and self.age_max is not None and self.age_min > self.age_max:
            raise ValueError("期望年龄下限不能大于上限")
        if self.height_min is not None and self.height_max is not None and self.height_min > self.height_max:
            raise ValueError("期望身高下限不能大于上限")
        return self


class PreferenceResponse(BaseModel):
    user_id: int
    age_min: int | None
    age_max: int | None
    height_min: int | None
    height_max: int | None
    education_min: int | None
    income_min: float | None
    marriage_status: int | None
    preferred_province_code: str | None
    preferred_city_codes: list[str]
    accept_long_distance: bool
    accept_cross_province: bool
    housing_requirement: int | None
    smoking_requirement: int | None
    drinking_requirement: int | None
    dating_goal: str | None
    meeting_pace: str | None
    children_intention: str | None
    extra_requirement: str | None


class PhotoOrderRequest(BaseModel):
    media_ids: list[int] = Field(min_length=1, max_length=9)

    @field_validator("media_ids")
    @classmethod
    def validate_unique_ids(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("相册排序不能包含重复图片")
        return value


class RegistrationIntentResponse(BaseModel):
    intent_type: str
    label: str
    description: str


class RegistrationIntentUpdate(BaseModel):
    intent_type: Literal["self_match", "parent_match", "companion"]
    source: Literal["register", "profile"] = "register"


class MatchmakerSuccessCase(BaseModel):
    description: str = Field(min_length=1, max_length=1000)
    images: list[str] = Field(default_factory=list, max_length=3)


class MatchmakerApplicationDetails(BaseModel):
    """审核资料；仅返回给申请人本人或管理员，不用于公开红娘资料。"""

    wechat: str | None = Field(default=None, max_length=128)
    avatar: str | None = Field(default=None, max_length=512)
    specialties: list[str] = Field(default_factory=list, max_length=5)
    expected_price: float | None = Field(default=None, ge=0, le=1000000)
    success_cases: list[MatchmakerSuccessCase] = Field(default_factory=list, max_length=3)

    @field_validator("specialties")
    @classmethod
    def validate_specialties(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("擅长领域不能重复")
        return cleaned


class MatchmakerApplicationCreate(BaseModel):
    application_type: Literal["promoter", "partner", "service_matchmaker"]
    real_name: str = Field(min_length=2, max_length=64)
    phone: str = Field(pattern=r"^1[3-9]\d{9}$")
    intro: str = Field(min_length=10, max_length=2000)
    cert_images: list[str] = Field(default_factory=list, max_length=6)
    application_details: MatchmakerApplicationDetails = Field(default_factory=MatchmakerApplicationDetails)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_details(cls, value):
        """Accept the existing top-level frontend fields during migration."""
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        details = dict(normalized.get("application_details") or {})
        legacy_fields = ("wechat", "avatar", "specialties", "expected_price", "success_cases")
        for field_name in legacy_fields:
            if field_name not in details and field_name in normalized:
                details[field_name] = normalized[field_name]
        normalized["application_details"] = details
        return normalized


class MatchmakerApplicationResponse(BaseModel):
    id: int
    application_type: str
    status: int
    real_name: str
    phone_masked: str
    intro: str
    cert_images: list[str]
    application_details: MatchmakerApplicationDetails
    fail_reason: str | None
    created_at: str
    reviewed_at: str | None


class MatchmakerApplicationAdminPage(BaseModel):
    items: list[MatchmakerApplicationResponse]
    page: int
    page_size: int
    total: int
    has_more: bool


class MatchmakerReviewRequest(BaseModel):
    status: Literal[1, 2, 3]
    fail_reason: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def require_reason_for_rejection_or_suspension(self):
        if self.status in (2, 3) and not self.fail_reason:
            raise ValueError("驳回或暂停申请时必须填写原因")
        return self

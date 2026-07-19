"""Request and response contracts for the MVP authentication module."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SmsSendRequest(BaseModel):
    phone: str = Field(pattern=r"^1[3-9]\d{9}$", examples=["17870810285"], description="11位大陆手机号")
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
    gender: Literal[1, 2] | None = None
    birthday: date | None = None
    height: int | None = Field(default=None, ge=100, le=250)
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
    self_intro: str | None = Field(default=None, max_length=1000)
    interest_tags: list[str] | None = Field(default=None, max_length=20)
    personality_tags: list[str] | None = Field(default=None, max_length=20)

    @field_validator("interest_tags", "personality_tags")
    @classmethod
    def validate_tags(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and any(not tag.strip() or len(tag) > 32 for tag in value):
            raise ValueError("标签不能为空且长度不能超过32个字符")
        return value


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    phone_masked: str | None
    nickname: str | None
    avatar: str | None
    status: int
    phone_verified: bool
    realname_status: int
    need_bind_phone: bool


class ProfileResponse(BaseModel):
    user_id: int
    gender: int | None
    birthday: date | None
    age: int | None
    height: int | None
    occupation: str | None
    industry: str | None
    education_level: int | None
    income: float | None
    hometown_province_code: str | None
    hometown_city_code: str | None
    hometown_district_code: str | None
    residence_province_code: str | None
    residence_city_code: str | None
    residence_district_code: str | None
    self_intro: str | None
    interest_tags: list[str]
    personality_tags: list[str]
    completion_score: float


class CompletionResponse(BaseModel):
    score: float
    missing_items: list[str]
    can_browse: bool
    can_apply: bool
    can_chat: bool


class RegistrationIntentResponse(BaseModel):
    intent_type: str
    label: str
    description: str


class RegistrationIntentUpdate(BaseModel):
    intent_type: Literal["self_match", "parent_match", "companion"]
    source: Literal["register", "profile"] = "register"


class MatchmakerApplicationCreate(BaseModel):
    application_type: Literal["promoter", "partner", "service_matchmaker"]
    real_name: str = Field(min_length=2, max_length=64)
    phone: str = Field(pattern=r"^1[3-9]\d{9}$")
    intro: str = Field(min_length=10, max_length=2000)
    cert_images: list[str] = Field(default_factory=list, max_length=6)


class MatchmakerApplicationResponse(BaseModel):
    id: int
    application_type: str
    status: int
    real_name: str
    phone_masked: str
    intro: str
    cert_images: list[str]
    fail_reason: str | None
    created_at: str
    reviewed_at: str | None


class MatchmakerReviewRequest(BaseModel):
    status: Literal[1, 2, 3]
    fail_reason: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def require_reason_for_rejection_or_suspension(self):
        if self.status in (2, 3) and not self.fail_reason:
            raise ValueError("驳回或暂停申请时必须填写原因")
        return self

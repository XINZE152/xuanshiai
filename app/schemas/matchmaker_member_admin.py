"""Member administration contracts for the independent back office."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

def _adult_cutoff(today: date) -> date:
    try:
        return today.replace(year=today.year - 18)
    except ValueError:
        return today.replace(year=today.year - 18, day=28)


class MatchmakerMemberCreate(BaseModel):
    phone: str = Field(pattern=r"^1[3-9]\d{9}$")
    nickname: str = Field(min_length=1, max_length=64)
    gender: Literal[1, 2]
    birthday: date | None = None
    is_married: Literal[1, 2, 3] | None = None
    avatar: str | None = Field(default=None, max_length=255)
    remark: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_adult(self) -> "MatchmakerMemberCreate":
        if self.birthday and self.birthday > _adult_cutoff(date.today()):
            raise ValueError("会员必须年满18周岁")
        return self


class MatchmakerMemberUpdate(BaseModel):
    nickname: str | None = Field(default=None, min_length=1, max_length=64)
    gender: Literal[1, 2] | None = None
    birthday: date | None = None
    is_married: Literal[1, 2, 3] | None = None
    avatar: str | None = Field(default=None, max_length=255)
    income: float | None = Field(default=None, ge=0, le=1_000_000)
    height: int | None = Field(default=None, ge=80, le=250)
    weight: int | None = Field(default=None, ge=40, le=120)
    zodiac: str | None = Field(default=None, max_length=16)
    household: str | None = Field(default=None, max_length=128)
    ethnicity: str | None = Field(default=None, max_length=32)
    house: str | None = Field(default=None, max_length=32)
    car: str | None = Field(default=None, max_length=32)
    smoking: str | None = Field(default=None, max_length=32)
    hometown_province_code: str | None = Field(default=None, max_length=32)
    hometown_city_code: str | None = Field(default=None, max_length=32)
    hometown_district_code: str | None = Field(default=None, max_length=32)
    residence_province_code: str | None = Field(default=None, max_length=32)
    residence_city_code: str | None = Field(default=None, max_length=32)
    residence_district_code: str | None = Field(default=None, max_length=32)
    household_province_code: str | None = Field(default=None, max_length=32)
    household_city_code: str | None = Field(default=None, max_length=32)
    household_district_code: str | None = Field(default=None, max_length=32)
    hometown: str | None = Field(default=None, max_length=128)
    residence: str | None = Field(default=None, max_length=128)
    education: str | None = Field(default=None, max_length=64)
    job: str | None = Field(default=None, max_length=128)
    intention_level: Literal[1, 2, 3] | None = Field(default=None, description="客户意向：1低、2中、3高")
    auth_status: Literal[0, 1, 2, 3] | None = Field(default=None, description="认证审核状态：0待审、1审核中、2通过、3未通过")
    tags: dict[str, list[str]] | list[str] | None = None
    self_intro: str | None = Field(default=None, max_length=500)
    ideal_partner: str | None = Field(default=None, max_length=2000)
    wechat: str | None = Field(default=None, max_length=128)
    remark: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_update(self) -> "MatchmakerMemberUpdate":
        if not self.model_dump(exclude_unset=True):
            raise ValueError("至少提供一个需要修改的字段")
        return self


class MatchmakerMemberAdminItem(BaseModel):
    id: int
    nickname: str | None
    phone_masked: str | None
    gender: int | None
    status: Literal[1, 2, 3]
    is_vip: bool
    vip_end_at: datetime | None
    matchmaker_id: int | None
    created_at: datetime
    updated_at: datetime | None


class CertificationMaterial(BaseModel):
    id: int
    url: str
    thumbnail_url: str | None
    expires_at: datetime | None


class CertificationDetail(BaseModel):
    user_id: int
    kind: Literal["education", "house", "marriage"]
    status: Literal[0, 1, 2, 3]
    submitted_at: datetime | None
    reviewed_at: datetime | None
    fail_reason: str | None
    value: str | None
    material_urls: list[CertificationMaterial]
    reviewer_id: int | None
    audit_history: list[dict[str, object]]


class CertificationsAdminResponse(BaseModel):
    education: CertificationDetail
    house: CertificationDetail
    marriage: CertificationDetail


class MemberAuditLogItem(BaseModel):
    id: int
    action: str
    resource_type: str
    resource_id: int | None
    reason: str | None
    created_at: datetime

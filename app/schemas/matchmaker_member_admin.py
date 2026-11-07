"""Member administration contracts for the independent back office."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# 婚况编码：1未婚 2离异(兼容旧值) 3丧偶 4离婚未育 5离异未带孩 6离异带女孩 7离异带男孩
MaritalStatus = Literal[1, 2, 3, 4, 5, 6, 7]


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
    is_married: MaritalStatus | None = None
    avatar: str | None = Field(default=None, max_length=255)
    # 会员登记来源，写入 users.source；后台建档默认「后台添加」。
    source: str | None = Field(default="后台添加", max_length=32)
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
    is_married: MaritalStatus | None = None
    avatar: str | None = Field(default=None, max_length=255)
    income: float | None = Field(default=None, ge=0, le=1_000_000)
    height: int | None = Field(default=None, ge=80, le=250)
    weight: int | None = Field(default=None, ge=30, le=150)
    constellation: str | None = Field(default=None, max_length=16)
    zodiac: str | None = Field(default=None, max_length=16)
    household: str | None = Field(default=None, max_length=128)
    ethnicity: str | None = Field(default=None, max_length=32)
    house: str | None = Field(default=None, max_length=32)
    car: str | None = Field(default=None, max_length=32)
    smoking: str | None = Field(default=None, max_length=32)
    drinking: str | None = Field(default=None, max_length=32)
    religion: str | None = Field(default=None, max_length=32)
    marriage_plan: str | None = Field(default=None, max_length=64)
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
    school: str | None = Field(default=None, max_length=128)
    job: str | None = Field(default=None, max_length=128)
    company: str | None = Field(default=None, max_length=128)
    match_status: Literal[1, 2, 3, 4, 5] | None = Field(
        default=None,
        description="交友展示状态：1公开相亲、2委托红娘、3完全私密、4停止相亲、5已经脱单",
    )
    intention_level: Literal[1, 2, 3] | None = Field(default=None, description="客户意向：1低、2中、3高")
    auth_status: Literal[0, 1, 2, 3] | None = Field(default=None, description="认证审核状态：0待审、1审核中、2通过、3未通过")
    wechat_added: Literal[0, 1, 2] | None = Field(default=None, description="加微信结果：0未添加、1已加上、2没加上")
    # 超级管理里的两个推荐位开关。库里是 tinyint(0/1)，用 bool 收口更贴合界面语义。
    promoted: bool | None = Field(default=None, description="置顶推荐：true 置顶中 / false 未置顶")
    newcomer: bool | None = Field(default=None, description="新人推荐：true 推荐中 / false 不推荐")
    tags: dict[str, list[str]] | list[str] | None = None
    self_intro: str | None = Field(default=None, max_length=500)
    ideal_partner: str | None = Field(default=None, max_length=2000)
    wechat: str | None = Field(default=None, max_length=128)
    # 手机号是登录标识与唯一键，改它会影响会员登录，因此单独做唯一性校验（见 update_member）。
    phone: str | None = Field(default=None, pattern=r"^1\d{10}$", description="11 位手机号")
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

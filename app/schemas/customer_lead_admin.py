"""Customer lead contracts for the back office."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.auth import PreferenceResponse


LeadStatus = Literal["NEW", "CONTACTED", "INTENDED", "CONVERTED", "LOST", "CLOSED"]
# 客源审核状态：active 有效 / pending 待核 / rejected 无效
LeadAuditStatus = Literal["active", "pending", "rejected"]
# 客源性别（A13）：0未知 1男 2女，与 users.gender 同语义
LeadGender = Literal[0, 1, 2]


def _strip_contact(value: object) -> object:
    """联系方式归一化：去首尾空白，空串视为未提供（None）。"""
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    return value


class _LeadProfileFields(BaseModel):
    """客源「本人资料」字段（对标站新建/详情表单）。

    全部可选、向后兼容：Create/Response 默认 None；Update 靠 `exclude_unset` 做部分更新。
    口径对齐账号本人资料（user_profile/user_auth/users）：枚举类存 varchar 原值
    （档位字典见 platform_base_data），age/height/weight int，income 档位 varchar，
    photos JSON；birth_date 存日期、age 由服务层派生。列不存在时服务层静默降级。
    """

    real_name: str | None = Field(default=None, max_length=64, description="真实姓名")
    birth_date: date | None = Field(default=None, description="出生日期（age 由此派生）")
    marriage: str | None = Field(default=None, max_length=32, description="婚姻状况")
    occupation: str | None = Field(default=None, max_length=128, description="职业")
    income: str | None = Field(default=None, max_length=32, description="年收入档位")
    height: int | None = Field(default=None, ge=50, le=250, description="身高cm")
    weight: int | None = Field(default=None, ge=20, le=300, description="体重kg")
    education: str | None = Field(default=None, max_length=32, description="学历")
    hometown: str | None = Field(default=None, max_length=128, description="家乡")
    residence: str | None = Field(default=None, max_length=128, description="现居地")
    house: str | None = Field(default=None, max_length=32, description="购房情况")
    car: str | None = Field(default=None, max_length=32, description="购车情况")
    smoking: str | None = Field(default=None, max_length=32, description="吸烟情况")
    drinking: str | None = Field(default=None, max_length=32, description="饮酒情况")
    ethnicity: str | None = Field(default=None, max_length=32, description="民族")
    school: str | None = Field(default=None, max_length=128, description="毕业学校")
    company: str | None = Field(default=None, max_length=128, description="工作单位")
    photos: list[str] | None = Field(default=None, description="相册图片URL列表")


class CustomerLeadCreate(_LeadProfileFields):
    name: str = Field(min_length=1, max_length=128)
    phone: str | None = Field(default=None, max_length=32)
    wechat: str | None = Field(default=None, max_length=128)
    source: str = Field(min_length=1, max_length=64)
    intention_level: Literal[1, 2, 3, 4, 5, 6, 7] = 1
    gender: LeadGender = Field(default=0, description="性别 0未知 1男 2女（A13）")
    remark: str | None = Field(default=None, max_length=2000)
    promoter_id: int | None = Field(default=None, ge=1, description="推广红娘用户ID")
    audit_status: LeadAuditStatus = "active"
    auto_assign: bool = Field(
        default=False,
        description="true=创建即按分派配置(strategy)自动归属红娘；false=待分派。选不到人时保持未分派，响应 assigned=false",
    )

    @field_validator("phone", "wechat", mode="before")
    @classmethod
    def normalize_contact(cls, value: object) -> object:
        return _strip_contact(value)

    @model_validator(mode="after")
    def require_contact(self) -> "CustomerLeadCreate":
        if not self.phone and not self.wechat:
            raise ValueError("phone 或 wechat 至少提供一个")
        return self


class CustomerLeadUpdate(_LeadProfileFields):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    phone: str | None = Field(default=None, max_length=32)
    wechat: str | None = Field(default=None, max_length=128)
    intention_level: Literal[1, 2, 3, 4, 5, 6, 7] | None = None
    gender: LeadGender | None = None
    status: LeadStatus | None = None
    remark: str | None = Field(default=None, max_length=2000)
    next_follow_at: datetime | None = None
    promoter_id: int | None = Field(default=None, ge=1)
    audit_status: LeadAuditStatus | None = None

    @field_validator("phone", "wechat", mode="before")
    @classmethod
    def normalize_contact(cls, value: object) -> object:
        return _strip_contact(value)

    @model_validator(mode="after")
    def require_update(self) -> "CustomerLeadUpdate":
        # 部分更新：任意字段（含新增资料字段）显式给出非 None 值即可；
        # 仅提交空白联系方式（归一化后为 None）等价于没有修改项 → 422。
        if all(value is None for value in self.model_dump().values()):
            raise ValueError("至少提供一个需要修改的字段")
        return self


class CustomerLeadAssignment(BaseModel):
    matchmaker_id: int | None = Field(default=None, ge=1)
    organization_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def require_owner(self) -> "CustomerLeadAssignment":
        if self.matchmaker_id is None and self.organization_id is None:
            raise ValueError("至少指定红娘或门店")
        return self


class CustomerLeadFollowUpCreate(BaseModel):
    method: Literal["PHONE", "WECHAT", "VISIT", "OTHER"]
    content: str = Field(min_length=1, max_length=2000)
    intention_level: Literal[1, 2, 3, 4, 5, 6, 7] | None = None
    next_follow_at: datetime | None = None


class CustomerLeadFollowUp(BaseModel):
    id: int
    lead_id: int
    method: str
    content: str
    intention_level: int | None
    next_follow_at: datetime | None
    created_by: int
    created_at: datetime
    # 2026-10-29 补（客源跟进附件，对齐会员侧 member_follow_up）：
    # 图片 URL 列表 + 录音地址 + 录音时长；旧库未迁移/无附件时缺列降级为空。
    images: list[str] = []
    voice_url: str | None = None
    voice_duration_sec: int | None = None


class CustomerLead(_LeadProfileFields):
    id: int
    name: str
    phone: str | None
    wechat: str | None
    source: str
    intention_level: Literal[1, 2, 3, 4, 5, 6, 7]
    gender: LeadGender = 0
    # age 只读：由服务层从 birth_date 派生（无 birth_date 时回退 raw age 列），不接受写入。
    age: int | None = Field(default=None, description="年龄（由出生日期派生，只读）")
    status: LeadStatus
    audit_status: LeadAuditStatus = "active"
    matchmaker_id: int | None
    organization_id: int | None
    promoter_id: int | None = None
    next_follow_at: datetime | None
    converted_user_id: int | None
    remark: str | None
    created_by: int
    created_at: datetime
    updated_at: datetime


class CustomerLeadCreateResult(CustomerLead):
    """录入 / 智能分派专用响应：在读模型基础上附带本次是否成功分派。

    仅建单与 auto-assign 端点返回，列表/详情仍用精简的 CustomerLead，避免所有读取
    响应都多出 assigned 字段。assigned=true 表示本次已写入 matchmaker_id；
    false 表示按策略未选到红娘（含 strategy=none/未配置/停用/池为空），保持未分派。
    """

    assigned: bool


class LeadAutoAssignRequest(BaseModel):
    """单条重算请求。force=false 时仅对当前未分派的客源生效；
    force=true 覆盖已有归属，按策略重新选红娘。"""

    force: bool = Field(default=False, description="true=即使已有归属也按策略重算并覆盖")


class CustomerLeadPage(BaseModel):
    items: list[CustomerLead]
    page: int
    page_size: int
    total: int
    has_more: bool


class CustomerLeadStatistics(BaseModel):
    total: int
    new_count: int
    contacted_count: int
    intended_count: int
    converted_count: int
    lost_count: int


class CustomerLeadAbandonRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class CustomerLeadRestoreRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class CustomerLeadBatchDeleteRequest(BaseModel):
    """批量软删除客源线索（A16）。lead_ids 1-200 个正整数，重复自动去重。"""

    lead_ids: list[int] = Field(min_length=1, max_length=200)


class CustomerLeadBatchDeleteFailure(BaseModel):
    id: int
    error: str


class CustomerLeadBatchDeleteResult(BaseModel):
    """部分成功语义：越权/不存在/已删除的归入 failed，不整体报错。"""

    deleted: list[int] = Field(default_factory=list)
    failed: list[CustomerLeadBatchDeleteFailure] = Field(default_factory=list)


class CustomerLeadConvertRequest(BaseModel):
    gender: Literal[1, 2] | None = None
    remark: str | None = Field(default=None, max_length=500)


class CustomerLeadConvertResult(BaseModel):
    lead: CustomerLead
    user_id: int
    review_status: Literal["PENDING"] = "PENDING"
    replayed: bool = False


class CustomerLeadAbandonment(BaseModel):
    id: int
    lead_id: int
    reason: str
    abandoned_by: int
    abandoned_at: datetime
    restored_by: int | None
    restored_at: datetime | None
    restore_reason: str | None


class CustomerLeadImportRow(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    phone: str | None = Field(default=None, max_length=32)
    wechat: str | None = Field(default=None, max_length=128)
    source: str = Field(min_length=1, max_length=64)
    intention_level: Literal[1, 2, 3, 4, 5, 6, 7] = 1
    gender: LeadGender = Field(default=0, description="性别 0未知 1男 2女（A13）")
    remark: str | None = Field(default=None, max_length=2000)


class CustomerLeadBatchImportRequest(BaseModel):
    rows: list[CustomerLeadImportRow] = Field(min_length=1, max_length=1000)
    dup_mode: Literal["skip", "append"] = "skip"


class CustomerLeadBatchImportResult(BaseModel):
    created: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = Field(default_factory=list)


class CustomerLeadImportSummary(CustomerLeadBatchImportResult):
    """文件导入结果：在批量导入结果上补充总行数，便于前端 Step 4 展示。"""

    total: int = 0


class CustomerLeadOption(BaseModel):
    """下拉字典项：value 提交给后端，label 供前端展示。"""

    value: str
    label: str


class CustomerLeadOptions(BaseModel):
    """客源线索页面下拉字典（导入设置 / 筛选）。"""

    sources: list[CustomerLeadOption] = Field(default_factory=list)
    matchmakers: list[CustomerLeadOption] = Field(default_factory=list)
    promoters: list[CustomerLeadOption] = Field(default_factory=list)


class CustomerLeadFollowUpRecord(BaseModel):
    """全局客源跟进记录行（A13），代替前端 N+1 聚合。"""

    id: int
    lead_id: int
    lead_name: str
    lead_source: str
    lead_status: str
    method: str
    content: str
    intention_level: int | None
    next_follow_at: datetime | None
    matchmaker_id: int | None
    promoter_id: int | None
    created_by: int
    created_at: datetime


class CustomerLeadFollowUpRecordPage(BaseModel):
    items: list[CustomerLeadFollowUpRecord]
    page: int
    page_size: int
    total: int
    has_more: bool


class CustomerLeadFollowBucket(BaseModel):
    """跟进全览顶部时间卡片计数（A13）。"""

    today: int = 0
    yesterday: int = 0
    last_3_days: int = 0
    this_week: int = 0
    last_week: int = 0
    this_month: int = 0
    last_month: int = 0


class MatchmakerFollowStat(BaseModel):
    """按红娘的客源跟进统计（A13，数据报表「跟进总条数/本月跟进条数」）。"""

    matchmaker_id: int
    matchmaker_name: str
    total_count: int = 0
    month_count: int = 0
    unfollowed_count: int = 0


class CustomerLeadPromoterStat(BaseModel):
    """推广红娘获客统计（A13，含男/女拆分，需 gender 列已迁移）。"""

    promoter_id: int
    promoter_name: str
    total: int = 0
    male: int = 0
    female: int = 0
    valid_male: int = 0
    valid_female: int = 0
    pending: int = 0
    invalid: int = 0


class LeadPreferenceResponse(BaseModel):
    """客源线索「择偶要求」读写响应。

    字段口径与 C 端 ``PreferenceResponse`` 完全一致。存储位置随入库状态不同一：
    已入库（``converted_user_id`` 非空）读写账号那份 ``user_partner_preference``；
    未入库读写 ``customer_lead_preference`` 暂存表（``converted_user_id`` 为 None、
    内层 ``preference.user_id`` 为 0），入库时由服务搬运至账号。
    两种情况 ``editable`` 均为 True（未入库现已可维护）；无暂存行时 ``preference``
    返回空默认值（非 None）。前端用 ``converted_user_id`` 区分是否已入库。
    """

    lead_id: int
    converted_user_id: int | None = None
    editable: bool = False
    preference: PreferenceResponse | None = None

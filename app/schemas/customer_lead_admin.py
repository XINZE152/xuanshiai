"""Customer lead contracts for the back office."""

from datetime import datetime
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


class CustomerLeadCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    phone: str | None = Field(default=None, max_length=32)
    wechat: str | None = Field(default=None, max_length=128)
    source: str = Field(min_length=1, max_length=64)
    intention_level: Literal[1, 2, 3, 4, 5, 6, 7] = 1
    gender: LeadGender = Field(default=0, description="性别 0未知 1男 2女（A13）")
    remark: str | None = Field(default=None, max_length=2000)
    promoter_id: int | None = Field(default=None, ge=1, description="推广红娘用户ID")
    audit_status: LeadAuditStatus = "active"

    @field_validator("phone", "wechat", mode="before")
    @classmethod
    def normalize_contact(cls, value: object) -> object:
        return _strip_contact(value)

    @model_validator(mode="after")
    def require_contact(self) -> "CustomerLeadCreate":
        if not self.phone and not self.wechat:
            raise ValueError("phone 或 wechat 至少提供一个")
        return self


class CustomerLeadUpdate(BaseModel):
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
        if all(value is None for value in (self.name, self.phone, self.wechat, self.intention_level, self.gender, self.status, self.remark, self.next_follow_at, self.promoter_id, self.audit_status)):
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


class CustomerLead(BaseModel):
    id: int
    name: str
    phone: str | None
    wechat: str | None
    source: str
    intention_level: Literal[1, 2, 3, 4, 5, 6, 7]
    gender: LeadGender = 0
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

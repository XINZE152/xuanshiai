"""Customer lead contracts for the back office."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


LeadStatus = Literal["NEW", "CONTACTED", "INTENDED", "CONVERTED", "LOST", "CLOSED"]
# 客源审核状态：active 有效 / pending 待核
LeadAuditStatus = Literal["active", "pending"]


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
    intention_level: Literal[1, 2, 3] = 1
    remark: str | None = Field(default=None, max_length=2000)
    promoter_id: int | None = Field(default=None, ge=1, description="推广红娘用户ID")
    audit_status: LeadAuditStatus = "active"
    tags: list[str] = Field(default_factory=list, max_length=20, description="客源标签名称")

    @field_validator("phone", "wechat", mode="before")
    @classmethod
    def normalize_contact(cls, value: object) -> object:
        return _strip_contact(value)

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tags(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def require_contact(self) -> "CustomerLeadCreate":
        if not self.phone and not self.wechat:
            raise ValueError("phone 或 wechat 至少提供一个")
        return self


class CustomerLeadUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    phone: str | None = Field(default=None, max_length=32)
    wechat: str | None = Field(default=None, max_length=128)
    intention_level: Literal[1, 2, 3] | None = None
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
        if all(value is None for value in (self.name, self.phone, self.wechat, self.intention_level, self.status, self.remark, self.next_follow_at, self.promoter_id, self.audit_status)):
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
    intention_level: Literal[1, 2, 3] | None = None
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
    intention_level: Literal[1, 2, 3]
    status: LeadStatus
    audit_status: LeadAuditStatus = "active"
    matchmaker_id: int | None
    organization_id: int | None
    promoter_id: int | None = None
    tags: list[str] = Field(default_factory=list)
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
    intention_level: Literal[1, 2, 3] = 1
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
    tags: list[CustomerLeadOption] = Field(default_factory=list)

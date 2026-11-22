"""短信管理（A1）contracts for the back office."""

from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class SmsVendorConfigUpdate(BaseModel):
    enabled: bool | None = None
    provider: str | None = Field(default=None, max_length=32)
    region: str | None = Field(default=None, max_length=32)
    secret_id: str | None = Field(default=None, max_length=128)
    secret_key: str | None = Field(default=None, max_length=256)
    sdk_app_id: str | None = Field(default=None, max_length=64)
    sign_name: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def require_update(self) -> "SmsVendorConfigUpdate":
        if not self.model_dump(exclude_unset=True):
            raise ValueError("至少提供一个需要修改的字段")
        return self


class IdentityVendorConfigUpdate(BaseModel):
    base_url: str | None = Field(default=None, max_length=300)
    secret_id: str | None = Field(default=None, max_length=128)
    secret_key: str | None = Field(default=None, max_length=256)
    timeout_seconds: int | None = Field(default=None, ge=1, le=60)

    @model_validator(mode="after")
    def require_update(self) -> "IdentityVendorConfigUpdate":
        if not self.model_dump(exclude_unset=True):
            raise ValueError("至少提供一个需要修改的字段")
        return self


# ─────────────────────────── 签名 ───────────────────────────


class SmsSignatureCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64, description="签名内容（不含【】）")
    status: str = Field(default="pending", pattern="^(pending|approved|rejected)$")
    audit_remark: str | None = Field(default=None, max_length=255)


class SmsSignatureUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    status: str | None = Field(default=None, pattern="^(pending|approved|rejected)$")
    audit_remark: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def require_update(self) -> "SmsSignatureUpdate":
        if not self.model_dump(exclude_unset=True):
            raise ValueError("至少提供一个需要修改的字段")
        return self


class SmsSignatureItem(BaseModel):
    id: int
    name: str
    provider_ref: str | None = None
    status: str
    audit_remark: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ─────────────────────────── 模板 ───────────────────────────


class SmsTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    code: str = Field(min_length=1, max_length=64, description="供应商模板编码，发送时使用")
    content: str = Field(min_length=1, max_length=1000, description="模板正文，变量用 {name} 占位")
    signature_id: int | None = Field(default=None, ge=1)
    scene: str = Field(default="notice", max_length=32, description="场景标识，如 notice / activity / vip")
    status: int = Field(default=1, ge=0, le=1)


class SmsTemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    code: str | None = Field(default=None, min_length=1, max_length=64)
    content: str | None = Field(default=None, min_length=1, max_length=1000)
    signature_id: int | None = Field(default=None, ge=1)
    scene: str | None = Field(default=None, max_length=32)
    status: int | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def require_update(self) -> "SmsTemplateUpdate":
        if not self.model_dump(exclude_unset=True):
            raise ValueError("至少提供一个需要修改的字段")
        return self


class SmsTemplateItem(BaseModel):
    id: int
    name: str
    code: str
    content: str
    signature_id: int | None = None
    signature_name: str | None = None
    scene: str
    status: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ─────────────────────────── 群发 ───────────────────────────


class SmsSendRequest(BaseModel):
    """群发：模板与自由文本二选一；会员与手机号至少一类。"""

    template_id: int | None = Field(default=None, ge=1, description="模板 ID；与 content 二选一")
    content: str | None = Field(default=None, max_length=1000, description="自由文本；与 template_id 二选一")
    user_ids: list[int] = Field(default_factory=list, max_length=500, description="目标会员 users.id，最多 500")
    phones: list[str] = Field(default_factory=list, max_length=100, description="额外手机号，最多 100")
    variables: dict[str, str] = Field(default_factory=dict, description="模板变量，仅 template_id 模式生效")

    @model_validator(mode="after")
    def validate_send(self) -> "SmsSendRequest":
        if (self.template_id is None) == (self.content is None):
            raise ValueError("template_id 与 content 必须二选一")
        if not self.user_ids and not self.phones:
            raise ValueError("user_ids 与 phones 至少提供一类")
        return self


class SmsSendResult(BaseModel):
    batch_no: str
    total: int
    sent: int
    failed: int
    skipped: list[dict]  # [{target, reason}]


# ─────────────────────────── 记录与余量 ───────────────────────────


class SmsRecordItem(BaseModel):
    id: int
    batch_no: str
    template_id: int | None = None
    template_name: str | None = None
    phone: str
    user_id: int | None = None
    content: str
    status: str
    provider_ref: str | None = None
    error: str | None = None
    sent_at: datetime | None = None
    created_at: datetime | None = None


class SmsRecordPage(BaseModel):
    items: list[SmsRecordItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class SmsQuota(BaseModel):
    provider: str
    remaining_count: int = 0
    success_count: int = 0
    failed_count: int = 0
    updated_at: datetime | None = None


class SmsSignaturePage(BaseModel):
    items: list[SmsSignatureItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class SmsTemplatePage(BaseModel):
    items: list[SmsTemplateItem]
    page: int
    page_size: int
    total: int
    has_more: bool

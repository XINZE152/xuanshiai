"""微信公众号运营（A2）contracts。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ─────────────────────────── 配置 ───────────────────────────


class WechatConfig(BaseModel):
    """公众号配置：密钥类字段只回显「是否已配置」，绝不返回明文。"""

    app_id_configured: bool
    app_secret_configured: bool
    token_configured: bool
    aes_key_configured: bool
    configured: bool
    wx_no: str = ""
    crypto_mode: str = "safe"
    qrcode_url: str | None = None
    platform_templates: list[Any] = Field(default_factory=list)
    version: int | None = None
    updated_at: datetime | None = None


class WechatConfigUpdate(BaseModel):
    """只接受非敏感项；密钥字段不属于本契约，传入会被 422 拒绝。

    ``app_secret`` / ``api_token`` / ``encoding_aes_key`` 只能通过环境变量注入，
    因此这里 ``extra="forbid"``：宁可明确报错，也不静默丢弃让调用方误以为已保存。
    """

    model_config = ConfigDict(extra="forbid")

    wx_no: str | None = Field(default=None, max_length=64)
    crypto_mode: str | None = Field(default=None, pattern="^(safe|compatible|plain)$")
    qrcode_url: str | None = Field(default=None, max_length=500)
    platform_templates: list[Any] | None = None

    @model_validator(mode="after")
    def require_update(self) -> "WechatConfigUpdate":
        if not self.model_dump(exclude_unset=True):
            raise ValueError("至少提供一个需要修改的字段")
        return self


# ─────────────────────────── 粉丝 ───────────────────────────


class WechatFanItem(BaseModel):
    id: int
    openid: str
    nickname: str | None = None
    avatar_url: str | None = None
    gender: int = 0
    city: str | None = None
    province: str | None = None
    subscribe_status: int
    subscribe_at: datetime | None = None
    unsubscribe_at: datetime | None = None
    group_id: int | None = None
    group_name: str | None = None
    user_id: int | None = None
    remark: str | None = None
    created_at: datetime | None = None


class WechatFanPage(BaseModel):
    items: list[WechatFanItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class WechatFanSyncResult(BaseModel):
    fetched: int
    created: int
    updated: int
    unsubscribed: int
    synced_at: datetime


class WechatFanGroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    sort: int = Field(default=0, ge=0, le=9999)


class WechatFanGroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    sort: int | None = Field(default=None, ge=0, le=9999)

    @model_validator(mode="after")
    def require_update(self) -> "WechatFanGroupUpdate":
        if not self.model_dump(exclude_unset=True):
            raise ValueError("至少提供一个需要修改的字段")
        return self


class WechatFanGroupItem(BaseModel):
    id: int
    name: str
    provider_group_id: int | None = None
    sort: int = 0
    fan_count: int = 0
    created_at: datetime | None = None


# ─────────────────────────── 菜单 ───────────────────────────


class WechatMenuButton(BaseModel):
    name: str = Field(min_length=1, max_length=16)
    type: str | None = Field(default=None, max_length=32)
    key: str | None = Field(default=None, max_length=128)
    url: str | None = Field(default=None, max_length=500)
    appid: str | None = Field(default=None, max_length=64)
    pagepath: str | None = Field(default=None, max_length=255)
    sub_button: list["WechatMenuButton"] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_button(self) -> "WechatMenuButton":
        if self.sub_button:
            if len(self.sub_button) > 5:
                raise ValueError("二级菜单最多 5 个")
            if self.type or self.key or self.url:
                raise ValueError("包含二级菜单的一级菜单不能再设置 type/key/url")
        return self


class WechatMenu(BaseModel):
    top_menus: list[WechatMenuButton] = Field(default_factory=list)
    published_at: datetime | None = None
    version: int | None = None


class WechatMenuPublish(BaseModel):
    """发布菜单：微信限制一级最多 3 个、二级最多 5 个。"""

    top_menus: list[WechatMenuButton] = Field(min_length=1, max_length=3)


# ─────────────────────────── 自动回复 ───────────────────────────


class WechatReplyRuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    match_type: str = Field(default="keyword", pattern="^(keyword|subscribe|default)$")
    keyword: str | None = Field(default=None, max_length=128)
    """关键词匹配方式：contain 包含 / equal 等于（仅 keyword 类型有效）。"""
    match_mode: str = Field(default="contain", pattern="^(contain|equal)$")
    reply_type: str = Field(default="text", pattern="^(text|image|news|link)$")
    content: str = Field(min_length=1, max_length=4000)
    status: int = Field(default=1, ge=0, le=1)
    sort: int = Field(default=0, ge=0, le=9999)

    @model_validator(mode="after")
    def validate_rule(self) -> "WechatReplyRuleCreate":
        if self.match_type == "keyword" and not (self.keyword or "").strip():
            raise ValueError("关键词规则必须填写关键词")
        return self


class WechatReplyRuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    match_type: str | None = Field(default=None, pattern="^(keyword|subscribe|default)$")
    keyword: str | None = Field(default=None, max_length=128)
    match_mode: str | None = Field(default=None, pattern="^(contain|equal)$")
    reply_type: str | None = Field(default=None, pattern="^(text|image|news|link)$")
    content: str | None = Field(default=None, min_length=1, max_length=4000)
    status: int | None = Field(default=None, ge=0, le=1)
    sort: int | None = Field(default=None, ge=0, le=9999)

    @model_validator(mode="after")
    def require_update(self) -> "WechatReplyRuleUpdate":
        if not self.model_dump(exclude_unset=True):
            raise ValueError("至少提供一个需要修改的字段")
        return self


class WechatReplyRuleItem(BaseModel):
    id: int
    name: str
    match_type: str
    keyword: str | None = None
    match_mode: str = "contain"
    reply_type: str
    content: str
    status: int
    sort: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ─────────────────────────── 模板消息 ───────────────────────────


class WechatTemplateItem(BaseModel):
    id: int
    template_id: str
    title: str
    primary_industry: str | None = None
    deputy_industry: str | None = None
    content: str | None = None
    status: int
    synced_at: datetime | None = None


class WechatTemplateSyncResult(BaseModel):
    fetched: int
    created: int
    updated: int
    synced_at: datetime


class WechatQrcodeResult(BaseModel):
    """公众号带参二维码：``qrcode_url`` 可直接 <img> 展示。"""

    qrcode_url: str
    scene_str: str


# ─────────────────────────── 发送 ───────────────────────────


class WechatSendRequest(BaseModel):
    """群发 / 模板消息二选一。

    ``broadcast``：按 ``group_id``（空=全部关注粉丝）或显式 ``openids`` 投递；
    ``template``：必须给 ``template_id`` 与 ``data``，并指定 ``openids`` 或 ``group_id``。
    """

    send_type: str = Field(default="broadcast", pattern="^(broadcast|template)$")
    template_id: int | None = Field(default=None, ge=1, description="admin_wechat_template.id")
    group_id: int | None = Field(default=None, ge=1)
    openids: list[str] = Field(default_factory=list, max_length=1000)
    title: str | None = Field(default=None, max_length=128)
    content: str | None = Field(default=None, max_length=2000)
    url: str | None = Field(default=None, max_length=500)
    data: dict[str, str] = Field(default_factory=dict, description="模板消息变量")

    @model_validator(mode="after")
    def validate_send(self) -> "WechatSendRequest":
        if self.send_type == "template":
            if self.template_id is None:
                raise ValueError("模板消息必须提供 template_id")
            if not self.openids and self.group_id is None:
                raise ValueError("模板消息必须指定 openids 或 group_id")
            return self
        if not (self.content or "").strip():
            raise ValueError("群发消息必须提供 content")
        return self


class WechatSendResult(BaseModel):
    batch_no: str
    send_type: str
    target_count: int
    sent_count: int
    failed_count: int
    provider_msg_id: str | None = None


class WechatMessageItem(BaseModel):
    id: int
    batch_no: str
    send_type: str
    template_id: int | None = None
    group_id: int | None = None
    group_name: str | None = None
    title: str | None = None
    content: str | None = None
    url: str | None = None
    target_count: int = 0
    sent_count: int = 0
    failed_count: int = 0
    status: str
    error: str | None = None
    provider_msg_id: str | None = None
    sent_at: datetime | None = None
    created_at: datetime | None = None


class WechatMessagePage(BaseModel):
    items: list[WechatMessageItem]
    page: int
    page_size: int
    total: int
    has_more: bool

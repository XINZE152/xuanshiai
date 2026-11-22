"""Contracts for versioned back-office configuration snapshots."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

JsonValue = Any


class AdminConfigSnapshot(BaseModel):
    namespace: str
    name: str
    description: str
    version: int = Field(ge=1)
    config: dict[str, JsonValue]
    sensitive_keys: list[str] = Field(default_factory=list)
    updated_by: int | None = None
    updated_at: datetime | None = None


class AdminConfigUpdate(BaseModel):
    version: int = Field(ge=1, description="客户端读取到的当前版本，防止覆盖他人修改")
    config: dict[str, JsonValue] = Field(description="完整配置域快照，不允许只提交局部字段")
    change_summary: str = Field(min_length=1, max_length=255)


class AdminConfigAuditItem(BaseModel):
    id: int
    namespace: str
    version: int
    action: str
    actor_user_id: int
    change_summary: str | None
    before_config: dict[str, JsonValue] | None
    after_config: dict[str, JsonValue] | None
    created_at: datetime | None


class AdminConfigAuditPage(BaseModel):
    items: list[AdminConfigAuditItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class PublicArticleItem(BaseModel):
    type: str
    title: str
    clickable: bool


class PublicArticleDetail(BaseModel):
    type: str
    title: str
    content: str
    updated_at: str | None = None


class PublicNavigationItem(BaseModel):
    id: str
    name: str
    icon: str | None = None
    icon_class: str | None = None
    visible: bool = True


class PublicNavigationSection(BaseModel):
    key: str
    items: list[PublicNavigationItem]


class PublicNavigationConfig(BaseModel):
    version: int
    sections: list[PublicNavigationSection]


class AdminMembershipPackage(BaseModel):
    code: str
    name: str = Field(min_length=1, max_length=64)
    duration_days: int = Field(ge=1, le=3650)
    price: float = Field(ge=0, le=1000000)
    original_price: float | None = Field(default=None, ge=0, le=1000000)
    daily_price: float | None = Field(default=None, ge=0, le=1000000)
    sort: int = Field(ge=0, le=10000)
    is_active: bool
    is_recommend: bool
    badge: str | None = Field(default=None, max_length=32)
    rights: dict[str, int | bool | str | None]
    effective_price: float
    price_overridden_by_environment: bool


class AdminMembershipPackageUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    duration_days: int = Field(ge=1, le=3650)
    price: float = Field(ge=0, le=1000000)
    original_price: float | None = Field(default=None, ge=0, le=1000000)
    daily_price: float | None = Field(default=None, ge=0, le=1000000)
    sort: int = Field(ge=0, le=10000)
    is_active: bool
    is_recommend: bool
    badge: str | None = Field(default=None, max_length=32)
    rights: dict[str, int | bool | str | None]


class AdminMembershipPackageCreate(AdminMembershipPackageUpdate):
    code: str = Field(min_length=2, max_length=32, pattern=r"^[a-z0-9_]+$")

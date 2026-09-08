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

"""Schemas for the generic admin content items (activities, merchants, short videos, gifts, ...)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

ALLOWED_STATUSES = (1, 2)


class ContentItemBase(BaseModel):
    title: str = Field("", max_length=255)
    subtitle: str | None = Field(None, max_length=500)
    image_url: str | None = Field(None, max_length=500)
    amount: float | None = None
    status: int = Field(1, description="1 正常 / 2 停用")
    sort: int = 0
    extra: dict[str, Any] = Field(default_factory=dict)


class ContentItemCreate(ContentItemBase):
    pass


class ContentItemUpdate(BaseModel):
    title: str | None = Field(None, max_length=255)
    subtitle: str | None = Field(None, max_length=500)
    image_url: str | None = Field(None, max_length=500)
    amount: float | None = None
    status: int | None = None
    sort: int | None = None
    extra: dict[str, Any] | None = None


class ContentItem(ContentItemBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    domain: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ContentItemPage(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[dict[str, Any]]

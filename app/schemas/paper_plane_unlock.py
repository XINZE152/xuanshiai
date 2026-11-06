"""Typed contracts for target-specific paper-plane profile unlocks."""

from datetime import datetime

from pydantic import BaseModel, Field


class PaperPlaneProfileUnlockResponse(BaseModel):
    target_user_id: int = Field(ge=1)
    unlocked: bool
    points_cost: int = Field(default=50, ge=0)
    balance: int = Field(ge=0)
    unlocked_at: datetime | None = None
    # Deliberately privacy-filtered. Contact details are never part of this API.
    profile: dict[str, object] | None = None


class PaperPlaneProfileUnlockItem(BaseModel):
    target_user_id: int = Field(ge=1)
    nickname: str | None = None
    avatar: str | None = None
    avatar_thumb: str | None = None
    age: int | None = None
    unlocked_at: datetime
    points_cost: int = Field(ge=0)
    unlock_source: str
    target_available: bool


class PaperPlaneProfileUnlockPage(BaseModel):
    items: list[PaperPlaneProfileUnlockItem]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    has_more: bool

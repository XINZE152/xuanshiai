"""Request and response models for discovery and card interactions."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class DiscoveryFilters(BaseModel):
    gender: Literal[1, 2] | None = None
    age_min: int | None = Field(default=None, ge=18, le=100)
    age_max: int | None = Field(default=None, ge=18, le=100)
    province_code: str | None = Field(default=None, max_length=32)
    city_code: str | None = Field(default=None, max_length=32)
    district_code: str | None = Field(default=None, max_length=32)
    marriage_status: Literal[1, 2, 3] | None = None
    education_min: int | None = Field(default=None, ge=1, le=8)
    height_min: int | None = Field(default=None, ge=100, le=250)
    height_max: int | None = Field(default=None, ge=100, le=250)
    income_min: float | None = Field(default=None, ge=0, le=1_000_000)
    income_max: float | None = Field(default=None, ge=0, le=1_000_000)
    pure_free: bool = False
    page: int = Field(default=1, ge=1, le=1000)
    page_size: int = Field(default=20, ge=1, le=20)

    @model_validator(mode="after")
    def validate_ranges(self):
        if self.age_min is not None and self.age_max is not None and self.age_min > self.age_max:
            raise ValueError("年龄下限不能大于上限")
        if self.height_min is not None and self.height_max is not None and self.height_min > self.height_max:
            raise ValueError("身高下限不能大于上限")
        if self.income_min is not None and self.income_max is not None and self.income_min > self.income_max:
            raise ValueError("收入下限不能大于上限")
        return self


class DiscoverySearch(BaseModel):
    nickname: str | None = Field(default=None, max_length=64)
    tag: str | None = Field(default=None, max_length=64)
    page: int = Field(default=1, ge=1, le=1000)
    page_size: int = Field(default=20, ge=1, le=20)

    @field_validator("nickname", "tag", mode="before")
    @classmethod
    def normalize_keyword(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_keywords(self):
        if not self.nickname and not self.tag:
            raise ValueError("昵称或标签至少填写一项")
        return self


class DiscoveryCard(BaseModel):
    user_id: int
    nickname: str | None
    avatar: str | None
    age: int | None
    height: int | None
    education_level: int | None
    occupation: str | None
    is_married: int | None
    online_status: int
    mbti: str | None
    interest_tags: list[str]
    certification_tags: list[str]
    match_score: float
    match_reason: str
    is_favorite: bool
    is_pure_free: bool
    is_boosted: bool
    detail_locked: bool = False


class DiscoveryPage(BaseModel):
    items: list[DiscoveryCard]
    page: int
    page_size: int
    total: int
    has_more: bool


class FilterOptionsResponse(BaseModel):
    genders: list[dict[str, int | str]]
    marriage_statuses: list[dict[str, int | str]]
    education_levels: list[dict[str, int | str]]
    cities: list[str]


class SavedFilterResponse(BaseModel):
    filters: DiscoveryFilters | None


class BrowseHistoryItem(BaseModel):
    target: DiscoveryCard
    viewed_at: datetime


class BrowseHistoryPage(BaseModel):
    items: list[BrowseHistoryItem]
    page: int
    page_size: int
    total: int


class VisitorPage(BaseModel):
    can_view_details: bool
    count: int
    items: list[BrowseHistoryItem]


class PublicProfileResponse(BaseModel):
    user_id: int
    card: DiscoveryCard
    profile: dict | None
    is_vip_viewer: bool
    browse_quota_remaining: int | None
    can_apply: bool


class FavoriteResponse(BaseModel):
    target_user_id: int
    is_favorite: bool


class ApplicationCreateRequest(BaseModel):
    message: str | None = Field(default=None, max_length=255)


class ApplicationResponse(BaseModel):
    id: int
    from_user_id: int
    to_user_id: int
    message: str | None
    status: Literal[0, 1, 2, 3]
    expire_at: datetime | None
    created_at: datetime


class ApplicationRejectRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=255)


class SuperLikeResponse(BaseModel):
    target_user_id: int
    remaining_today: int | None
    created_at: datetime

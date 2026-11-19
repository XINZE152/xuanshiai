"""匿名游客预览响应模型（GUEST-PREVIEW-20261115）。

字段白名单即脱敏边界：联系方式、收入、身高体重、自我介绍、问答、
精确地址、个人互动标记等一律不得出现在本模块模型中。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PublicPreviewCard(BaseModel):
    user_id: int
    nickname: str | None = Field(default=None, description="脱敏昵称：首字+**")
    avatar: str | None = None
    gender: int | None = None
    age: int | None = None
    residence_display: str | None = None
    education_level: int | None = None
    occupation: str | None = None
    mbti: str | None = None
    interest_tags: list[str] = Field(default_factory=list, description="最多 3 条")
    certification_tags: list[str] = Field(default_factory=list)
    is_vip: bool = False
    detail_locked: bool = Field(default=True, description="游客态恒锁定，完整资料需登录")
    is_liked: bool = False
    is_favorite: bool = False


class PublicDiscoveryPreviewPage(BaseModel):
    items: list[PublicPreviewCard]
    page: int
    page_size: int
    total: int
    has_more: bool
    is_preview: bool = True


class PublicPostPreview(BaseModel):
    id: int
    author_nickname: str | None = Field(default=None, description="脱敏昵称：首字+**")
    author_avatar: str | None = None
    content: str
    images: list[str] = Field(default_factory=list, description="最多 3 张")
    topic_name: str | None = None
    like_count: int
    comment_count: int
    created_at: datetime


class PublicCommunityPreviewPage(BaseModel):
    items: list[PublicPostPreview]
    page: int
    page_size: int
    total: int
    has_more: bool
    is_preview: bool = True


class PublicActivityPreview(BaseModel):
    id: int
    title: str
    cover: str | None = None
    type: str | None = None
    city: str | None = None
    start_time: datetime
    end_time: datetime
    signup_deadline: datetime | None = None
    max_people: int
    current_people: int
    price: float
    status_text: str
    description: str | None = None


class PublicActivityPreviewPage(BaseModel):
    items: list[PublicActivityPreview]
    page: int
    page_size: int
    total: int
    has_more: bool
    is_preview: bool = True
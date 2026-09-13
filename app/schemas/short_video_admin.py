"""Short-video (短视频) contracts for the back office (M7-C)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


# ─────────────────────────── 分类 ───────────────────────────


class VideoCategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    sort: int = Field(default=0, ge=-1000000, le=1000000)
    status: int = Field(default=1, ge=0, le=1)


class VideoCategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    sort: int | None = Field(default=None, ge=-1000000, le=1000000)
    status: int | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def require_update(self) -> "VideoCategoryUpdate":
        if not self.model_dump(exclude_unset=True):
            raise ValueError("至少提供一个需要修改的字段")
        return self


class VideoCategoryItem(BaseModel):
    id: int
    name: str
    sort: int = 0
    status: int = 1
    video_count: int = 0


# ─────────────────────────── 视频 ───────────────────────────

AuditStatus = Literal["pending", "approved", "rejected"]


class ShortVideoCreate(BaseModel):
    publisher_user_id: int = Field(ge=1)
    cover: str | None = Field(default=None, max_length=255)
    cover_mode: Literal["auto", "custom"] = "auto"
    description: str | None = Field(default=None, max_length=255)
    category_id: int | None = Field(default=None, ge=1)
    duration_seconds: float = Field(default=0, ge=0, le=100000)
    video_url: str | None = Field(default=None, max_length=500)
    link_type: Literal["none", "custom", "member", "activity", "home"] = "none"
    link_value: str | None = Field(default=None, max_length=255)
    view_permission: Literal["login", "all", "member"] = "login"
    sort: int = Field(default=0, ge=-1000000, le=1000000)
    virtual_views: int = Field(default=0, ge=0, le=1000000000)
    comment_enabled: bool = True
    tip_enabled: bool = True
    visible: bool = True
    audit_status: AuditStatus = "pending"
    published_at: datetime | None = None


class ShortVideoUpdate(BaseModel):
    publisher_user_id: int | None = Field(default=None, ge=1)
    cover: str | None = Field(default=None, max_length=255)
    cover_mode: Literal["auto", "custom"] | None = None
    description: str | None = Field(default=None, max_length=255)
    category_id: int | None = Field(default=None, ge=1)
    duration_seconds: float | None = Field(default=None, ge=0, le=100000)
    video_url: str | None = Field(default=None, max_length=500)
    link_type: Literal["none", "custom", "member", "activity", "home"] | None = None
    link_value: str | None = Field(default=None, max_length=255)
    view_permission: Literal["login", "all", "member"] | None = None
    sort: int | None = Field(default=None, ge=-1000000, le=1000000)
    virtual_views: int | None = Field(default=None, ge=0, le=1000000000)
    comment_enabled: bool | None = None
    tip_enabled: bool | None = None
    visible: bool | None = None
    audit_status: AuditStatus | None = None
    is_top: bool | None = None
    is_recommend: bool | None = None
    is_hot: bool | None = None
    has_red_packet: bool | None = None

    @model_validator(mode="after")
    def require_update(self) -> "ShortVideoUpdate":
        if not self.model_dump(exclude_unset=True):
            raise ValueError("至少提供一个需要修改的字段")
        return self


class ShortVideoItem(BaseModel):
    id: int
    publisher_user_id: int
    publisher_nickname: str | None = None
    cover: str | None = None
    cover_mode: str = "auto"
    description: str | None = None
    category_id: int | None = None
    category_name: str | None = None
    duration_seconds: float = 0
    duration_label: str = "0s"
    video_url: str | None = None
    link_type: str = "none"
    link_label: str = "不关联"
    link_value: str | None = None
    view_permission: str = "login"
    view_permission_label: str = "必须先登录"
    sort: int = 0
    virtual_views: int = 0
    comment_enabled: bool = True
    tip_enabled: bool = True
    visible: bool = True
    audit_status: str = "pending"
    audit_label: str = "待审"
    is_top: bool = False
    is_recommend: bool = False
    is_hot: bool = False
    has_red_packet: bool = False
    view_count: int = 0
    comment_count: int = 0
    like_count: int = 0
    tip_amount: str = "0.00"
    published_at: datetime | None = None
    created_at: datetime | None = None


class ShortVideoPage(BaseModel):
    items: list[ShortVideoItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class VideoBrushRequest(BaseModel):
    brush_type: Literal["views", "likes", "publish_time"]
    min_value: int = Field(ge=0, le=1000000000)
    max_value: int = Field(ge=0, le=1000000000)

    @model_validator(mode="after")
    def validate_range(self) -> "VideoBrushRequest":
        if self.max_value < self.min_value:
            raise ValueError("max_value 必须不小于 min_value")
        return self


class VideoBrushResult(BaseModel):
    brush_type: str
    affected: int


# ─────────────────────────── 评论 ───────────────────────────


class VideoCommentItem(BaseModel):
    id: int
    video_id: int
    video_description: str | None = None
    user_id: int
    nickname: str | None = None
    content: str
    like_count: int = 0
    ip: str | None = None
    audit_status: str = "pending"
    created_at: datetime | None = None


class VideoCommentPage(BaseModel):
    items: list[VideoCommentItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class VideoCommentUpdate(BaseModel):
    content: str | None = Field(default=None, max_length=500)
    audit_status: AuditStatus | None = None

    @model_validator(mode="after")
    def require_update(self) -> "VideoCommentUpdate":
        if not self.model_dump(exclude_unset=True):
            raise ValueError("至少提供一个需要修改的字段")
        return self


class VideoCommentBatchDelete(BaseModel):
    ids: list[int] = Field(min_length=1)


# ─────────────────────────── 打赏 ───────────────────────────


class VideoTipItem(BaseModel):
    id: int
    video_id: int
    video_description: str | None = None
    tipper_user_id: int
    tipper_nickname: str | None = None
    receiver_user_id: int
    receiver_nickname: str | None = None
    message: str | None = None
    tip_form: str = "cash"
    tip_form_label: str = "现金"
    amount: str = "0.00"
    pay_method: str | None = None
    order_no: str | None = None
    status: str = "paid"
    created_at: datetime | None = None


class VideoTipPage(BaseModel):
    items: list[VideoTipItem]
    page: int
    page_size: int
    total: int
    has_more: bool
    total_amount: str = "0.00"


# ─────────────────────────── 红包 ───────────────────────────


class RedPacketItem(BaseModel):
    id: int
    video_id: int
    video_description: str | None = None
    sender_label: str = "后台发放"
    amount: str = "0.00"
    total_parts: int = 1
    is_equal: bool = False
    remain_parts: int = 0
    remain_amount: str = "0.00"
    pay_status: str = "unpaid"
    claim_status: str = "unfinished"
    created_at: datetime | None = None


class RedPacketPage(BaseModel):
    items: list[RedPacketItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class RedPacketClaimItem(BaseModel):
    id: int
    packet_id: int
    user_id: int
    nickname: str | None = None
    amount: str = "0.00"
    created_at: datetime | None = None


# ─────────────────────────── 会员主页 ───────────────────────────


class VideoHomepageItem(BaseModel):
    id: int
    nickname: str | None = None
    wechat: str | None = None
    bio: str | None = None
    video_count: int = 0
    view_count: int = 0
    follower_count: int = 0
    like_count: int = 0
    tip_amount: str = "0.00"
    certified: bool = False
    created_at: datetime | None = None


class VideoHomepagePage(BaseModel):
    items: list[VideoHomepageItem]
    page: int
    page_size: int
    total: int
    has_more: bool

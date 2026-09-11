"""会员资料媒体验证（M3-2）管理后台契约：个人介绍编辑 + 头像/照片/视频媒体审核。

所有出参字段名与前端 ``content-verify`` 页面严格对应；会员编号统一为
``G`` + 6 位左补零（由 SQL ``CONCAT('G', LPAD(u.id, 6, '0'))`` 生成）。
"""

from datetime import datetime

from pydantic import BaseModel, Field


# ─── 个人介绍（自白内容） ───────────────────────────────────────


class MemberIntroItem(BaseModel):
    """个人介绍行。``id`` 等于 ``user_id``，便于前端「查看资料」复用。"""

    id: int  # 列表主键（= user_id）
    user_id: int  # 会员 user_id，「查看资料」使用
    member_code: str  # 会员编号 G+6 位
    nickname: str | None = None  # 昵称
    avatar: str | None = None  # 头像 URL
    self_intro: str | None = None  # 自白内容（个人介绍）
    updated_at: datetime | None = None  # 自白最近修改时间


class MemberIntroPage(BaseModel):
    """个人介绍分页响应。"""

    items: list[MemberIntroItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class MemberIntroUpdate(BaseModel):
    """个人介绍（自白内容）更新请求体。允许为空串（清空自白）。"""

    self_intro: str = Field(..., max_length=500)  # 自白内容，≤500 字；空串表示清空（不限制最小长度）


# ─── 媒体（头像/照片/视频） ─────────────────────────────────────


class MemberMediaItem(BaseModel):
    """媒体审核行（头像/照片/视频通用）。"""

    id: int  # 媒体记录主键（user_media.id）
    user_id: int  # 关联会员 user_id
    member_code: str  # 会员编号 G+6 位
    nickname: str | None = None  # 昵称
    avatar: str | None = None  # 会员头像 URL
    media_type: str  # avatar / photo / video
    file_url: str | None = None  # 媒体文件地址
    thumbnail_url: str | None = None  # 缩略图地址（视频/部分图片）
    mime_type: str | None = None  # 文件 MIME 类型
    duration_seconds: int | None = None  # 视频时长（秒）
    review_status: int  # 0审核中 1已通过 2未通过 3已隐藏
    review_status_label: str  # 审核状态中文标签（审核中/已通过/未通过/已隐藏）
    review_reason: str | None = None  # 审核不通过/隐藏原因
    age: int | None = None  # 由 birthday 推算的年龄（无生日为 None）
    meta_text: str | None = None  # 形如 "1990年 175cm 大专"，缺项跳过；全空为 None
    created_at: datetime | None = None  # 上传时间


class MemberMediaPage(BaseModel):
    """媒体分页响应。"""

    items: list[MemberMediaItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class MemberMediaReview(BaseModel):
    """媒体审核请求体：通过(1)/未通过(2)/隐藏(3)。"""

    review_status: int = Field(..., ge=1, le=3)  # 1通过 2未通过 3隐藏（0 审核中由上传自动置位，不可手动置回）
    review_reason: str | None = Field(None, max_length=255)  # 不通过/隐藏原因


class MemberMediaReplace(BaseModel):
    """媒体重新上传请求体：覆盖文件并将审核状态归零（待审核）。"""

    file_url: str = Field(..., min_length=1, max_length=512)  # 新文件地址（必填，不可为空）
    thumbnail_url: str | None = Field(None, max_length=512)  # 新缩略图地址（可选）

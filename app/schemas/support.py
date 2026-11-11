"""C 端反馈 / 意见箱与帮助中心契约（Q54，2026-11-02）。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

FeedbackCategory = Literal["咨询", "BUG", "建议", "投诉"]


class FeedbackCreate(BaseModel):
    category: FeedbackCategory = Field(default="咨询", description="反馈分类")
    content: str = Field(min_length=1, max_length=2000, description="反馈内容")
    images: list[str] = Field(default_factory=list, max_length=9, description="截图 URL，最多 9 张")
    contact_phone: str | None = Field(
        default=None,
        pattern=r"^1[3-9]\d{9}$",
        description="联系手机号（可选）",
    )

    @field_validator("content")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("反馈内容不能为空")
        return text

    @field_validator("images")
    @classmethod
    def _limit_image_length(cls, value: list[str]) -> list[str]:
        for url in value:
            if not url or len(url) > 500:
                raise ValueError("图片 URL 长度必须在 1-500 之间")
        return value


class ContactRequest(BaseModel):
    """转人工客服：落 user_feedback，category 固定为「咨询」。"""

    content: str = Field(min_length=1, max_length=2000)
    images: list[str] = Field(default_factory=list, max_length=9)
    contact_phone: str | None = Field(default=None, pattern=r"^1[3-9]\d{9}$")

    @field_validator("content")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("咨询内容不能为空")
        return text


class FeedbackItem(BaseModel):
    id: int
    category: str
    content: str
    images: list[str] = Field(default_factory=list)
    contact_phone: str | None = None
    status: int = Field(description="0待处理 / 1处理中 / 2已回复 / 3已关闭")
    reply_content: str | None = None
    replied_at: datetime | None = None
    created_at: datetime | None = None


class FeedbackPage(BaseModel):
    items: list[FeedbackItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class HelpDocItem(BaseModel):
    id: int
    title: str
    subtitle: str | None = None
    category: str | None = None
    body: str | None = None
    updated_at: datetime | None = None


class HelpDocPage(BaseModel):
    items: list[HelpDocItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class AiChatRequest(BaseModel):
    """AI 客服提问。限长 200 字：这不是聊天室，是一句问题。"""

    content: str = Field(min_length=1, max_length=200, description="用户问题")

    @field_validator("content")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("问题不能为空")
        return text


class AiChatReply(BaseModel):
    """AI 客服回复。

    ``status`` 只有三种取值，前端按它决定是否显示「联系客服」按钮：

    - ``answered``：命中帮助中心词条，``answer`` 是词条正文；
    - ``no_answer``：问的是平台的事，但知识库没有答案——``answer`` 是
      「暂时无法帮助您，等待升级」这类固定话术；
    - ``off_topic``：与本平台无关的内容，拒答。
    """

    status: Literal["answered", "no_answer", "off_topic"]
    answer: str
    matched_title: str | None = Field(default=None, description="命中的帮助词条标题")
    escalate: bool = Field(default=False, description="是否建议转人工客服")



class AdminFeedbackItem(FeedbackItem):
    user_id: int
    nickname: str | None = None
    replied_by: int | None = None


class AdminFeedbackPage(BaseModel):
    items: list[AdminFeedbackItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class AdminFeedbackReply(BaseModel):
    reply_content: str = Field(min_length=1, max_length=2000, description="客服回复内容")

    @field_validator("reply_content")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("回复内容不能为空")
        return text

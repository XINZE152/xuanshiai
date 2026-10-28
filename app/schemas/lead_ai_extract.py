"""AI 智能录入（客源文本抽取）接口契约。

用于红娘后台「粘贴一段自由文本 → 解析出结构化客源字段 → 回填录入表单」。
本模块只做**解析结果回传**，不落库；红娘核对后仍走既有 ``POST /admin/customer-leads``
创建接口。字段口径与 ``CustomerLeadCreate`` 对齐，全部为可选（文本未提及即 None）。
"""

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.customer_lead_admin import LeadGender

# 抽取白名单：与前端录入表单可回填的客源字段一一对应，模型输出仅此键集。
LEAD_EXTRACT_FIELDS: tuple[str, ...] = (
    "name",
    "phone",
    "wechat",
    "source",
    "intention_level",
    "gender",
    "remark",
)

# 客户意向 A-G 七档（与 customer_lead.intention_level 同语义）。
LeadIntentionGrade = Literal[1, 2, 3, 4, 5, 6, 7]


class LeadExtractRequest(BaseModel):
    """文本抽取请求：仅接收一段自由文本，不接收图片（图片 OCR 为二期）。"""

    text: str = Field(
        min_length=1,
        max_length=4000,
        description="待抽取的自由文本（如红娘记录的聊天片段、来电备忘、名片信息）",
    )


class LeadExtractedFields(BaseModel):
    """解析出的客源字段候选；文本未命中的字段为 None，前端不回填。"""

    name: str | None = Field(default=None, description="客源称呼/姓名，最长 128")
    phone: str | None = Field(default=None, description="手机号，最长 32")
    wechat: str | None = Field(default=None, description="微信号，最长 128")
    source: str | None = Field(default=None, description="来源渠道，最长 64")
    intention_level: LeadIntentionGrade | None = Field(
        default=None,
        description="客户意向 A-G 七档：1 A未接 2 B初步沟通 3 C深入沟通未缔结 "
        "4 D待确定到店 5 E已确定到店 6 F预约需二邀 7 G已到店未签约",
    )
    gender: LeadGender | None = Field(default=None, description="性别 0未知 1男 2女")
    remark: str | None = Field(default=None, description="备注/其它信息，最长 2000")


class LeadExtractResult(BaseModel):
    """抽取结果：字段候选 + 逐字段置信度与来源片段 + 歧义提示。"""

    fields: LeadExtractedFields = Field(description="解析出的客源字段候选")
    confidence: dict[str, float] = Field(
        default_factory=dict,
        description="每个已抽取字段的置信度 0~1，键为字段名；缺省表示模型未给出",
    )
    snippets: dict[str, str] = Field(
        default_factory=dict,
        description="每个已抽取字段对应的原文片段（截断），键为字段名，供红娘核对",
    )
    notes: list[str] = Field(
        default_factory=list,
        description="抽取过程中的歧义/告警说明，如手机号疑似不完整、意向无法判定",
    )

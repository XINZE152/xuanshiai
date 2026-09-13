"""Contracts for the 合伙红娘 → 分成配置 (3 固定级别) back-office page.

身份模型：合伙人分成级别固定 3 种（1 初级 / 2 中级 / 3 战略合伙人），
不开放后台新增/删除/重排；后端只允许编辑业务参数：
自动升级条件（团队累计业绩 / 有效会员数）/ 注册奖励 / 推广红娘纳入分成 /
会员消费分成 / 分成模式与比例 / 按事件的分成金额明细。
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

PartnerSplitMode = Literal["fixed_amount", "auto_rate"]
ConsumeCommissionMode = Literal["none", "auto_rate"]


class PartnerBonusItem(BaseModel):
    """按事件的分成金额明细行（前端「分成金额」网格）。"""

    name: str = Field(min_length=1, max_length=64)
    amount: Decimal = Field(default=Decimal("0"), ge=0, le=1000000, decimal_places=2)


class PartnerLevelItem(BaseModel):
    """总览 3 行表。"""

    id: int
    level_id: Literal[1, 2, 3]
    level_name: str
    auto_split_mode: PartnerSplitMode
    auto_split_mode_label: str = Field(description="列表『分成模式』列展示文案")
    auto_split_rate: Decimal | None = Field(default=None, description="按比例自动计算的比例(%)")
    promote_performance_threshold: Decimal | None = Field(
        default=None, description="自动升级条件：团队累计业绩阈值(元)"
    )
    promote_member_threshold: int | None = Field(
        default=None, description="自动升级条件：团队累计发展有效相亲会员数阈值"
    )
    promote_condition_text: str = Field(description="自动升级条件展示文案")
    partner_count: int = Field(default=0, description="当前处于该级别的合伙人数")
    register_reward_male: Decimal = Field(default=Decimal("0"))
    register_reward_female: Decimal = Field(default=Decimal("0"))
    promoter_join_reward: Decimal = Field(default=Decimal("0"))
    consume_commission_mode: ConsumeCommissionMode
    consume_commission_rate: Decimal | None = None
    share_bonus: bool = True
    bonus_items: list[PartnerBonusItem] = Field(default_factory=list)
    updated_at: datetime | None = None


class PartnerLevelPage(BaseModel):
    items: list[PartnerLevelItem]


class PartnerLevelUpdate(BaseModel):
    """编辑 1 个级别的业务参数（level_id 固定不允许改）。"""

    level_name: str | None = Field(default=None, min_length=1, max_length=32)
    auto_split_mode: PartnerSplitMode | None = None
    auto_split_rate: Decimal | None = Field(default=None, ge=0, le=100, decimal_places=4)
    promote_performance_threshold: Decimal | None = Field(default=None, ge=0, le=100000000, decimal_places=2)
    promote_member_threshold: int | None = Field(default=None, ge=0, le=10000000)
    register_reward_male: Decimal | None = Field(default=None, ge=0, le=1000000, decimal_places=2)
    register_reward_female: Decimal | None = Field(default=None, ge=0, le=1000000, decimal_places=2)
    promoter_join_reward: Decimal | None = Field(default=None, ge=0, le=1000000, decimal_places=2)
    consume_commission_mode: ConsumeCommissionMode | None = None
    consume_commission_rate: Decimal | None = Field(default=None, ge=0, le=100, decimal_places=4)
    share_bonus: bool | None = None
    bonus_items: list[PartnerBonusItem] | None = None

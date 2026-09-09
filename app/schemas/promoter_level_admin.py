"""Contracts for the 推广红娘 → 分成配置 (4 固定级别) back-office page.

身份模型：分成级别固定 4 种（1 初级 / 2 推广大师 / 3 推广大使 / 4 推广天使），
不开放后台新增/删除/重排；`auto_split_mode` 字段硬编码（1/3/4 = fixed_amount,
2 = auto_rate），后端只允许编辑业务参数：自动升级条件 / 注册奖励 / 消费分成。
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

AutoSplitMode = Literal["fixed_amount", "auto_rate"]
ConsumeCommissionMode = Literal["none", "auto_rate"]


class PromoterLevelItem(BaseModel):
    """总览 4 行表。"""

    id: int
    level_id: Literal[1, 2, 3, 4]
    level_name: str
    auto_split_mode: AutoSplitMode
    auto_split_mode_label: str = Field(description="列表『分成模式』列展示文案")
    auto_split_rate: Decimal | None = Field(default=None, description="同比比例(%)，auto_split_mode=auto_rate 时生效")
    promote_threshold: int | None = Field(default=None, description="自动升级条件：累计发展有效相亲会员数")
    promote_threshold_text: str = Field(description="自动升级条件展示文案")
    matchmaker_count: int = Field(default=0, description="当前处于该级别的红娘数")
    register_reward_male: Decimal = Field(default=Decimal("0"))
    register_reward_female: Decimal = Field(default=Decimal("0"))
    consume_commission_mode: ConsumeCommissionMode
    consume_commission_rate: Decimal | None = None
    updated_at: datetime | None = None


class PromoterLevelPage(BaseModel):
    items: list[PromoterLevelItem]


class PromoterLevelUpdate(BaseModel):
    """编辑 1 个级别的业务参数（auto_split_mode 硬编码不允许改）。"""

    promote_threshold: int | None = Field(default=None, ge=0, le=10000000, description="累计发展有效相亲会员数阈值（0 / null = 默认/不限制）")
    register_reward_male: Decimal | None = Field(default=None, ge=0, le=1000000, decimal_places=2)
    register_reward_female: Decimal | None = Field(default=None, ge=0, le=1000000, decimal_places=2)
    consume_commission_mode: ConsumeCommissionMode | None = None
    consume_commission_rate: Decimal | None = Field(default=None, ge=0, le=100, decimal_places=4, description="会员消费分成比例(%)，mode=auto_rate 时必填")

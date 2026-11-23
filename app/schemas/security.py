"""安全中心展示数据（治理公示 + 反诈内容）的响应模型。"""

from __future__ import annotations

from pydantic import BaseModel


class SecurityHero(BaseModel):
    title: str
    subtitle: str
    note: str


class SecurityGovernance(BaseModel):
    """本月治理口径。

    数据源全部是真实表，没有占位字段：

    - 举报工单：``total_processed``（本月已完结）、``content_actions``（内容处置）、
      ``dismissed``（驳回）；
    - 账号处置：``temporary_bans`` / ``permanent_bans`` / ``other_restrictions``
      来自 ``user_restriction``（``TOTAL_BAN`` 按 ``ends_at`` 区分临时/永久，
      其余类型算功能限制）。

    注意：系统里**没有「警告」这种处置类型**，原设计稿的 ``warnings`` 已用
    ``other_restrictions``（功能限制）替代，不编造警告数量。
    """

    month_label: str
    total_processed: int
    content_actions: int
    dismissed: int
    temporary_bans: int = 0
    permanent_bans: int = 0
    other_restrictions: int = 0


class SecurityFraudArticle(BaseModel):
    type: str
    mark: str
    tone: str
    title: str
    description: str


class SecurityCenterResponse(BaseModel):
    hero: SecurityHero
    governance: SecurityGovernance | None = None
    fraud_articles: list[SecurityFraudArticle]
    ai_support_title: str
    ai_support_description: str
    risk_notice: str

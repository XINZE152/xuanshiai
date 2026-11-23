"""安全中心：平台治理公示 + 反诈内容 + 客服入口文案。

治理口径（2026-11-23 落地，两个数据源都是真实表）：

1. 举报工单（``user_report``）：只统计**本月已完结**（``status IN (1 已处理, 2 驳回)``
   且 ``reviewed_at`` 在本月）；``content_actions`` = 内容处置，``dismissed`` = 驳回。
2. 账号处置（``user_restriction``）：按 ``created_at`` 统计本月新增，
   ``TOTAL_BAN`` 按 ``ends_at`` 区分临时/永久，其余类型算功能限制。

系统里没有「警告」这种处置类型（``RestrictionType`` 只有封禁与四类功能限制），
所以不返回 ``warnings``，用 ``other_restrictions`` 表达真实口径。

静态文案优先读 ``platform_content`` 配置域，缺省回退到本模块常量，
后台不改配置也能正常出页面。
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.security import (
    SecurityCenterResponse,
    SecurityFraudArticle,
    SecurityGovernance,
    SecurityHero,
)
from app.services.runtime_config import load_config

DEFAULT_HERO = SecurityHero(
    title="宣誓爱安全中心",
    subtitle="守护你的社交安全",
    note="隐私守护 · 行为规范 · 资料核验",
)

DEFAULT_AI_SUPPORT_TITLE = "需要操作指引？联系客服"
DEFAULT_AI_SUPPORT_DESCRIPTION = "客服只提供入口说明，不会代替你提交举报"
DEFAULT_RISK_NOTICE = (
    "遇到现实中的人身或财产风险，请停止互动、保存记录，"
    "并及时联系当地警方或紧急服务。"
)

#: 反诈卡片 ``type`` 必须是 ``public_articles.ARTICLES`` 里真实存在的类型，
#: 否则用户点开必然 404。``cheat_notice``（防骗提醒）是当前唯一可用的反诈文章。
DEFAULT_FRAUD_ARTICLES: list[SecurityFraudArticle] = [
    SecurityFraudArticle(
        type="cheat_notice",
        mark="防",
        tone="guide-mark",
        title="先核实，再转账",
        description="冒充客服或红娘索要验证码、投资充值、借款代收，一律先向平台核实。",
    )
]

# 治理统计是聚合查询，进页面就扫一次没必要；同月内 5 分钟缓存足够，
# 后台复核举报后最多延迟一个 TTL 生效。
_CACHE_TTL_SECONDS = 300.0
_CACHE: dict[str, tuple[float, SecurityGovernance | None]] = {}


def clear_governance_cache() -> None:
    """测试与后台处置后可主动失效缓存。"""
    _CACHE.clear()


def _pick(content: dict[str, Any], key: str, default: str) -> str:
    value = content.get(key)
    if isinstance(value, str):
        text_value = value.strip()
        if text_value != "":
            return text_value
    return default


async def _governance(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    use_cache: bool = True,
) -> SecurityGovernance | None:
    """本月治理数字（举报工单 + 账号处置）。

    两项都为 0 时返回 ``None``（前端显示「暂无本月治理数据」），
    避免把一个“全 0 的空月”渲染成一排 0 冒充治理成果。
    """
    current = now if now is not None else datetime.now(UTC)
    month_key = current.strftime("%Y-%m")
    if use_cache:
        cached = _CACHE.get(month_key)
        if cached is not None and (time.monotonic() - cached[0]) < _CACHE_TTL_SECONDS:
            return cached[1]
    # 库里的 reviewed_at / created_at 由 UTC_TIMESTAMP() 写入（naive UTC），比较值也要 naive。
    month_start = current.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    )
    report_row = (
        await db.execute(
            text(
                "SELECT COUNT(*) AS total_processed,"
                " COALESCE(SUM(action IN ('hide_content','restore_content')), 0) AS content_actions,"
                " COALESCE(SUM(status = 2), 0) AS dismissed"
                " FROM user_report"
                " WHERE status IN (1, 2) AND reviewed_at >= :month_start"
            ),
            {"month_start": month_start},
        )
    ).mappings().first()
    # 封禁口径来自 user_restriction：TOTAL_BAN 按 ends_at 区分临时/永久，
    # 其余类型（POST/COMMENT/MESSAGE/APPLICATION_RESTRICTED）算功能限制。
    restriction_row = (
        await db.execute(
            text(
                "SELECT"
                " COALESCE(SUM(restriction_type = 'TOTAL_BAN' AND ends_at IS NOT NULL), 0) AS temporary_bans,"
                " COALESCE(SUM(restriction_type = 'TOTAL_BAN' AND ends_at IS NULL), 0) AS permanent_bans,"
                " COALESCE(SUM(restriction_type <> 'TOTAL_BAN'), 0) AS other_restrictions"
                " FROM user_restriction"
                " WHERE created_at >= :month_start"
            ),
            {"month_start": month_start},
        )
    ).mappings().first()
    total_processed = int((report_row or {}).get("total_processed") or 0)
    content_actions = int((report_row or {}).get("content_actions") or 0)
    dismissed = int((report_row or {}).get("dismissed") or 0)
    temporary_bans = int((restriction_row or {}).get("temporary_bans") or 0)
    permanent_bans = int((restriction_row or {}).get("permanent_bans") or 0)
    other_restrictions = int((restriction_row or {}).get("other_restrictions") or 0)
    result: SecurityGovernance | None
    if (
        total_processed <= 0
        and content_actions <= 0
        and dismissed <= 0
        and temporary_bans <= 0
        and permanent_bans <= 0
        and other_restrictions <= 0
    ):
        result = None
    else:
        result = SecurityGovernance(
            month_label=f"{current.month}月",
            total_processed=total_processed,
            content_actions=content_actions,
            dismissed=dismissed,
            temporary_bans=temporary_bans,
            permanent_bans=permanent_bans,
            other_restrictions=other_restrictions,
        )
    if use_cache:
        _CACHE[month_key] = (time.monotonic(), result)
    return result


async def get_security_center(db: AsyncSession) -> SecurityCenterResponse:
    content = await load_config(db, "platform_content")
    hero = SecurityHero(
        title=_pick(content, "security_hero_title", DEFAULT_HERO.title),
        subtitle=_pick(content, "security_hero_subtitle", DEFAULT_HERO.subtitle),
        note=_pick(content, "security_hero_note", DEFAULT_HERO.note),
    )
    return SecurityCenterResponse(
        hero=hero,
        governance=await _governance(db),
        fraud_articles=DEFAULT_FRAUD_ARTICLES,
        ai_support_title=_pick(content, "security_support_title", DEFAULT_AI_SUPPORT_TITLE),
        ai_support_description=_pick(
            content, "security_support_description", DEFAULT_AI_SUPPORT_DESCRIPTION
        ),
        risk_notice=_pick(content, "security_risk_notice", DEFAULT_RISK_NOTICE),
    )

"""Public, allowlisted article content backed by platform configuration."""

from __future__ import annotations

from datetime import date
from html import unescape
from html.parser import HTMLParser

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.admin_config import PublicArticleDetail, PublicArticleItem
from app.services.runtime_config import load_config

ARTICLES: tuple[tuple[str, str, str, str], ...] = (
    ("terms", "用户协议", "user_agreement", "平台内容"),
    ("privacy", "隐私政策", "privacy_policy", "平台内容"),
    ("children", "儿童/青少年个人信息保护规则及监护人须知", "children_policy", "平台内容"),
    ("thirdparty", "第三方信息共享清单", "thirdparty_list", "平台内容"),
    ("platform_mgmt", "平台管理规范", "platform_rules", "平台内容"),
    ("community_norm", "平台社区规范", "community_rules", "平台内容"),
    ("ai_service", "大模型生成式人工智能服务协议", "ai_service_agreement", "平台内容"),
    ("governance_notice", "治理公示栏", "governance_notice", "平台内容"),
    ("about", "关于我们", "about_html", "自定义页面"),
    ("cheat_notice", "防骗提醒", "cheat_html", "自定义页面"),
)
ARTICLE_BY_TYPE = {item[0]: item for item in ARTICLES}


class _PlainText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._suppressed = 0

    def handle_data(self, data: str) -> None:
        if self._suppressed == 0 and data.strip():
            self.parts.append(data.strip())

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "iframe", "object", "svg"}:
            self._suppressed += 1
            return
        if self._suppressed > 0:
            return
        if tag in {"p", "div", "li", "br", "h1", "h2", "h3", "section"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "iframe", "object", "svg"}:
            self._suppressed = max(0, self._suppressed - 1)
            return
        if self._suppressed > 0:
            return
        if tag in {"p", "div", "li", "h1", "h2", "h3", "section"}:
            self.parts.append("\n")


def _article_text(value: str) -> str:
    parser = _PlainText()
    parser.feed(value)
    return "\n\n".join(part for part in "\n".join(parser.parts).splitlines() if part.strip()) or unescape(value)


def _warning_items(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value[:30]:
        if isinstance(item, str):
            normalized = " ".join(item.split())
            if normalized:
                result.append(normalized[:120])
    return result


async def list_public_articles(db: AsyncSession) -> list[PublicArticleItem]:
    content = await load_config(db, "platform_content")
    custom = await load_config(db, "platform_custom_pages")
    result = [PublicArticleItem(type="version", title="版本更新", clickable=False)]
    for article_type, title, key, namespace in ARTICLES:
        config = custom if namespace == "自定义页面" else content
        value = config.get(key)
        if article_type == "about":
            title = str(config.get("about_title") or title)[:64]
        if article_type == "cheat_notice":
            title = str(custom.get("cheat_title") or title)[:64]
        published = isinstance(value, str) and bool(value.strip())
        if article_type == "cheat_notice":
            published = published or bool(_warning_items(custom.get("warn_items")))
        result.append(PublicArticleItem(type=article_type, title=title, clickable=published))
    return result


async def get_public_article(db: AsyncSession, article_type: str) -> PublicArticleDetail:
    if article_type == "version":
        return PublicArticleDetail(type="version", title="版本更新", content="当前已是最新版本。", updated_at=date.today().isoformat())
    item = ARTICLE_BY_TYPE.get(article_type)
    if item is None:
        raise HTTPException(status_code=404, detail="文章不存在")
    _, default_title, key, namespace = item
    config = await load_config(db, "platform_custom_pages" if namespace == "自定义页面" else "platform_content")
    value = config.get(key)
    warnings = _warning_items(config.get("warn_items")) if article_type == "cheat_notice" else []
    if (not isinstance(value, str) or not value.strip()) and not warnings:
        raise HTTPException(status_code=404, detail="文章尚未发布")
    title = default_title
    if article_type == "about":
        title = str(config.get("about_title") or default_title)[:64]
    elif article_type == "cheat_notice":
        title = str(config.get("cheat_title") or default_title)[:64]
    content = _article_text(value) if isinstance(value, str) and value.strip() else ""
    if warnings:
        warning_text = "小心以下行为的用户：\n" + "\n".join(f"- {warning}" for warning in warnings)
        content = (content + "\n\n" + warning_text).strip()
    return PublicArticleDetail(type=article_type, title=title, content=content, updated_at=None)

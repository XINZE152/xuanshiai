"""Sensitive-word decisions for community text."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_CACHE_TTL_SEC = 60.0
_cached_words: list[str] = []
_cached_at: float = 0.0
_cache_loaded: bool = False


@dataclass(frozen=True)
class ContentDecision:
    action: str
    display_content: str
    matched_words: tuple[str, ...] = ()
    max_level: int = 0
    provider: str = "local"


def clear_sensitive_word_cache() -> None:
    """Test helper / admin hook after word-bank changes."""
    global _cached_words, _cached_at, _cache_loaded
    _cached_words = []
    _cached_at = 0.0
    _cache_loaded = False


async def load_active_sensitive_words(db: AsyncSession, *, force: bool = False) -> list[str]:
    global _cached_words, _cached_at, _cache_loaded
    now = time.monotonic()
    # 用 _cache_loaded 而非 _cached_words 判定，避免空词库时每次调用都穿透查库
    if not force and _cache_loaded and (now - _cached_at) < _CACHE_TTL_SEC:
        return list(_cached_words)
    result = await db.execute(
        text("""SELECT word, COALESCE(level, 1) AS level,
                      COALESCE(action, CASE WHEN level = 1 THEN 'reject' ELSE 'replace' END) AS action
               FROM config_sensitive_word
               WHERE is_active = 1 AND word IS NOT NULL AND word <> ''""")
    )
    try:
        rows = result.mappings().all()
    except (AttributeError, TypeError):
        # Unit-test fakes and legacy adapters may not expose row mappings; an
        # unavailable optional word bank must not turn ordinary publishing into
        # a database-shape error.
        rows = []
    words: list[str] = []
    for row in rows:
        value = row.get("word") if hasattr(row, "get") else row["word"]
        if value is None:
            continue
        token = str(value).strip()
        if token:
            words.append(f"{int(row.get('level') or 1)}\t{row.get('action') or 'replace'}\t{token}")
    _cached_words = words
    _cached_at = now
    _cache_loaded = True
    return list(words)


def find_sensitive_hit(content: str | None, words: Iterable[str]) -> str | None:
    text_value = (content or "").strip()
    if not text_value:
        return None
    lowered = text_value.casefold()
    for word in words:
        token = (word or "").split("\t")[-1].strip()
        if not token:
            continue
        if token.casefold() in lowered:
            return token
    return None


async def assert_text_allowed(db: AsyncSession, content: str | None, *, field: str = "内容") -> None:
    """Raise 422 when content contains an active sensitive word."""
    words = await load_active_sensitive_words(db)
    hit = None
    normalized = _normalized_text((content or "").strip())
    highest: tuple[int, str, str] | None = None
    priority = {"reject": 3, "manual_review": 2, "replace": 1}
    for encoded in words:
        level_s, action, token = encoded.split("\t", 2)
        if _normalized_text(token) in normalized and (highest is None or (int(level_s), priority.get(action, 0)) > (highest[0], priority.get(highest[1], 0))):
            highest = (int(level_s), action, token)
    if highest is not None and highest[1] == "reject":
        hit = highest[2]
    if hit is not None:
        raise HTTPException(422, detail=f"{field}含违规词，请修改后重试")


def _normalized_text(value: str) -> str:
    return "".join(ch for ch in value.casefold() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


async def decide_text(db: AsyncSession, content: str | None) -> ContentDecision:
    value = (content or "").strip()
    if not value:
        return ContentDecision("allow", value)
    normalized = _normalized_text(value)
    hits: list[tuple[int, str, str]] = []
    for encoded in await load_active_sensitive_words(db):
        level_s, action, token = encoded.split("\t", 2)
        if _normalized_text(token) in normalized:
            hits.append((int(level_s), action, token))
    if not hits:
        return ContentDecision("allow", value)
    priority = {"reject": 3, "manual_review": 2, "replace": 1}
    level, action, _ = max(hits, key=lambda item: (item[0], priority.get(item[1], 0)))
    unique = tuple(dict.fromkeys(item[2] for item in hits))
    if action != "replace":
        return ContentDecision(action, value, unique, level)
    display = value
    for token in sorted(unique, key=len, reverse=True):
        display = display.replace(token, "*" * max(1, len(token)))
    return ContentDecision("replace", display, unique, level)


async def moderate_text(
    db: AsyncSession,
    content: str | None,
    *,
    field: str = "内容",
    scene: int = 1,
    openid: str | None = None,
) -> ContentDecision:
    """Apply local rules, then optional Aliyun and WeChat providers once each.

    ``scene``/``openid`` are additive optional args: existing callers keep their
    behaviour (scene defaults to 1 / user profile). Callers on comment/forum/
    social paths should pass the matching scene so 微信 msgSecCheck uses the
    right classification context.
    """
    # Keep the existing assertion hook for compatibility with callers and
    # tests. It blocks local reject rules before any external request.
    await assert_text_allowed(db, content, field=field)
    local_decision = await decide_text(db, content)
    if local_decision.action == "reject":
        return local_decision

    from app.services.aliyun_content_moderation import moderate_with_aliyun

    provider_decision = await moderate_with_aliyun(content or "")
    if provider_decision.action != "allow":
        display_content = content or ""
        if provider_decision.action == "replace":
            for token in sorted(provider_decision.matched_words, key=len, reverse=True):
                if token:
                    display_content = display_content.replace(
                        token,
                        "*" * max(1, len(token)),
                    )
        return ContentDecision(
            action=provider_decision.action,
            display_content=display_content,
            matched_words=provider_decision.matched_words,
            max_level=provider_decision.risk_level,
            provider=provider_decision.provider,
        )

    # 微信官方文本检测（默认关闭；命中违规→reject，异常→manual_review）。
    from app.services.wechat_sec_check import check_text

    wechat_decision = await check_text(content or "", scene=scene, openid=openid)
    if wechat_decision.action == "allow":
        return local_decision
    # 返回既有 action 语义，交给调用方的 reject/manual_review 分支统一处理
    # （通知、422 文案、待审落库），不在本层抛异常绕过那些逻辑。
    # reject 时 display_content 用原文：调用方会拒绝该内容，不会展示。
    display_content = local_decision.display_content if wechat_decision.action == "manual_review" else (content or "")
    return ContentDecision(
        action=wechat_decision.action,
        display_content=display_content,
        matched_words=local_decision.matched_words,
        max_level=max(local_decision.max_level, wechat_decision.risk_level),
        provider=wechat_decision.provider,
    )

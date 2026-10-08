"""安全的资料卡/海报公开导出。

导出不是叙事层读取的别名：只允许当前用户、personal、当前正式 revision、
有效授权和已明确采用的资料卡字段进入公开白名单。confirmed narrative、心理
洞察、原始对话与 ideal_partner 永远不作为海报数据来源。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.ai_profile import ProfileSubject
from app.services.ai.profile import AIInputError
from app.services.ai.profile_card import _require_consent


class ProfileCardExportUnavailable(Exception):
    code = "PROFILE_CARD_EXPORT_NOT_AVAILABLE"
    status_code = 404
    retryable = False

    def __init__(self, message: str = "尚未采用可公开的资料卡内容") -> None:
        super().__init__(message)
        self.message = message


class ProfileCardExportStale(Exception):
    code = "RESULT_STALE"
    status_code = 409
    retryable = False

    def __init__(self, message: str = "画像来源或公开授权已变化，请重新整理并采用") -> None:
        super().__init__(message)
        self.message = message


def _json(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return default
    return default


async def _first_row(result: Any) -> dict[str, Any] | None:
    return result.mappings().first()


def _list_values(value: Any) -> list[str]:
    parsed = _json(value, [])
    if isinstance(parsed, dict):
        values: list[Any] = []
        for item in parsed.values():
            if isinstance(item, list):
                values.extend(item)
        parsed = values
    if not isinstance(parsed, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in parsed:
        text_value = str(item or "").strip()
        if text_value and text_value not in seen:
            seen.add(text_value)
            result.append(text_value)
    return result


def _public_fields(applied_meta: Any) -> set[str]:
    meta = _json(applied_meta, {})
    if not isinstance(meta, dict):
        return set()
    raw = meta.get("ai_generated")
    return {str(item) for item in raw if str(item) in {"self_intro", "personal_tags"}} if isinstance(raw, list) else set()


async def export_public_profile_card(
    db: AsyncSession,
    user_id: int,
    *,
    subject: ProfileSubject,
    revision_id: int,
) -> dict[str, Any]:
    """Return only adopted public profile fields for a poster.

    ``revision_id`` is mandatory at the route boundary so a late page response
    cannot silently export a newer or older authority than the one being viewed.
    """
    if subject is not ProfileSubject.PERSONAL:
        raise AIInputError("海报公开导出仅支持 personal 本人画像")
    if revision_id < 1:
        raise AIInputError("revision_id 必须为正整数")

    await _require_consent(db, user_id)

    current = await _first_row(
        await db.execute(
            text(
                "SELECT r.id, r.revision_no "
                "FROM ai_profile_revision r "
                "LEFT JOIN ai_profile_draft d ON d.draft_id = r.draft_id "
                "WHERE r.user_id = :user_id AND r.subject = :subject "
                "AND (d.status = 'published' OR r.draft_id IS NULL) "
                "ORDER BY r.revision_no DESC, r.id DESC LIMIT 1"
            ),
            {"user_id": user_id, "subject": ProfileSubject.PERSONAL.value},
        )
    )
    if current is None:
        raise ProfileCardExportUnavailable("暂无可公开的 personal 正式版本")
    current_revision_id = int(current["id"])
    if current_revision_id != revision_id:
        raise ProfileCardExportStale()

    narrative = await _first_row(
        await db.execute(
            text(
                "SELECT status, revision_id FROM ai_profile_summary "
                "WHERE user_id = :user_id AND subject = :subject "
                "AND status NOT IN ('deleted', 'stale') "
                "ORDER BY created_at DESC, id DESC LIMIT 1"
            ),
            {"user_id": user_id, "subject": ProfileSubject.PERSONAL.value},
        )
    )
    if narrative is None or str(narrative.get("status") or "") != "confirmed":
        raise ProfileCardExportUnavailable("请先确认当前 personal 画像成稿")
    if int(narrative.get("revision_id") or 0) != current_revision_id:
        raise ProfileCardExportStale("画像叙事与当前正式版本不一致，请重新确认")

    card = await _first_row(
        await db.execute(
            text(
                "SELECT draft_id, source_revision_id, status, applied_meta "
                "FROM ai_profile_card_draft WHERE user_id = :user_id "
                "AND status = 'applied' ORDER BY applied_at DESC, updated_at DESC LIMIT 1"
            ),
            {"user_id": user_id},
        )
    )
    if card is None or not _public_fields(card.get("applied_meta")):
        raise ProfileCardExportUnavailable()
    if int(card.get("source_revision_id") or 0) != current_revision_id:
        raise ProfileCardExportStale()

    profile = await _first_row(
        await db.execute(
            text(
                "SELECT p.self_intro, p.interest_tags, p.personality_tags, p.tags, "
                "COALESCE(pr.only_vip_can_see_detail, 0) AS only_vip_can_see_detail "
                "FROM user_profile p LEFT JOIN user_privacy pr ON pr.user_id = p.user_id "
                "WHERE p.user_id = :user_id"
            ),
            {"user_id": user_id},
        )
    ) or {}
    fields = _public_fields(card.get("applied_meta"))
    self_intro = str(profile.get("self_intro") or "").strip() if "self_intro" in fields else ""
    tags = [] if int(profile.get("only_vip_can_see_detail") or 0) else _list_values(profile.get("interest_tags")) + _list_values(profile.get("personality_tags"))
    if "personal_tags" not in fields:
        tags = []
    tags = list(dict.fromkeys(tags))[:10]
    if not self_intro and not tags:
        raise ProfileCardExportUnavailable("已采用内容当前不可公开，请重新整理公开介绍")

    return {
        "status": "ready",
        "subject": ProfileSubject.PERSONAL.value,
        "revision_id": current_revision_id,
        "source_revision_id": int(card["source_revision_id"]),
        "public": {
            "persona_title": "我的公开介绍",
            "persona_tags": tags,
            "self_intro": self_intro,
        },
    }

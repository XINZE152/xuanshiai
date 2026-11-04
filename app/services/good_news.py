"""红娘喜讯业务服务。

内容主体：``admin_content_item``（domain=good_news）。
流程数据：``good_news_meta`` / ``good_news_consent`` / ``good_news_blessing`` /
``good_news_report`` / ``good_news_audit``。

设计约束：
- 公共列表只返回审核通过、已发布且双方同意未撤回的内容；
- 工作台按红娘/组织范围过滤；
- 审核状态独立于行级 status；
- 祝福用唯一约束去重，计数与明细同事务更新；
- 公共 DTO 与现有前端字段名保持扁平兼容。
"""

from __future__ import annotations

import html
import json
import re
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from PIL.Image import DecompressionBombError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.schemas.admin_content import ContentItem, ContentItemPage
from app.schemas.good_news import (
    GOOD_NEWS_STAGES,
    GoodNewsBlessingResponse,
    GoodNewsConfig,
    GoodNewsConsentItem,
    GoodNewsConsentPage,
    GoodNewsItem,
    GoodNewsPage,
    GoodNewsPennantItem,
    GoodNewsPennantPage,
    GoodNewsPhotoResponse,
    GoodNewsReportCreate,
    GoodNewsReportItem,
    GoodNewsReportPage,
    GoodNewsReviewRequest,
)
from app.services.admin_config import DEFAULT_CONFIGS, get_config as get_admin_config
from app.services.notifications import emit_notification

GOOD_NEWS_DOMAIN = "good_news"
PENNANT_DOMAIN = "good_news_pennant"
DEFAULT_CONSENT_SCOPES = ["name", "avatar", "photos", "content", "matchmaker"]
IMAGE_MAX_BYTES = 5 * 1024 * 1024
IMAGE_MAX_PIXELS = 25_000_000

_META_SELECT = """
    c.*,
    m.stage AS meta_stage,
    m.review_status AS meta_review_status,
    m.public_status AS meta_public_status,
    m.source_type AS meta_source_type,
    m.source_id AS meta_source_id,
    m.couple_key AS meta_couple_key,
    m.from_user_id AS meta_from_user_id,
    m.to_user_id AS meta_to_user_id,
    m.matchmaker_id AS meta_matchmaker_id,
    m.organization_id AS meta_organization_id,
    m.meet_duration_months AS meta_meet_duration_months,
    m.display_mode AS meta_display_mode,
    m.verification_level AS meta_verification_level,
    m.submitted_by_user_id AS meta_submitted_by_user_id,
    m.submitted_by_role AS meta_submitted_by_role,
    m.blessing_count AS meta_blessing_count,
    m.published_at AS meta_published_at,
    m.reviewed_by AS meta_reviewed_by,
    m.reviewed_at AS meta_reviewed_at,
    m.reject_reason AS meta_reject_reason
"""

_PUBLIC_WHERE = """
    c.tenant_id = 1
    AND c.domain = 'good_news'
    AND c.status = 1
    AND m.review_status = 'APPROVED'
    AND m.public_status = 'PUBLISHED'
    AND EXISTS (
        SELECT 1 FROM good_news_consent cf
        WHERE cf.news_id = c.id AND cf.role = 'FROM' AND cf.revoked_at IS NULL
    )
    AND EXISTS (
        SELECT 1 FROM good_news_consent ct
        WHERE ct.news_id = c.id AND ct.role = 'TO' AND ct.revoked_at IS NULL
    )
"""


def _parse_extra(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if not raw:
        return {}
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _as_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _parse_images(extra: dict[str, Any]) -> list[str]:
    raw = extra.get("images")
    if raw is None:
        raw = extra.get("images_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raw = []
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    for value in raw:
        item = str(value).strip()
        if item:
            result.append(item)
    return result


def _content_text(extra: dict[str, Any]) -> str:
    for key in ("content", "content_html", "body"):
        value = extra.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _format_duration(months: int | None) -> str | None:
    if months is None:
        return None
    if months <= 0:
        return "不到 1 个月"
    if months < 12:
        return f"{months} 个月"
    years, remainder = divmod(months, 12)
    return f"{years} 年" + (f" {remainder} 个月" if remainder else "")


def _mask_name(name: str | None, mode: str) -> str | None:
    value = (name or "").strip()
    if not value:
        return None
    if mode == "FULL":
        return value
    if mode == "ANONYMOUS":
        return "匿名会员"
    if len(value) == 1:
        return value + "*"
    return value[0] + "*" * (len(value) - 1)


def _mask_avatar(avatar: str | None, mode: str) -> str | None:
    if mode == "ANONYMOUS":
        return None
    return avatar


def _clean_images(images: list[str]) -> list[str]:
    if len(images) > 9:
        raise HTTPException(422, detail="一条喜讯最多上传 9 张图片")
    result: list[str] = []
    for raw in images:
        value = str(raw).strip()
        if not value:
            continue
        if not value.startswith("/storage/"):
            raise HTTPException(422, detail="图片需先通过喜讯图片上传接口上传")
        result.append(value)
    return result


def _duration_months(body: GoodNewsReportCreate) -> int | None:
    if body.meet_duration_months is not None:
        return body.meet_duration_months
    raw = (body.meet_duration or "").strip()
    if not raw:
        return None
    match = re.search(r"\d+", raw)
    return int(match.group()) if match else None


def _couple_key(from_user_id: int | None, to_user_id: int | None, partner_name: str, submitter_id: int) -> str | None:
    if from_user_id and to_user_id:
        left, right = sorted((from_user_id, to_user_id))
        return f"{left}:{right}"
    partner = partner_name.strip().lower()
    if partner:
        return f"submitter:{submitter_id}:{partner}"
    return None


def _display_title(from_name: str, to_name: str) -> str:
    if from_name and to_name:
        return f"{from_name} & {to_name}"
    return to_name or from_name or "喜讯"


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    try:
        value = row[key]
    except (KeyError, TypeError):
        return default
    return default if value is None else value


def _item_from_row(
    row: Any,
    *,
    public_view: bool,
    blessed_by_me: bool = False,
) -> GoodNewsItem:
    extra = _parse_extra(row.get("extra_json"))
    display_mode = str(row.get("meta_display_mode") or extra.get("display_mode") or "MASKED")
    review_status = str(row.get("meta_review_status") or extra.get("review_status") or "PENDING")
    public_status = str(row.get("meta_public_status") or extra.get("public_status") or "DRAFT")
    months = _as_int(row.get("meta_meet_duration_months"))
    raw_duration = str(extra.get("meet_duration") or "").strip() or None
    from_name = str(extra.get("feedback_name") or extra.get("from_name") or "").strip() or None
    to_name = str(extra.get("partner_name") or extra.get("to_name") or "").strip() or None
    from_avatar = str(extra.get("from_avatar") or "").strip() or None
    to_avatar = str(extra.get("to_avatar") or "").strip() or None
    from_user_id = _as_int(row.get("meta_from_user_id")) or _as_int(extra.get("from_user_id"))
    to_user_id = _as_int(row.get("meta_to_user_id")) or _as_int(extra.get("to_user_id"))
    if public_view:
        from_name = _mask_name(from_name, display_mode)
        to_name = _mask_name(to_name, display_mode)
        from_avatar = _mask_avatar(from_avatar, display_mode)
        to_avatar = _mask_avatar(to_avatar, display_mode)
        # 公共 DTO 不暴露会员 ID，避免被用于枚举资料或越权跳转。
        from_user_id = None
        to_user_id = None
    return GoodNewsItem(
        id=int(row["id"]),
        stage=str(row.get("meta_stage") or extra.get("stage") or extra.get("status") or ""),
        stage_label=str(row.get("meta_stage") or extra.get("stage") or extra.get("status") or "喜讯"),
        from_user_id=from_user_id,
        to_user_id=to_user_id,
        from_name=from_name,
        to_name=to_name,
        from_avatar=from_avatar,
        to_avatar=to_avatar,
        matchmaker_id=_as_int(row.get("meta_matchmaker_id")) or _as_int(extra.get("matchmaker_id")),
        matchmaker_name=str(extra.get("matchmaker_name") or "服务红娘"),
        meet_duration=raw_duration or (str(months) if months is not None else None),
        meet_duration_months=months,
        meet_duration_text=_format_duration(months),
        images=_parse_images(extra),
        content=_content_text(extra),
        blessing=str(extra.get("blessing") or "") or None,
        blessing_count=int(row.get("meta_blessing_count") or 0),
        blessed_by_me=blessed_by_me,
        verification_level=str(row.get("meta_verification_level") or extra.get("verification_level") or "SELF_REPORTED"),
        display_mode=display_mode,
        review_status=review_status,
        public_status=public_status,
        pending_review=review_status == "PENDING",
        created_at=row.get("created_at"),
        published_at=row.get("meta_published_at"),
    )


async def _fetch_row(db: AsyncSession, news_id: int) -> Any:
    return (
        await db.execute(
            text(
                f"SELECT {_META_SELECT} FROM admin_content_item c "
                "LEFT JOIN good_news_meta m ON m.news_id = c.id "
                "WHERE c.id = :id AND c.domain = 'good_news' AND c.tenant_id = 1"
            ),
            {"id": news_id},
        )
    ).mappings().first()


async def _fetch_public_row(db: AsyncSession, news_id: int) -> Any:
    return (
        await db.execute(
            text(
                f"SELECT {_META_SELECT} FROM admin_content_item c "
                "JOIN good_news_meta m ON m.news_id = c.id "
                f"WHERE c.id = :id AND {_PUBLIC_WHERE}"
            ),
            {"id": news_id},
        )
    ).mappings().first()


async def _consent_complete(db: AsyncSession, news_id: int) -> bool:
    rows = (
        await db.execute(
            text("SELECT role FROM good_news_consent WHERE news_id = :id AND revoked_at IS NULL"),
            {"id": news_id},
        )
    ).mappings().all()
    return {"FROM", "TO"}.issubset({str(row["role"]) for row in rows})


async def _consent_scope_problem(
    db: AsyncSession, news_id: int, display_mode: str, has_images: bool
) -> str | None:
    required = {"content"}
    if has_images:
        required.add("photos")
    if display_mode != "ANONYMOUS":
        required.add("name")
    if display_mode == "FULL":
        required.add("avatar")
    rows = (
        await db.execute(
            text("SELECT role, scopes_json FROM good_news_consent WHERE news_id = :id AND revoked_at IS NULL"),
            {"id": news_id},
        )
    ).mappings().all()
    scopes_by_role: dict[str, set[str]] = {}
    for row in rows:
        raw = row.get("scopes_json")
        scopes: set[str] = set()
        if isinstance(raw, list):
            scopes = {str(item) for item in raw}
        elif isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    scopes = {str(item) for item in parsed}
            except (TypeError, ValueError):
                scopes = set()
        scopes_by_role[str(row["role"])] = scopes
    if not {"FROM", "TO"}.issubset(scopes_by_role):
        return "双方同意记录不完整"
    for role in ("FROM", "TO"):
        missing = required - scopes_by_role[role]
        if missing:
            return f"{role} 同意范围缺少：{','.join(sorted(missing))}"
    return None


async def _blessed_ids(db: AsyncSession, viewer_id: int | None, news_ids: list[int]) -> set[int]:
    if not viewer_id or not news_ids:
        return set()
    placeholders = ", ".join(f":id{i}" for i in range(len(news_ids)))
    params: dict[str, Any] = {"user_id": viewer_id}
    params.update({f"id{i}": value for i, value in enumerate(news_ids)})
    rows = (
        await db.execute(
            text(f"SELECT news_id FROM good_news_blessing WHERE user_id = :user_id AND news_id IN ({placeholders})"),
            params,
        )
    ).mappings().all()
    return {int(row["news_id"]) for row in rows}


async def _load_user(db: AsyncSession, user_id: int | None) -> dict[str, Any] | None:
    if not user_id:
        return None
    row = (
        await db.execute(
            text("SELECT id, nickname, avatar FROM users WHERE id = :id AND status = 1"),
            {"id": user_id},
        )
    ).mappings().first()
    return dict(row) if row else None


async def _load_matchmaker_name(db: AsyncSession, user_id: int | None) -> str:
    if not user_id:
        return ""
    row = (
        await db.execute(
            text(
                """SELECT COALESCE(p.display_name, u.nickname, '服务红娘') AS name
                   FROM users u
                   LEFT JOIN matchmaker_workspace_profile p ON p.user_id = u.id AND p.status = 1
                   WHERE u.id = :id"""
            ),
            {"id": user_id},
        )
    ).mappings().first()
    return str(row["name"]) if row and row["name"] else ""


async def _audit(
    db: AsyncSession,
    *,
    news_id: int | None,
    report_id: int | None,
    actor_user_id: int | None,
    actor_role: str,
    action: str,
    reason: str | None = None,
    before_status: str | None = None,
    after_status: str | None = None,
) -> None:
    await db.execute(
        text(
            "INSERT INTO good_news_audit "
            "(news_id, report_id, actor_user_id, actor_role, action, reason, before_status, after_status) "
            "VALUES (:news_id, :report_id, :actor_user_id, :actor_role, :action, :reason, :before_status, :after_status)"
        ),
        {
            "news_id": news_id,
            "report_id": report_id,
            "actor_user_id": actor_user_id,
            "actor_role": actor_role,
            "action": action,
            "reason": reason,
            "before_status": before_status,
            "after_status": after_status,
        },
    )


async def _insert_consent(
    db: AsyncSession,
    *,
    news_id: int,
    user_id: int | None,
    role: str,
    scopes: list[str],
    consent_version: str,
    consent_source: str,
) -> None:
    await db.execute(
        text(
            "INSERT INTO good_news_consent "
            "(news_id, user_id, role, scopes_json, consent_version, consent_source, consented_at) "
            "VALUES (:news_id, :user_id, :role, :scopes, :version, :source, NOW()) "
            "ON DUPLICATE KEY UPDATE user_id = :user_id, scopes_json = :scopes, "
            "consent_version = :version, consent_source = :source, consented_at = NOW(), revoked_at = NULL"
        ),
        {
            "news_id": news_id,
            "user_id": user_id,
            "role": role,
            "scopes": json.dumps(scopes or DEFAULT_CONSENT_SCOPES, ensure_ascii=False),
            "version": consent_version,
            "source": consent_source,
        },
    )


async def _notify_submitter(db: AsyncSession, news_id: int, title: str, content: str) -> None:
    row = (
        await db.execute(
            text("SELECT submitted_by_user_id FROM good_news_meta WHERE news_id = :id"),
            {"id": news_id},
        )
    ).mappings().first()
    if not row or not row.get("submitted_by_user_id"):
        return
    try:
        await emit_notification(
            db,
            recipient_user_id=int(row["submitted_by_user_id"]),
            actor_user_id=None,
            event_type="good_news_review",
            title=title,
            content=content,
            target_type="good_news",
            target_id=news_id,
        )
    except Exception:
        # 通知失败不应回滚审核结果；审核本身已写审计。
        pass


async def _ensure_meta(db: AsyncSession, news_id: int) -> None:
    exists = (
        await db.execute(
            text("SELECT news_id FROM good_news_meta WHERE news_id = :id"),
            {"id": news_id},
        )
    ).mappings().first()
    if exists:
        return
    content = (
        await db.execute(
            text("SELECT extra_json FROM admin_content_item WHERE id = :id AND domain = 'good_news' AND tenant_id = 1"),
            {"id": news_id},
        )
    ).mappings().first()
    if content is None:
        raise HTTPException(404, detail="喜讯不存在")
    extra = _parse_extra(content.get("extra_json"))
    stage = str(extra.get("stage") or extra.get("status") or "")
    await db.execute(
        text(
            "INSERT IGNORE INTO good_news_meta "
            "(news_id, stage, review_status, public_status, source_type, source_id, from_user_id, to_user_id, "
            "matchmaker_id, meet_duration_months, display_mode, verification_level, submitted_by_role) "
            "VALUES (:news_id, :stage, 'PENDING', 'DRAFT', :source_type, :source_id, :from_user_id, :to_user_id, "
            ":matchmaker_id, :months, :display_mode, :verification_level, 'ADMIN')"
        ),
        {
            "news_id": news_id,
            "stage": stage,
            "source_type": extra.get("source_type") or "MANUAL",
            "source_id": _as_int(extra.get("source_id")),
            "from_user_id": _as_int(extra.get("from_user_id")),
            "to_user_id": _as_int(extra.get("to_user_id")),
            "matchmaker_id": _as_int(extra.get("matchmaker_id")),
            "months": _as_int(extra.get("meet_duration_months")),
            "display_mode": extra.get("display_mode") or "MASKED",
            "verification_level": extra.get("verification_level") or "SELF_REPORTED",
        },
    )


async def list_public(
    db: AsyncSession,
    *,
    stage: str | None,
    page: int,
    page_size: int,
    viewer_id: int | None,
) -> GoodNewsPage:
    if stage and stage not in GOOD_NEWS_STAGES:
        raise HTTPException(422, detail="不支持的喜讯阶段")
    conditions = [_PUBLIC_WHERE]
    params: dict[str, Any] = {"limit": page_size, "offset": (page - 1) * page_size}
    if stage:
        conditions.append("m.stage = :stage")
        params["stage"] = stage
    where = " AND ".join(conditions)
    total = int(
        (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM admin_content_item c "
                    "JOIN good_news_meta m ON m.news_id = c.id "
                    f"WHERE {where}"
                ),
                params,
            )
        ).scalar()
        or 0
    )
    rows = (
        await db.execute(
            text(
                f"SELECT {_META_SELECT} FROM admin_content_item c "
                "JOIN good_news_meta m ON m.news_id = c.id "
                f"WHERE {where} "
                "ORDER BY COALESCE(m.published_at, c.created_at) DESC, c.id DESC "
                "LIMIT :limit OFFSET :offset"
            ),
            params,
        )
    ).mappings().all()
    ids = [int(row["id"]) for row in rows]
    blessed = await _blessed_ids(db, viewer_id, ids)
    return GoodNewsPage(
        items=[_item_from_row(row, public_view=True, blessed_by_me=int(row["id"]) in blessed) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
    )


async def get_public(db: AsyncSession, news_id: int, viewer_id: int | None) -> GoodNewsItem:
    row = await _fetch_public_row(db, news_id)
    if row is None:
        raise HTTPException(404, detail="喜讯不存在或未公开")
    blessed = await _blessed_ids(db, viewer_id, [news_id])
    return _item_from_row(row, public_view=True, blessed_by_me=news_id in blessed)


async def get_public_config(db: AsyncSession) -> GoodNewsConfig:
    try:
        snapshot = await get_admin_config(db, "tools_good_news")
        config = snapshot.config
    except HTTPException:
        config = dict(DEFAULT_CONFIGS["tools_good_news"][2])
    categories = config.get("categories") if isinstance(config.get("categories"), list) else []
    # 统一后台「锦旗飘飘」与前端「锦旗飘扬」的命名。
    normalized: list[dict] = []
    for item in categories:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        if str(row.get("value") or "") == "锦旗飘飘" or str(row.get("label") or "") == "锦旗飘飘":
            row["value"] = "锦旗飘扬"
            row["label"] = "锦旗飘扬"
            row["is_pennant"] = True
        normalized.append(row)
    blessings = config.get("blessings") if isinstance(config.get("blessings"), list) else []
    return GoodNewsConfig(
        title=str(config.get("title") or "脱单喜讯"),
        description=str(config.get("description") or "脱单喜讯"),
        share_cover_url=config.get("share_cover_url"),
        banner_url=config.get("banner_url"),
        categories=normalized,
        blessings=[str(item) for item in blessings if str(item).strip()],
    )


async def bless(db: AsyncSession, *, news_id: int, user_id: int) -> GoodNewsBlessingResponse:
    row = await _fetch_public_row(db, news_id)
    if row is None:
        raise HTTPException(404, detail="喜讯不存在或未公开")
    if user_id in {_as_int(row.get("meta_from_user_id")), _as_int(row.get("meta_to_user_id"))}:
        raise HTTPException(403, detail="不能给自己送祝福")
    result = await db.execute(
        text("INSERT IGNORE INTO good_news_blessing (news_id, user_id, created_at) VALUES (:news_id, :user_id, NOW())"),
        {"news_id": news_id, "user_id": user_id},
    )
    if int(result.rowcount or 0) > 0:
        await db.execute(
            text("UPDATE good_news_meta SET blessing_count = blessing_count + 1 WHERE news_id = :news_id"),
            {"news_id": news_id},
        )
    await db.commit()
    count = int(
        (
            await db.execute(
                text("SELECT blessing_count FROM good_news_meta WHERE news_id = :news_id"),
                {"news_id": news_id},
            )
        ).scalar()
        or 0
    )
    return GoodNewsBlessingResponse(
        news_id=news_id, blessing_count=count, blessed_by_me=True, blessed=True
    )


async def unbless(db: AsyncSession, *, news_id: int, user_id: int) -> GoodNewsBlessingResponse:
    result = await db.execute(
        text("DELETE FROM good_news_blessing WHERE news_id = :news_id AND user_id = :user_id"),
        {"news_id": news_id, "user_id": user_id},
    )
    if int(result.rowcount or 0) > 0:
        await db.execute(
            text("UPDATE good_news_meta SET blessing_count = GREATEST(blessing_count - 1, 0) WHERE news_id = :news_id"),
            {"news_id": news_id},
        )
    await db.commit()
    count = int(
        (
            await db.execute(
                text("SELECT blessing_count FROM good_news_meta WHERE news_id = :news_id"),
                {"news_id": news_id},
            )
        ).scalar()
        or 0
    )
    return GoodNewsBlessingResponse(
        news_id=news_id, blessing_count=count, blessed_by_me=False, blessed=False
    )


async def _ensure_workspace_member(db: AsyncSession, actor: Any, member_id: int) -> None:
    """红娘代登记只能关联自己/本组织服务范围内的会员。"""
    if actor is None:
        return
    params: dict[str, Any] = {"member_id": member_id}
    if getattr(actor, "scope", "SELF") == "SELF":
        condition = (
            "EXISTS (SELECT 1 FROM resource_assignment ra WHERE ra.user_id = :member_id "
            "AND ra.status = 1 AND ra.matchmaker_id = :matchmaker_id)"
        )
        params["matchmaker_id"] = actor.user_id
    else:
        if not getattr(actor, "organization_id", None):
            raise HTTPException(403, detail="超级服务红娘尚未绑定有效组织范围")
        condition = (
            "EXISTS (SELECT 1 FROM resource_assignment ra WHERE ra.user_id = :member_id "
            "AND ra.status = 1 AND ra.organization_id = :organization_id)"
        )
        params["organization_id"] = actor.organization_id
    allowed = (await db.execute(text(f"SELECT {condition}"), params)).scalar()
    if not allowed:
        raise HTTPException(403, detail="只能登记工作范围内的会员")


async def create_report(
    db: AsyncSession,
    *,
    submitter_id: int,
    submitter_role: str,
    body: GoodNewsReportCreate,
    matchmaker_id: int | None = None,
    organization_id: int | None = None,
    idempotency_key: str | None = None,
    actor: Any | None = None,
) -> tuple[GoodNewsItem, int]:
    if submitter_role not in {"MEMBER", "MATCHMAKER"}:
        raise HTTPException(422, detail="提交人角色不合法")
    if idempotency_key:
        existing = (
            await db.execute(
                text(
                    "SELECT id, news_id FROM good_news_report "
                    "WHERE submitter_id = :submitter_id AND idempotency_key = :key LIMIT 1"
                ),
                {"submitter_id": submitter_id, "key": idempotency_key},
            )
        ).mappings().first()
        if existing:
            if existing.get("news_id"):
                return await _get_item_for_submitter(db, int(existing["news_id"]), submitter_id), int(existing["id"])
            raise HTTPException(409, detail="该报喜已提交，正在处理")
    else:
        partner_key = (body.partner_name or "").strip()
        existing = (
            await db.execute(
                text(
                    "SELECT id FROM good_news_report "
                    "WHERE submitter_id = :submitter_id AND stage = :stage "
                    "AND COALESCE(partner_name, '') = :partner_name AND status = 'PENDING' "
                    "AND created_at >= (NOW() - INTERVAL 24 HOUR) LIMIT 1"
                ),
                {"submitter_id": submitter_id, "stage": body.stage, "partner_name": partner_key},
            )
        ).mappings().first()
        if existing:
            raise HTTPException(409, detail="24 小时内已提交过相同进展，请等待审核")

    from_user_id = body.from_user_id if submitter_role == "MATCHMAKER" else submitter_id
    to_user_id = body.to_user_id
    if submitter_role == "MATCHMAKER":
        if from_user_id:
            await _ensure_workspace_member(db, actor, from_user_id)
        if to_user_id:
            await _ensure_workspace_member(db, actor, to_user_id)
    from_profile = await _load_user(db, from_user_id)
    to_profile = await _load_user(db, to_user_id)
    if from_user_id and from_profile is None:
        raise HTTPException(404, detail="发起方会员不存在或不可用")
    if to_user_id and to_profile is None:
        raise HTTPException(404, detail="对方会员不存在或不可用")
    from_name = str((from_profile or {}).get("nickname") or "").strip()
    to_name = str((to_profile or {}).get("nickname") or body.partner_name or "").strip()
    if not from_name and submitter_role == "MEMBER":
        submitter_profile = await _load_user(db, submitter_id)
        from_name = str((submitter_profile or {}).get("nickname") or "").strip()
    if not from_name and not to_name:
        raise HTTPException(422, detail="至少需要填写对方称呼或关联会员")

    if submitter_role == "MATCHMAKER":
        matchmaker_id = submitter_id
    matchmaker_name = await _load_matchmaker_name(db, matchmaker_id) or "服务红娘"
    months = _duration_months(body)
    images = _clean_images(body.images)
    display_mode = body.display_mode or "MASKED"
    # 核验等级由服务端控制，客户端不能自封 PLATFORM_VERIFIED。
    verification_level = "SELF_REPORTED" if submitter_role == "MEMBER" else "MATCHMAKER_FOLLOWUP"
    source_type = body.source_type
    if submitter_role == "MEMBER":
        source_type = "SELF_REPORTED"
    elif source_type == "SELF_REPORTED":
        source_type = "MANUAL"
    couple_key = _couple_key(from_user_id, to_user_id, to_name, submitter_id)
    title = _display_title(from_name, to_name)
    raw_duration = (body.meet_duration or "").strip() or (str(months) if months is not None else None)
    extra = {
        "stage": body.stage,
        "status": body.stage,
        "feedback_name": from_name,
        "partner_name": to_name,
        "from_user_id": from_user_id,
        "to_user_id": to_user_id,
        "from_avatar": (from_profile or {}).get("avatar"),
        "to_avatar": (to_profile or {}).get("avatar"),
        "matchmaker_id": matchmaker_id,
        "matchmaker_name": matchmaker_name,
        "meet_duration": raw_duration,
        "meet_duration_months": months,
        "content": body.content,
        "content_html": html.escape(body.content),
        "images": images,
        "blessing": body.blessing,
        "public": False,
        "review_status": "PENDING",
        "public_status": "DRAFT",
        "display_mode": display_mode,
        "verification_level": verification_level,
        "source_type": source_type,
        "source_id": body.source_id,
    }
    result = await db.execute(
        text(
            "INSERT INTO admin_content_item "
            "(tenant_id, domain, title, subtitle, image_url, amount, status, sort, extra_json, created_by) "
            "VALUES (1, 'good_news', :title, :subtitle, :image_url, NULL, 1, 0, :extra, :created_by)"
        ),
        {
            "title": title,
            "subtitle": matchmaker_name or None,
            "image_url": images[0] if images else None,
            "extra": json.dumps(extra, ensure_ascii=False),
            "created_by": submitter_id,
        },
    )
    news_id = int(result.lastrowid or 0)
    if news_id <= 0:
        raise HTTPException(500, detail="喜讯创建失败")
    await db.execute(
        text(
            "INSERT INTO good_news_meta "
            "(news_id, stage, review_status, public_status, source_type, source_id, couple_key, from_user_id, "
            "to_user_id, matchmaker_id, organization_id, meet_duration_months, display_mode, verification_level, "
            "submitted_by_user_id, submitted_by_role, blessing_count) "
            "VALUES (:news_id, :stage, 'PENDING', 'DRAFT', :source_type, :source_id, :couple_key, :from_user_id, "
            ":to_user_id, :matchmaker_id, :organization_id, :months, :display_mode, :verification_level, "
            ":submitter_id, :submitter_role, 0)"
        ),
        {
            "news_id": news_id,
            "stage": body.stage,
            "source_type": source_type,
            "source_id": body.source_id,
            "couple_key": couple_key,
            "from_user_id": from_user_id,
            "to_user_id": to_user_id,
            "matchmaker_id": matchmaker_id,
            "organization_id": organization_id,
            "months": months,
            "display_mode": display_mode,
            "verification_level": verification_level,
            "submitter_id": submitter_id,
            "submitter_role": submitter_role,
        },
    )
    consent_source = "MEMBER" if submitter_role == "MEMBER" else "MATCHMAKER_ATTESTED"
    if body.consent_from:
        await _insert_consent(
            db,
            news_id=news_id,
            user_id=from_user_id,
            role="FROM",
            scopes=body.consent_scopes,
            consent_version=body.consent_version,
            consent_source=consent_source,
        )
    if body.consent_to:
        await _insert_consent(
            db,
            news_id=news_id,
            user_id=to_user_id,
            role="TO",
            scopes=body.consent_scopes,
            consent_version=body.consent_version,
            consent_source=consent_source,
        )
    try:
        report_result = await db.execute(
            text(
                "INSERT INTO good_news_report "
                "(news_id, submitter_id, submitter_role, from_user_id, to_user_id, source_type, source_id, stage, "
                "partner_name, meet_duration_months, content, images_json, consent_snapshot_json, status, idempotency_key) "
                "VALUES (:news_id, :submitter_id, :submitter_role, :from_user_id, :to_user_id, :source_type, :source_id, "
                ":stage, :partner_name, :months, :content, :images, :consent, 'PENDING', :idempotency_key)"
            ),
            {
                "news_id": news_id,
                "submitter_id": submitter_id,
                "submitter_role": submitter_role,
                "from_user_id": from_user_id,
                "to_user_id": to_user_id,
                "source_type": source_type,
                "source_id": body.source_id,
                "stage": body.stage,
                "partner_name": to_name or None,
                "months": months,
                "content": body.content,
                "images": json.dumps(images, ensure_ascii=False),
                "consent": json.dumps(
                    {
                        "from": body.consent_from,
                        "to": body.consent_to,
                        "version": body.consent_version,
                        "scopes": body.consent_scopes,
                    },
                    ensure_ascii=False,
                ),
                "idempotency_key": idempotency_key,
            },
        )
    except IntegrityError:
        # 并发同键：回滚本次内容/元数据，返回已存在记录。
        await db.rollback()
        existing = (
            await db.execute(
                text(
                    "SELECT id, news_id FROM good_news_report "
                    "WHERE submitter_id = :submitter_id AND idempotency_key = :key LIMIT 1"
                ),
                {"submitter_id": submitter_id, "key": idempotency_key},
            )
        ).mappings().first()
        if existing and existing.get("news_id"):
            return await _get_item_for_submitter(db, int(existing["news_id"]), submitter_id), int(existing["id"])
        raise HTTPException(409, detail="该报喜已提交，正在处理")
    report_id = int(report_result.lastrowid or 0)
    await _audit(
        db,
        news_id=news_id,
        report_id=report_id,
        actor_user_id=submitter_id,
        actor_role=submitter_role,
        action="SUBMIT",
        after_status="PENDING",
    )
    await db.commit()
    row = await _fetch_row(db, news_id)
    if row is None:
        raise HTTPException(500, detail="喜讯创建后读取失败")
    return _item_from_row(row, public_view=False), report_id


async def _get_item_for_submitter(db: AsyncSession, news_id: int, submitter_id: int) -> GoodNewsItem:
    row = await _fetch_row(db, news_id)
    if row is None:
        raise HTTPException(404, detail="喜讯不存在")
    if _as_int(row.get("meta_submitted_by_user_id")) not in {None, submitter_id}:
        raise HTTPException(403, detail="无权查看该喜讯")
    return _item_from_row(row, public_view=False)


async def list_workspace(
    db: AsyncSession,
    *,
    actor: Any,
    stage: str | None,
    page: int,
    page_size: int,
) -> GoodNewsPage:
    if stage and stage not in GOOD_NEWS_STAGES:
        raise HTTPException(422, detail="不支持的喜讯阶段")
    conditions = ["c.tenant_id = 1", "c.domain = 'good_news'", "c.status = 1"]
    params: dict[str, Any] = {
        "actor_id": actor.user_id,
        "limit": page_size,
        "offset": (page - 1) * page_size,
    }
    if getattr(actor, "scope", "SELF") == "ORGANIZATION":
        if not getattr(actor, "organization_id", None):
            raise HTTPException(403, detail="超级服务红娘尚未绑定有效组织范围")
        conditions.append("m.organization_id = :organization_id")
        params["organization_id"] = actor.organization_id
    else:
        conditions.append("m.matchmaker_id = :matchmaker_id")
        params["matchmaker_id"] = actor.user_id
    conditions.append(
        "((m.review_status = 'APPROVED' AND m.public_status = 'PUBLISHED') "
        "OR m.submitted_by_user_id = :actor_id)"
    )
    if stage:
        conditions.append("m.stage = :stage")
        params["stage"] = stage
    where = " AND ".join(conditions)
    total = int(
        (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM admin_content_item c "
                    "JOIN good_news_meta m ON m.news_id = c.id "
                    f"WHERE {where}"
                ),
                params,
            )
        ).scalar()
        or 0
    )
    rows = (
        await db.execute(
            text(
                f"SELECT {_META_SELECT} FROM admin_content_item c "
                "JOIN good_news_meta m ON m.news_id = c.id "
                f"WHERE {where} "
                "ORDER BY COALESCE(m.published_at, c.created_at) DESC, c.id DESC "
                "LIMIT :limit OFFSET :offset"
            ),
            params,
        )
    ).mappings().all()
    ids = [int(row["id"]) for row in rows]
    blessed = await _blessed_ids(db, actor.user_id, ids)
    return GoodNewsPage(
        items=[_item_from_row(row, public_view=False, blessed_by_me=int(row["id"]) in blessed) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
    )


async def list_pennants(
    db: AsyncSession,
    *,
    actor: Any,
    page: int,
    page_size: int,
) -> GoodNewsPennantPage:
    conditions = ["tenant_id = 1", "domain = 'good_news_pennant'", "status = 1"]
    params: dict[str, Any] = {"limit": page_size, "offset": (page - 1) * page_size}
    if getattr(actor, "scope", "SELF") != "ORGANIZATION":
        conditions.append("JSON_UNQUOTE(JSON_EXTRACT(extra_json, '$.target')) = :target")
        params["target"] = actor.display_name
    where = " AND ".join(conditions)
    total = int(
        (
            await db.execute(
                text(f"SELECT COUNT(*) FROM admin_content_item WHERE {where}"),
                params,
            )
        ).scalar()
        or 0
    )
    rows = (
        await db.execute(
            text(
                "SELECT id, image_url, extra_json, created_at FROM admin_content_item "
                f"WHERE {where} ORDER BY sort DESC, id DESC LIMIT :limit OFFSET :offset"
            ),
            params,
        )
    ).mappings().all()
    items: list[GoodNewsPennantItem] = []
    for row in rows:
        extra = _parse_extra(row.get("extra_json"))
        items.append(
            GoodNewsPennantItem(
                id=int(row["id"]),
                maker=extra.get("maker"),
                giver=extra.get("giver"),
                target=extra.get("target"),
                words=extra.get("words"),
                image_url=row.get("image_url"),
                created_at=row.get("created_at"),
            )
        )
    return GoodNewsPennantPage(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
    )


async def list_my_reports(
    db: AsyncSession,
    *,
    submitter_id: int,
    page: int,
    page_size: int,
) -> GoodNewsReportPage:
    params: dict[str, Any] = {
        "submitter_id": submitter_id,
        "limit": page_size,
        "offset": (page - 1) * page_size,
    }
    total = int(
        (
            await db.execute(
                text("SELECT COUNT(*) FROM good_news_report WHERE submitter_id = :submitter_id"),
                params,
            )
        ).scalar()
        or 0
    )
    rows = (
        await db.execute(
            text(
                "SELECT * FROM good_news_report WHERE submitter_id = :submitter_id "
                "ORDER BY created_at DESC, id DESC LIMIT :limit OFFSET :offset"
            ),
            params,
        )
    ).mappings().all()
    return GoodNewsReportPage(
        items=[_report_item(row) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
    )


async def get_my_report(db: AsyncSession, *, report_id: int, submitter_id: int) -> GoodNewsReportItem:
    row = (
        await db.execute(
            text("SELECT * FROM good_news_report WHERE id = :id AND submitter_id = :submitter_id"),
            {"id": report_id, "submitter_id": submitter_id},
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(404, detail="报喜记录不存在")
    return _report_item(row)


def _report_item(row: Any) -> GoodNewsReportItem:
    images: list[str] = []
    raw = row.get("images_json")
    if isinstance(raw, list):
        images = [str(item) for item in raw if str(item).strip()]
    elif isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                images = [str(item) for item in parsed if str(item).strip()]
        except (TypeError, ValueError):
            images = []
    return GoodNewsReportItem(
        id=int(row["id"]),
        news_id=_as_int(row.get("news_id")),
        stage=str(row.get("stage") or ""),
        partner_name=row.get("partner_name"),
        meet_duration_months=_as_int(row.get("meet_duration_months")),
        content=str(row.get("content") or ""),
        images=images,
        status=str(row.get("status") or "PENDING"),
        reject_reason=row.get("reject_reason"),
        submitter_role=str(row.get("submitter_role") or "MEMBER"),
        created_at=row.get("created_at"),
        reviewed_at=row.get("reviewed_at"),
    )


async def admin_list(
    db: AsyncSession,
    *,
    page: int,
    page_size: int,
    keyword: str | None = None,
    status: int | None = None,
    stage: str | None = None,
    review_status: str | None = None,
    public_status: str | None = None,
    matchmaker_id: int | None = None,
) -> ContentItemPage:
    conditions = ["c.tenant_id = 1", "c.domain = 'good_news'"]
    params: dict[str, Any] = {"limit": page_size, "offset": (page - 1) * page_size}
    if keyword:
        conditions.append("(c.title LIKE :kw OR c.subtitle LIKE :kw OR c.extra_json LIKE :kw)")
        params["kw"] = f"%{keyword}%"
    if status in (1, 2):
        conditions.append("c.status = :status")
        params["status"] = status
    if stage:
        conditions.append("m.stage = :stage")
        params["stage"] = stage
    if review_status:
        conditions.append("m.review_status = :review_status")
        params["review_status"] = review_status
    if public_status:
        conditions.append("m.public_status = :public_status")
        params["public_status"] = public_status
    if matchmaker_id:
        conditions.append("m.matchmaker_id = :matchmaker_id")
        params["matchmaker_id"] = matchmaker_id
    where = " AND ".join(conditions)
    total = int(
        (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM admin_content_item c "
                    "LEFT JOIN good_news_meta m ON m.news_id = c.id "
                    f"WHERE {where}"
                ),
                params,
            )
        ).scalar()
        or 0
    )
    rows = (
        await db.execute(
            text(
                "SELECT c.*, m.stage AS meta_stage, m.review_status AS meta_review_status, "
                "m.public_status AS meta_public_status, m.display_mode AS meta_display_mode, "
                "m.verification_level AS meta_verification_level, m.blessing_count AS meta_blessing_count, "
                "m.matchmaker_id AS meta_matchmaker_id, m.from_user_id AS meta_from_user_id, "
                "m.to_user_id AS meta_to_user_id, m.reject_reason AS meta_reject_reason, "
                "m.published_at AS meta_published_at, "
                "EXISTS (SELECT 1 FROM good_news_consent cf WHERE cf.news_id = c.id AND cf.role = 'FROM' AND cf.revoked_at IS NULL) AS consent_from, "
                "EXISTS (SELECT 1 FROM good_news_consent ct WHERE ct.news_id = c.id AND ct.role = 'TO' AND ct.revoked_at IS NULL) AS consent_to "
                "FROM admin_content_item c "
                "LEFT JOIN good_news_meta m ON m.news_id = c.id "
                f"WHERE {where} ORDER BY c.sort DESC, c.id DESC LIMIT :limit OFFSET :offset"
            ),
            params,
        )
    ).mappings().all()
    return ContentItemPage(
        total=total,
        page=page,
        page_size=page_size,
        items=[_admin_item(row) for row in rows],
    )


async def admin_get(db: AsyncSession, news_id: int) -> ContentItem:
    await _ensure_meta(db, news_id)
    row = (
        await db.execute(
            text(
                "SELECT c.*, m.stage AS meta_stage, m.review_status AS meta_review_status, "
                "m.public_status AS meta_public_status, m.display_mode AS meta_display_mode, "
                "m.verification_level AS meta_verification_level, m.blessing_count AS meta_blessing_count, "
                "m.matchmaker_id AS meta_matchmaker_id, m.from_user_id AS meta_from_user_id, "
                "m.to_user_id AS meta_to_user_id, m.reject_reason AS meta_reject_reason, "
                "m.published_at AS meta_published_at, "
                "EXISTS (SELECT 1 FROM good_news_consent cf WHERE cf.news_id = c.id AND cf.role = 'FROM' AND cf.revoked_at IS NULL) AS consent_from, "
                "EXISTS (SELECT 1 FROM good_news_consent ct WHERE ct.news_id = c.id AND ct.role = 'TO' AND ct.revoked_at IS NULL) AS consent_to "
                "FROM admin_content_item c LEFT JOIN good_news_meta m ON m.news_id = c.id "
                "WHERE c.id = :id AND c.domain = 'good_news' AND c.tenant_id = 1"
            ),
            {"id": news_id},
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(404, detail="喜讯不存在")
    item = _admin_item(row)
    return ContentItem(
        id=int(item["id"]),
        domain=str(item["domain"]),
        title=str(item["title"]),
        subtitle=item["subtitle"],
        image_url=item["image_url"],
        amount=item["amount"],
        status=int(item["status"]),
        sort=int(item["sort"]),
        extra=item["extra"],
        created_at=item["created_at"],
        updated_at=item["updated_at"],
    )


def _admin_item(row: Any) -> dict[str, Any]:
    extra = _parse_extra(row.get("extra_json"))
    consent_from = bool(row.get("consent_from"))
    consent_to = bool(row.get("consent_to"))
    extra.update(
        {
            "stage": row.get("meta_stage") or extra.get("stage") or extra.get("status") or "",
            "review_status": row.get("meta_review_status") or "PENDING",
            "public_status": row.get("meta_public_status") or "DRAFT",
            "display_mode": row.get("meta_display_mode") or "MASKED",
            "verification_level": row.get("meta_verification_level") or "SELF_REPORTED",
            "blessing_count": int(row.get("meta_blessing_count") or 0),
            "matchmaker_id": _as_int(row.get("meta_matchmaker_id")),
            "from_user_id": _as_int(row.get("meta_from_user_id")),
            "to_user_id": _as_int(row.get("meta_to_user_id")),
            "reject_reason": row.get("meta_reject_reason"),
            "published_at": _iso(row.get("meta_published_at")),
            "consent_from": consent_from,
            "consent_to": consent_to,
            "consent_complete": consent_from and consent_to,
        }
    )
    created_at = row.get("created_at")
    updated_at = row.get("updated_at")
    return {
        "id": int(row["id"]),
        "domain": str(row["domain"]),
        "title": str(row.get("title") or ""),
        "subtitle": row.get("subtitle"),
        "image_url": row.get("image_url"),
        "amount": float(row["amount"]) if row.get("amount") is not None else None,
        "status": int(row.get("status") or 1),
        "sort": int(row.get("sort") or 0),
        "extra": extra,
        "created_at": _iso(created_at),
        "updated_at": _iso(updated_at),
    }


async def review(db: AsyncSession, *, news_id: int, admin_account_id: int, body: GoodNewsReviewRequest) -> GoodNewsItem:
    await _ensure_meta(db, news_id)
    row = await _fetch_row(db, news_id)
    if row is None:
        raise HTTPException(404, detail="喜讯不存在")
    current_review = str(row.get("meta_review_status") or "PENDING")
    current_public = str(row.get("meta_public_status") or "DRAFT")
    if body.action == "APPROVE":
        if current_review != "PENDING":
            raise HTTPException(409, detail="只有待审喜讯可以通过")
        new_review, new_public = "APPROVED", "DRAFT"
    elif body.action == "REJECT":
        if current_review != "PENDING":
            raise HTTPException(409, detail="只有待审喜讯可以驳回")
        new_review, new_public = "REJECTED", "DRAFT"
    else:
        if current_public != "PUBLISHED":
            raise HTTPException(409, detail="只有已发布喜讯可以下架")
        new_review, new_public = current_review, "UNPUBLISHED"
    await db.execute(
        text(
            "UPDATE good_news_meta SET review_status = :review_status, public_status = :public_status, "
            "reviewed_by = :reviewed_by, reviewed_at = NOW(), reject_reason = :reason WHERE news_id = :news_id"
        ),
        {
            "review_status": new_review,
            "public_status": new_public,
            "reviewed_by": admin_account_id,
            "reason": body.reason,
            "news_id": news_id,
        },
    )
    if body.action in {"APPROVE", "REJECT"}:
        await db.execute(
            text(
                "UPDATE good_news_report SET status = :status, reject_reason = :reason, reviewed_at = NOW() "
                "WHERE news_id = :news_id"
            ),
            {"status": new_review, "reason": body.reason, "news_id": news_id},
        )
    await _audit(
        db,
        news_id=news_id,
        report_id=None,
        actor_user_id=admin_account_id,
        actor_role="ADMIN",
        action=f"REVIEW_{body.action}",
        reason=body.reason,
        before_status=f"{current_review}/{current_public}",
        after_status=f"{new_review}/{new_public}",
    )
    if body.action == "APPROVE":
        await _notify_submitter(db, news_id, "喜讯审核通过", "你的喜讯已通过审核，待发布后即可公开展示。")
    elif body.action == "REJECT":
        await _notify_submitter(db, news_id, "喜讯审核未通过", body.reason or "你的喜讯未通过审核。")
    await db.commit()
    fresh = await _fetch_row(db, news_id)
    if fresh is None:
        raise HTTPException(500, detail="审核后读取失败")
    return _item_from_row(fresh, public_view=False)


async def publish(db: AsyncSession, *, news_id: int, admin_account_id: int) -> GoodNewsItem:
    await _ensure_meta(db, news_id)
    row = await _fetch_row(db, news_id)
    if row is None:
        raise HTTPException(404, detail="喜讯不存在")
    if str(row.get("meta_review_status") or "") != "APPROVED":
        raise HTTPException(409, detail="只有审核通过的喜讯可以发布")
    if int(row.get("status") or 0) != 1:
        raise HTTPException(409, detail="喜讯内容已停用，不能发布")
    if not await _consent_complete(db, news_id):
        raise HTTPException(409, detail="双方同意记录不完整，不能发布")
    extra = _parse_extra(row.get("extra_json"))
    scope_problem = await _consent_scope_problem(
        db,
        news_id,
        str(row.get("meta_display_mode") or "MASKED"),
        bool(_parse_images(extra)),
    )
    if scope_problem:
        raise HTTPException(409, detail=scope_problem)
    await db.execute(
        text(
            "UPDATE good_news_meta SET public_status = 'PUBLISHED', "
            "published_at = COALESCE(published_at, NOW()) WHERE news_id = :news_id"
        ),
        {"news_id": news_id},
    )
    await _audit(
        db,
        news_id=news_id,
        report_id=None,
        actor_user_id=admin_account_id,
        actor_role="ADMIN",
        action="PUBLISH",
        after_status="PUBLISHED",
    )
    await db.commit()
    fresh = await _fetch_row(db, news_id)
    if fresh is None:
        raise HTTPException(500, detail="发布后读取失败")
    return _item_from_row(fresh, public_view=False)


async def unpublish(db: AsyncSession, *, news_id: int, admin_account_id: int, reason: str | None = None) -> GoodNewsItem:
    await _ensure_meta(db, news_id)
    row = await _fetch_row(db, news_id)
    if row is None:
        raise HTTPException(404, detail="喜讯不存在")
    if str(row.get("meta_public_status") or "") != "PUBLISHED":
        raise HTTPException(409, detail="只有已发布喜讯可以下架")
    await db.execute(
        text("UPDATE good_news_meta SET public_status = 'UNPUBLISHED' WHERE news_id = :news_id"),
        {"news_id": news_id},
    )
    await _audit(
        db,
        news_id=news_id,
        report_id=None,
        actor_user_id=admin_account_id,
        actor_role="ADMIN",
        action="UNPUBLISH",
        reason=reason,
        after_status="UNPUBLISHED",
    )
    await db.commit()
    fresh = await _fetch_row(db, news_id)
    if fresh is None:
        raise HTTPException(500, detail="下架后读取失败")
    return _item_from_row(fresh, public_view=False)


async def list_consents(db: AsyncSession, news_id: int) -> GoodNewsConsentPage:
    await _ensure_meta(db, news_id)
    rows = (
        await db.execute(
            text("SELECT * FROM good_news_consent WHERE news_id = :news_id ORDER BY role, id"),
            {"news_id": news_id},
        )
    ).mappings().all()
    items: list[GoodNewsConsentItem] = []
    for row in rows:
        scopes: list[str] = []
        raw = row.get("scopes_json")
        if isinstance(raw, list):
            scopes = [str(item) for item in raw]
        elif isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    scopes = [str(item) for item in parsed]
            except (TypeError, ValueError):
                scopes = []
        items.append(
            GoodNewsConsentItem(
                id=int(row["id"]),
                news_id=int(row["news_id"]),
                user_id=_as_int(row.get("user_id")),
                role=str(row["role"]),
                scopes=scopes,
                consent_version=str(row.get("consent_version") or ""),
                consent_source=str(row.get("consent_source") or "MEMBER"),
                consented_at=row.get("consented_at"),
                revoked_at=row.get("revoked_at"),
            )
        )
    return GoodNewsConsentPage(items=items)


async def record_consent(
    db: AsyncSession,
    *,
    news_id: int,
    admin_account_id: int,
    role: str,
    user_id: int | None,
    scopes: list[str],
    consent_version: str,
    source: str,
) -> GoodNewsConsentPage:
    await _ensure_meta(db, news_id)
    await _insert_consent(
        db,
        news_id=news_id,
        user_id=user_id,
        role=role,
        scopes=scopes,
        consent_version=consent_version,
        consent_source=source,
    )
    await _audit(
        db,
        news_id=news_id,
        report_id=None,
        actor_user_id=admin_account_id,
        actor_role="ADMIN",
        action="CONSENT",
        after_status=role,
    )
    await db.commit()
    return await list_consents(db, news_id)


async def revoke_consent(db: AsyncSession, *, news_id: int, user_id: int, role: str) -> None:
    row = (
        await db.execute(
            text("SELECT id, user_id FROM good_news_consent WHERE news_id = :news_id AND role = :role"),
            {"news_id": news_id, "role": role},
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(404, detail="同意记录不存在")
    consent_user = _as_int(row.get("user_id"))
    if consent_user is not None and consent_user != user_id:
        raise HTTPException(403, detail="无权撤回该同意")
    if consent_user is None:
        meta = await _fetch_row(db, news_id)
        if meta is None or user_id not in {_as_int(meta.get("meta_from_user_id")), _as_int(meta.get("meta_to_user_id"))}:
            raise HTTPException(403, detail="无权撤回该同意")
    await db.execute(
        text("UPDATE good_news_consent SET revoked_at = NOW() WHERE news_id = :news_id AND role = :role"),
        {"news_id": news_id, "role": role},
    )
    await db.execute(
        text("UPDATE good_news_meta SET public_status = 'UNPUBLISHED' WHERE news_id = :news_id"),
        {"news_id": news_id},
    )
    await _audit(
        db,
        news_id=news_id,
        report_id=None,
        actor_user_id=user_id,
        actor_role="MEMBER",
        action="REVOKE",
        reason=role,
        after_status="UNPUBLISHED",
    )
    await db.commit()


async def upload_photo(file: UploadFile, *, user_id: int) -> GoodNewsPhotoResponse:
    data = await _read_limited(file, IMAGE_MAX_BYTES)
    try:
        with Image.open(BytesIO(data)) as source:
            if source.format not in {"JPEG", "PNG", "WEBP"}:
                raise HTTPException(415, detail="仅支持 JPG、PNG 或 WEBP 图片")
            if source.width * source.height > IMAGE_MAX_PIXELS:
                raise HTTPException(413, detail="图片像素不能超过 2500 万")
            source.verify()
        with Image.open(BytesIO(data)) as source:
            image = ImageOps.exif_transpose(source)
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            output = BytesIO()
            image.save(output, format="WEBP", quality=85, method=6)
            payload = output.getvalue()
    except DecompressionBombError as exc:
        raise HTTPException(413, detail="图片像素过大") from exc
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(415, detail="图片内容无法识别") from exc
    directory = Path(settings.upload_dir) / "good_news" / str(user_id)
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid4().hex}.webp"
    (directory / filename).write_bytes(payload)
    return GoodNewsPhotoResponse(
        url=f"/storage/uploads/good_news/{user_id}/{filename}",
        content_type="image/webp",
        size=len(payload),
    )


async def _read_limited(file: UploadFile, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(413, detail=f"文件大小不能超过 {limit // 1024 // 1024}MB")
        chunks.append(chunk)
    if total == 0:
        raise HTTPException(422, detail="文件内容为空")
    return b"".join(chunks)

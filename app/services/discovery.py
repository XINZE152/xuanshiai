"""Recommendation, card browsing and interaction services."""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from fastapi import HTTPException, Response
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.profile_tags import (
    CUSTOM_TAG_CATEGORY_KEY,
    CUSTOM_TAG_CATEGORY_MAP_KEY,
    DISCOVERY_CITY_OPTIONS,
    display_personal_tags,
    normalize_custom_tag_categories,
    personal_tags,
)
from app.core.redis import consume_daily, consume_daily_unique, get_daily_used, mark_daily_member, refund_daily
from app.services.membership import (
    active_membership_exists_for_column_sql,
    has_active_membership,
)
from app.schemas.discovery import (
    ApplicationCreateRequest,
    ApplicationPage,
    ApplicationRejectRequest,
    ApplicationResponse,
    BrowseHistoryItem,
    BrowseHistoryPage,
    DiscoveryCard,
    DiscoveryFilters,
    DiscoveryPage,
    DiscoverySearch,
    FavoritePage,
    FavoriteReceivedItem,
    FavoriteReceivedPage,
    FavoriteResponse,
    FilterOptionsResponse,
    DiscoveryCardPreviewResponse,
    PublicProfileResponse,
    RelationUserSummary,
    SavedFilterResponse,
    SuperLikeItem,
    SuperLikePage,
    SuperLikeResponse,
    VisitorPage,
)
from app.services.candidate_query import (
    SORT_VERSION,
    CandidatePage,
    CandidateQueryService,
    CandidateQuerySnapshot,
    InvalidCandidateCursor,
    build_query_fingerprint,
)
from app.services.candidate_visibility import (
    CandidateVisibilityService,
    SqlPredicate,
    ViewerContext,
    VisibilityScene,
)
from app.services.notifications import emit_notification
from app.services.profile import _calculate_age, _get_media, _json_dict, _json_list, _json_object, get_profile
from app.services.quotas import consume_extra, resolve_apply_limit, resolve_browse_limit, resolve_superlike_limit
from app.services.regions import region_display
from app.services.admin_config import get_runtime_value
from app.services.restrictions import ensure_user_allowed
from app.services.runtime_config import (
    match_line_permissions,
    platform_browse_open,
    platform_maintenance_message,
)

logger = logging.getLogger(__name__)
candidate_visibility_service = CandidateVisibilityService()
candidate_query_service = CandidateQueryService(secret_key=settings.secret_key)

# 旧 match_score/match_reason 的算法版本（统一方案 §9.1/§10.4）：语义恒为
# legacy-rule-v1；新兼容度（compatibility-rule-v2）只写 ai_compatibility_snapshot，
# 不触碰旧字段或推荐排序。
LEGACY_MATCH_ALGORITHM_VERSION = "legacy-rule-v1"


CARD_SELECT = """
    SELECT u.id AS user_id, u.nickname, u.avatar, u.gender, u.birthday, u.is_married,
           u.is_single_pledge, p.height, p.education_level, p.occupation, p.income,
           p.weight, p.zodiac,
           p.hometown_province_code, p.hometown_city_code, p.hometown_district_code,
           p.residence_province_code, p.residence_city_code, p.residence_district_code,
           (p.residence_city_code IS NOT NULL AND p.residence_city_code =
             (SELECT vp2.residence_city_code FROM user_profile vp2 WHERE vp2.user_id = :viewer_id)) AS same_city,
           p.mbti, p.interest_tags, p.personality_tags, p.tags,
           p.self_intro, p.qa_answers,
           cp.age_min AS candidate_age_min, cp.age_max AS candidate_age_max,
           cp.height_min AS candidate_height_min, cp.height_max AS candidate_height_max,
           cp.education_min AS candidate_education_min, cp.income_min AS candidate_income_min,
           cp.marriage_status AS candidate_marriage_status,
           CASE WHEN p.online_status = 2 THEN 2
                WHEN p.last_active_at IS NOT NULL AND p.last_active_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 90 SECOND) THEN 1
                ELSE 0 END AS online_status,
           p.last_active_at, COALESCE(c.score, 0) AS completion_score,
           COALESCE(ua.realname_status, 0) AS realname_status,
           COALESCE(pr.only_vip_can_see_detail, 0) AS only_vip_can_see_detail,
           COALESCE(pr.hide_school, 0) AS hide_school,
           COALESCE(pr.hide_company, 0) AS hide_company,
           COALESCE(pr.hide_distance, 0) AS hide_distance,
           COALESCE(pr.hide_online_status, 0) AS hide_online_status,
           COALESCE(pr.show_profile, 1) AS show_profile,
           COALESCE(pr.who_can_see_me, 1) AS who_can_see_me,
           COALESCE(pr.match_status, 1) AS match_status,
           """ + active_membership_exists_for_column_sql(
               membership_alias="m", user_id_column="u.id"
           ) + """ AS is_vip,
           EXISTS (SELECT 1 FROM user_boost b
                   WHERE b.target_user_id = u.id AND b.status = 1
                     AND (b.start_at IS NULL OR b.start_at <= UTC_TIMESTAMP())
                     AND (b.end_at IS NULL OR b.end_at > UTC_TIMESTAMP())) AS is_boosted,
           EXISTS (SELECT 1 FROM user_favorite f
                   WHERE f.user_id = :viewer_id AND f.target_user_id = u.id AND f.type = 2) AS is_favorite,
           -- 「喜欢」与「收藏」分表语义：type=1 是红心/喜欢（与 /relations/likes 同源），
           -- type=2 是旧收藏，仅保留兼容。走 uk_pair_type(user_id,target_user_id,type) 索引。
           EXISTS (SELECT 1 FROM user_favorite f
                   WHERE f.user_id = :viewer_id AND f.target_user_id = u.id AND f.type = 1) AS is_liked
"""


CARD_FROM = """
    FROM users u
    LEFT JOIN user_profile p ON p.user_id = u.id
    LEFT JOIN user_profile_completion c ON c.user_id = u.id
    LEFT JOIN user_auth ua ON ua.user_id = u.id
    LEFT JOIN user_privacy pr ON pr.user_id = u.id
    LEFT JOIN user_partner_preference vp ON vp.user_id = :viewer_id
    LEFT JOIN user_partner_preference cp ON cp.user_id = u.id
"""


async def _viewer_context(db: AsyncSession, user_id: int) -> dict[str, Any]:
    result = await db.execute(
        text("""SELECT u.gender, u.birthday, COALESCE(c.score, 0) AS completion_score,
                      u.phone, p.height, p.education_level, p.income, p.mbti, p.interest_tags,
               p.personality_tags, p.tags, p.residence_city_code,
               COALESCE(ua.realname_status, 0) AS realname_status,
                      pref.age_min, pref.age_max, pref.height_min, pref.height_max,
                       pref.education_min, pref.income_min, pref.marriage_status,
                       pref.preferred_province_code, pref.preferred_city_codes
               FROM users u LEFT JOIN user_profile p ON p.user_id = u.id
               LEFT JOIN user_profile_completion c ON c.user_id = u.id
               LEFT JOIN user_auth ua ON ua.user_id = u.id
               LEFT JOIN user_partner_preference pref ON pref.user_id = u.id
               WHERE u.id = :user_id"""),
        {"user_id": user_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(404, detail="用户不存在")
    return dict(row)


async def _is_vip(db: AsyncSession, user_id: int) -> bool:
    return await has_active_membership(db, user_id)


def _skip_discovery_gates() -> bool:
    """测试环境专用：是否放开发现链路的异性过滤与资料完整度门禁。"""
    return settings.discovery_skip_gates_enabled


def _visibility_predicate(
    viewer_id: int,
    viewer: dict[str, Any],
    viewer_is_vip: bool,
    scene: VisibilityScene,
    *,
    candidate_alias: str = "u",
    privacy_alias: str = "pr",
    completion_alias: str = "c",
    skip_completion_gate: bool = False,
) -> SqlPredicate:
    return candidate_visibility_service.predicate(
        ViewerContext(
            user_id=viewer_id,
            realname_status=int(viewer.get("realname_status") or 0),
            is_vip=viewer_is_vip,
        ),
        scene,
        candidate_alias=candidate_alias,
        privacy_alias=privacy_alias,
        completion_alias=completion_alias,
        skip_completion_gate=skip_completion_gate,
    )


async def _scene_visibility(
    db: AsyncSession,
    viewer_id: int,
    scene: VisibilityScene,
    *,
    candidate_alias: str = "u",
    privacy_alias: str = "pr",
    completion_alias: str = "c",
) -> tuple[dict[str, Any], bool, SqlPredicate]:
    viewer = await _viewer_context(db, viewer_id)
    viewer_is_vip = await _is_vip(db, viewer_id)
    return (
        viewer,
        viewer_is_vip,
        _visibility_predicate(
            viewer_id,
            viewer,
            viewer_is_vip,
            scene,
            candidate_alias=candidate_alias,
            privacy_alias=privacy_alias,
            completion_alias=completion_alias,
        ),
    )


def _json_city_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return [str(item) for item in parsed] if isinstance(parsed, list) else []
    return []


def _all_tags(row: dict[str, Any]) -> set[str]:
    tags = set(_json_list(row.get("interest_tags"))) | set(_json_list(row.get("personality_tags")))
    for values in _json_dict(row.get("tags")).values():
        tags.update(values)
    stored_custom = _stored_custom_tags(row)
    return set(personal_tags(list(tags), stored_custom))


def _stored_custom_tags(row: dict[str, Any]) -> list[str]:
    stored = _json_object(row.get("tags"))
    values = stored.get(CUSTOM_TAG_CATEGORY_KEY, [])
    mapping = normalize_custom_tag_categories(
        [str(item) for item in values] if isinstance(values, list) else [],
        stored.get(CUSTOM_TAG_CATEGORY_MAP_KEY),
    )
    return list(mapping)


def _candidate_score(viewer: dict[str, Any], candidate: dict[str, Any]) -> tuple[float, str]:
    reasons: list[str] = []
    points = 0.0
    candidate_age = _calculate_age(candidate["birthday"]) if candidate.get("birthday") else None
    viewer_age = _calculate_age(viewer["birthday"]) if viewer.get("birthday") else None
    if viewer.get("age_min") and candidate_age and viewer["age_min"] <= candidate_age <= (viewer.get("age_max") or 100):
        points += 30
        reasons.append("符合年龄偏好")
    elif candidate_age and viewer_age:
        age_gap = abs(candidate_age - viewer_age)
        if age_gap <= 3:
            points += 30
            reasons.append("年龄相仿")
        elif age_gap <= 6:
            points += 20
            reasons.append("年龄较为接近")
        elif age_gap <= 10:
            points += 10
    if viewer.get("residence_city_code") and viewer["residence_city_code"] == candidate.get("residence_city_code"):
        points += 15
        reasons.append("同城")
    viewer_tags = _all_tags(viewer)
    candidate_tags = _all_tags(candidate)
    overlap = viewer_tags & candidate_tags
    if overlap:
        points += min(35, len(overlap) * 7)
        reasons.append("共同兴趣：" + "、".join(sorted(overlap)[:3]))
    viewer_mbti = viewer.get("mbti")
    candidate_mbti = candidate.get("mbti")
    if viewer_mbti and candidate_mbti:
        complementary = sum(left != right for left, right in zip(viewer_mbti[:3], candidate_mbti[:3]))
        points += complementary * 3
        if complementary >= 2:
            reasons.append("MBTI互补")
    if candidate.get("last_active_at") and (datetime.now(UTC).replace(tzinfo=None) - candidate["last_active_at"]) <= timedelta(days=7):
        points += 10
        reasons.append("近期活跃")
    if candidate.get("realname_status") == 2:
        points += 5
        reasons.append("已实名认证")
    if candidate.get("is_single_pledge") == 1:
        points += 5
        reasons.append("已签署单身承诺")
    return round(min(100.0, points), 2), "、".join(reasons[:3]) or "资料匹配"


def _card_qa_answers(value: Any) -> list[dict[str, str]]:
    """卡片只下发已作答的公开问答，最多 3 条；格式与详情页 qa_answers 一致。"""
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        answer = str(item.get("answer") or "").strip()
        if answer == "":
            continue
        question = str(item.get("question") or "").strip()
        result.append({"question": question or "关于我", "answer": answer})
        if len(result) >= 3:
            break
    return result


def _card(row: dict[str, Any], score: float, reason: str, detail_locked: bool = False) -> DiscoveryCard:
    certification_tags: list[str] = []
    if row.get("realname_status") == 2:
        certification_tags.append("实名认证")
    if row.get("is_single_pledge") == 1:
        certification_tags.append("单身承诺")
    return DiscoveryCard(
        user_id=int(row["user_id"]),
        nickname=row.get("nickname"),
        avatar=row.get("avatar"),
        age=_calculate_age(row["birthday"]) if row.get("birthday") else None,
        height=row.get("height") if not detail_locked else None,
        education_level=row.get("education_level") if not detail_locked and not row.get("hide_school") else None,
        occupation=row.get("occupation") if not detail_locked and not row.get("hide_company") else None,
        city_code=row.get("residence_city_code") if not detail_locked else None,
        income=float(row["income"]) if row.get("income") is not None and not detail_locked else None,
        distance_km=(0.0 if row.get("same_city") else None) if not detail_locked and not row.get("hide_distance") else None,
        is_married=row.get("is_married") if not detail_locked else None,
        online_status=0 if row.get("hide_online_status") else int(row.get("online_status") or 0),
        mbti=row.get("mbti") if not detail_locked else None,
        weight=row.get("weight") if not detail_locked else None,
        hometown_display=region_display(
            row.get("hometown_province_code"),
            row.get("hometown_city_code"),
            row.get("hometown_district_code"),
        )
        if not detail_locked
        else None,
        residence_display=region_display(
            row.get("residence_province_code"),
            row.get("residence_city_code"),
            row.get("residence_district_code"),
        )
        if not detail_locked
        else None,
        zodiac=(row.get("zodiac") or None) if not detail_locked else None,
        self_intro=(row.get("self_intro") or None) if not detail_locked else None,
        qa_answers=_card_qa_answers(row.get("qa_answers")) if not detail_locked else [],
        personal_tags=display_personal_tags(_json_list(row.get("interest_tags")) + _json_list(row.get("personality_tags")), _stored_custom_tags(row)) if not detail_locked else [],
        interest_tags=personal_tags(_json_list(row.get("interest_tags")), _stored_custom_tags(row))[:5] if not detail_locked else [],
        certification_tags=certification_tags,
        match_score=score,
        match_reason=reason,
        algorithm_version=LEGACY_MATCH_ALGORITHM_VERSION,
        match_score_source=LEGACY_MATCH_ALGORITHM_VERSION,
        is_favorite=bool(row.get("is_favorite")),
        is_liked=bool(row.get("is_liked")),
        is_vip=bool(row.get("is_vip")),
        is_pure_free=not bool(row.get("is_vip")) and not bool(row.get("is_boosted")),
        is_boosted=bool(row.get("is_boosted")),
        detail_locked=detail_locked,
    )


def _escape_like(value: str) -> str:
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def _filter_sql(filters: DiscoveryFilters, params: dict[str, Any]) -> list[str]:
    clauses: list[str] = []
    if filters.gender:
        clauses.append("u.gender = :filter_gender")
        params["filter_gender"] = filters.gender
    if filters.age_min:
        clauses.append(f"u.birthday <= DATE_SUB(CURDATE(), INTERVAL {filters.age_min} YEAR)")
    if filters.age_max:
        clauses.append(f"u.birthday >= DATE_SUB(CURDATE(), INTERVAL {filters.age_max + 1} YEAR)")
    for field, column in (("province_code", "p.residence_province_code"), ("city_code", "p.residence_city_code"), ("district_code", "p.residence_district_code")):
        value = getattr(filters, field)
        if value:
            clauses.append(f"{column} = :filter_{field}")
            params[f"filter_{field}"] = value
    if filters.marriage_status:
        clauses.append("u.is_married = :filter_marriage")
        params["filter_marriage"] = filters.marriage_status
    if filters.education_min:
        clauses.append("p.education_level >= :filter_education")
        params["filter_education"] = filters.education_min
    if filters.height_min:
        clauses.append("p.height >= :filter_height_min")
        params["filter_height_min"] = filters.height_min
    if filters.height_max:
        clauses.append("p.height <= :filter_height_max")
        params["filter_height_max"] = filters.height_max
    if filters.income_min is not None:
        clauses.append("p.income >= :filter_income_min")
        params["filter_income_min"] = filters.income_min
    if filters.income_max is not None:
        clauses.append("p.income <= :filter_income_max")
        params["filter_income_max"] = filters.income_max
    if filters.pure_free:
        clauses.append(
            "NOT "
            + active_membership_exists_for_column_sql(
                membership_alias="m2", user_id_column="u.id"
            )
        )
        clauses.append("NOT EXISTS (SELECT 1 FROM user_boost b2 WHERE b2.target_user_id = u.id AND b2.status = 1 AND (b2.end_at IS NULL OR b2.end_at > UTC_TIMESTAMP()))")
    return clauses


def _candidate_snapshot(
    *,
    viewer_id: int,
    viewer: dict[str, Any],
    viewer_is_vip: bool,
    filters: DiscoveryFilters,
    plaza: bool,
    nickname: str | None,
    tag: str | None,
    respect_preferences: bool,
) -> CandidateQuerySnapshot:
    """Build one server-owned candidate SELECT/count pair.

    Both statements consume the same visibility and filter clauses.  The
    query fingerprint deliberately excludes the page and cursor so a cursor
    can advance through one logical result set without changing its identity.
    """
    scene = VisibilityScene.SEARCH if nickname or tag else VisibilityScene.DISCOVERY
    skip_gates = _skip_discovery_gates()
    visibility = _visibility_predicate(
        viewer_id,
        viewer,
        viewer_is_vip,
        scene,
        skip_completion_gate=skip_gates,
    )
    params: dict[str, Any] = {
        "viewer_id": viewer_id,
        **visibility.params,
    }
    clauses = [visibility.clause]
    if nickname:
        clauses.append("u.nickname LIKE CONCAT('%', :search_nickname, '%') ESCAPE '!' ")
        params["search_nickname"] = _escape_like(nickname)
    if tag:
        clauses.append(
            """(
            JSON_CONTAINS(p.interest_tags, JSON_QUOTE(:search_tag))
            OR JSON_CONTAINS(p.personality_tags, JSON_QUOTE(:search_tag))
            OR JSON_SEARCH(p.tags, 'one', :search_tag) IS NOT NULL
        )"""
        )
        params["search_tag"] = tag
    if not skip_gates and viewer.get("gender") in (1, 2):
        clauses.append("u.gender <> :opposite_gender")
        params["opposite_gender"] = viewer["gender"]
    if respect_preferences:
        clauses.extend(
            [
                "(vp.age_min IS NULL OR TIMESTAMPDIFF(YEAR, u.birthday, CURDATE()) >= vp.age_min)",
                "(vp.age_max IS NULL OR TIMESTAMPDIFF(YEAR, u.birthday, CURDATE()) <= vp.age_max)",
                "(vp.height_min IS NULL OR p.height >= vp.height_min)",
                "(vp.height_max IS NULL OR p.height <= vp.height_max)",
                "(vp.education_min IS NULL OR p.education_level >= vp.education_min)",
                "(vp.income_min IS NULL OR p.income >= vp.income_min)",
                "(vp.marriage_status IS NULL OR vp.marriage_status = 0 OR u.is_married = vp.marriage_status)",
                "(vp.preferred_province_code IS NULL OR p.residence_province_code = vp.preferred_province_code)",
                "(vp.preferred_city_codes IS NULL OR JSON_LENGTH(vp.preferred_city_codes) = 0 OR JSON_CONTAINS(vp.preferred_city_codes, JSON_QUOTE(p.residence_city_code)))",
            ]
        )
    if not plaza:
        clauses.extend(
            [
                "NOT EXISTS (SELECT 1 FROM user_swipe_record sw WHERE sw.user_id = :viewer_id AND sw.target_user_id = u.id AND sw.action = 2)",
            ]
        )
    clauses.extend(_filter_sql(filters, params))
    filter_facts = filters.model_dump(mode="json")
    filter_facts.pop("cursor", None)
    filter_facts.pop("page", None)
    viewer_predicate_facts: dict[str, Any] = {
        "gender": viewer.get("gender"),
    }
    if skip_gates:
        viewer_predicate_facts["gender"] = None
    if respect_preferences:
        viewer_predicate_facts["partner_preferences"] = {
            "age_min": viewer.get("age_min"),
            "age_max": viewer.get("age_max"),
            "height_min": viewer.get("height_min"),
            "height_max": viewer.get("height_max"),
            "education_min": viewer.get("education_min"),
            "income_min": viewer.get("income_min"),
            "marriage_status": viewer.get("marriage_status"),
            "preferred_province_code": viewer.get("preferred_province_code"),
            "preferred_city_codes": sorted(
                _json_city_list(viewer.get("preferred_city_codes"))
            ),
        }
    fingerprint_payload: dict[str, Any] = {
        "viewer_id": viewer_id,
        "viewer_realname_status": viewer.get("realname_status") or 0,
        "viewer_is_vip": viewer_is_vip,
        "viewer_predicate_facts": viewer_predicate_facts,
        "scene": scene.value,
        "plaza": plaza,
        "respect_preferences": respect_preferences,
        "nickname": nickname,
        "tag": tag,
        "filters": filter_facts,
        "policy_revision": visibility.policy_revision,
        "sort_version": SORT_VERSION,
    }
    if skip_gates:
        fingerprint_payload["skip_gates"] = True
    query_fingerprint = build_query_fingerprint(fingerprint_payload)
    return CandidateQuerySnapshot(
        select_sql=CARD_SELECT + CARD_FROM,
        count_sql="SELECT COUNT(DISTINCT u.id)" + CARD_FROM,
        where_sql=" AND ".join(clauses),
        params=params,
        query_fingerprint=query_fingerprint,
        page=filters.page,
    )


async def _candidate_query_page(
    db: AsyncSession,
    viewer_id: int,
    filters: DiscoveryFilters,
    *,
    plaza: bool,
    nickname: str | None = None,
    tag: str | None = None,
    respect_preferences: bool = True,
) -> tuple[dict[str, Any], bool, CandidatePage]:
    viewer = await _viewer_context(db, viewer_id)
    viewer_is_vip = await _is_vip(db, viewer_id)
    snapshot = _candidate_snapshot(
        viewer_id=viewer_id,
        viewer=viewer,
        viewer_is_vip=viewer_is_vip,
        filters=filters,
        plaza=plaza,
        nickname=nickname,
        tag=tag,
        respect_preferences=respect_preferences,
    )
    try:
        page = await candidate_query_service.fetch_page(
            db,
            snapshot,
            cursor=filters.cursor,
            page_size=filters.page_size,
        )
    except InvalidCandidateCursor as exc:
        raise HTTPException(400, detail="INVALID_CANDIDATE_CURSOR") from exc
    return viewer, viewer_is_vip, page


async def _fetch_rows(
    db: AsyncSession,
    viewer_id: int,
    filters: DiscoveryFilters,
    *,
    plaza: bool,
    nickname: str | None = None,
    tag: str | None = None,
    respect_preferences: bool = True,
) -> list[dict[str, Any]]:
    """Compatibility wrapper for callers that still need a candidate list."""
    _, _, page = await _candidate_query_page(
        db,
        viewer_id,
        filters,
        plaza=plaza,
        nickname=nickname,
        tag=tag,
        respect_preferences=respect_preferences,
    )
    return page.items


async def get_discovery_page(db: AsyncSession, viewer_id: int, filters: DiscoveryFilters, *, plaza: bool) -> DiscoveryPage:
    if not await platform_browse_open(db):
        raise HTTPException(403, detail=await platform_maintenance_message(db))
    viewer = await _viewer_context(db, viewer_id)
    if not _skip_discovery_gates() and viewer["completion_score"] < 100:
        raise HTTPException(403, detail="请先完善资料后再进入推荐")
    _, viewer_is_vip, candidate_page = await _candidate_query_page(
        db, viewer_id, filters, plaza=plaza
    )
    items = [
        _card(
            row,
            *_candidate_score(viewer, row),
            detail_locked=bool(row.get("only_vip_can_see_detail")) and not viewer_is_vip,
        )
        for row in candidate_page.items
    ]
    return DiscoveryPage(
        items=items,
        page=candidate_page.page,
        page_size=candidate_page.page_size,
        total=candidate_page.total,
        has_more=candidate_page.has_more,
        next_cursor=candidate_page.next_cursor,
        sort_version=candidate_page.sort_version,
        total_is_estimate=candidate_page.total_is_estimate,
    )


async def search_discovery(db: AsyncSession, viewer_id: int, query: DiscoverySearch) -> DiscoveryPage:
    if not await platform_browse_open(db):
        raise HTTPException(403, detail=await platform_maintenance_message(db))
    filters = DiscoveryFilters(
        cursor=query.cursor,
        page=query.page,
        page_size=query.page_size,
    )
    viewer = await _viewer_context(db, viewer_id)
    if not _skip_discovery_gates() and viewer["completion_score"] < 100:
        raise HTTPException(403, detail="请先完善资料后再搜索用户")
    _, viewer_is_vip, candidate_page = await _candidate_query_page(
        db,
        viewer_id,
        filters,
        plaza=True,
        nickname=query.nickname,
        tag=query.tag,
        respect_preferences=False,
    )
    items = [
        _card(
            row,
            *_candidate_score(viewer, row),
            detail_locked=bool(row.get("only_vip_can_see_detail")) and not viewer_is_vip,
        )
        for row in candidate_page.items
    ]
    return DiscoveryPage(
        items=items,
        page=candidate_page.page,
        page_size=candidate_page.page_size,
        total=candidate_page.total,
        has_more=candidate_page.has_more,
        next_cursor=candidate_page.next_cursor,
        sort_version=candidate_page.sort_version,
        total_is_estimate=candidate_page.total_is_estimate,
    )


async def get_filter_options() -> FilterOptionsResponse:
    # education_levels 必须与 user_profile.education_level 的存储域一致：
    # 1=高中及以下 … 5=博士（档案编辑写入域，utils 编号即 educationOptions
    # 数组下标；_AI_SYNC_EDU_MAP 目标域同）。此前 1=博士…5=高中 与存储域
    # 方向相反——education_min 的 SQL 语义是 `education_level >= :min`，
    # 反向字典会让"本科及以上"实际筛出"本科及以下"。
    return FilterOptionsResponse(
        genders=[{"value": 1, "label": "男"}, {"value": 2, "label": "女"}],
        marriage_statuses=[{"value": 1, "label": "未婚"}, {"value": 2, "label": "离异"}, {"value": 3, "label": "丧偶"}],
        education_levels=[
            {"value": 1, "label": "高中及以下"},
            {"value": 2, "label": "大专"},
            {"value": 3, "label": "本科"},
            {"value": 4, "label": "硕士"},
            {"value": 5, "label": "博士"},
        ],
        cities=sorted(DISCOVERY_CITY_OPTIONS),
    )


async def get_saved_filter(db: AsyncSession, user_id: int) -> SavedFilterResponse:
    result = await db.execute(
        text("SELECT filter_json FROM user_discovery_filter WHERE user_id = :user_id"),
        {"user_id": user_id},
    )
    raw = result.scalar()
    if not raw:
        return SavedFilterResponse(filters=None)
    try:
        filters = DiscoveryFilters.model_validate(json.loads(raw) if isinstance(raw, str) else raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(500, detail="已保存的筛选条件无效") from exc
    return SavedFilterResponse(filters=filters)


async def save_filter(db: AsyncSession, user_id: int, filters: DiscoveryFilters) -> SavedFilterResponse:
    await db.execute(
        text("""INSERT INTO user_discovery_filter (user_id, filter_json)
                  VALUES (:user_id, :filter_json)
                  ON DUPLICATE KEY UPDATE filter_json = VALUES(filter_json), updated_at = UTC_TIMESTAMP()"""),
        {
            "user_id": user_id,
            "filter_json": json.dumps(filters.model_dump(mode="json"), ensure_ascii=False),
        },
    )
    await db.commit()
    return SavedFilterResponse(filters=filters)


async def _quota_key(prefix: str, user_id: int) -> str:
    # UTC 日键，与 community quotas / redis consume_daily 重置对齐
    from app.core.redis import daily_quota_key

    return daily_quota_key(f"discovery:{prefix}", user_id)


async def _quota_limit(db: AsyncSession, user_id: int, is_vip: bool) -> int:
    """每日可查看的**不同用户**上限（委托 quotas 统一口径，与 /users/me/quotas 对齐）。"""
    return await resolve_browse_limit(db, user_id, is_vip=is_vip)


async def _record_quota_usage(db: AsyncSession, user_id: int, quota_code: str, source: str, reason: str, target_user_id: int | None = None) -> None:
    await db.execute(text("INSERT INTO user_quota_usage (user_id,quota_code,quota_date,source,reason,target_user_id) VALUES (:user_id,:quota_code,:quota_date,:source,:reason,:target_user_id)"), {"user_id": user_id, "quota_code": quota_code, "quota_date": date.today(), "source": source, "reason": reason, "target_user_id": target_user_id})


async def _consume_browse(db: AsyncSession, user_id: int, match_score: float, is_vip: bool, target_user_id: int) -> int:
    """扣除一次「查看不同用户」的额度，返回当天剩余可看的新用户数。

    计费单元是**当天查看过的不同 target_user_id 个数**：同一用户当天第 2 次及
    以后打开（``consume_daily_unique`` 返回 -1）直接放行，不扣常规/高匹配/积分
    任何一个池，也不重复写台账。额度耗尽只对"新用户"生效。
    """
    limit = await _quota_limit(db, user_id, is_vip)
    high_match_bonus = int(await get_runtime_value(db, "platform_permissions", "high_match_browse_bonus", settings.browse_high_match_bonus))
    regular_key = await _quota_key("browse", user_id)
    seen_key = await _quota_key("browse_seen", user_id)
    member = str(target_user_id)
    charged = await consume_daily_unique(seen_key, regular_key, member, limit)
    if charged == -1:
        return max(0, limit - await get_daily_used(regular_key))
    if charged == 1:
        await _record_quota_usage(db, user_id, "browse", "package" if is_vip else "free", "Daily profile view", target_user_id)
        return max(0, limit - await get_daily_used(regular_key))
    if match_score > 80:
        bonus_key = await _quota_key("browse_bonus", user_id)
        bonus = await consume_daily_unique(seen_key, bonus_key, member, high_match_bonus)
        if bonus == 1:
            await _record_quota_usage(db, user_id, "browse", "bonus", "High-match profile bonus", target_user_id)
            return max(0, high_match_bonus - await get_daily_used(bonus_key))
        if bonus == -1:
            return max(0, limit - await get_daily_used(regular_key))
    if await consume_extra(db, user_id, "browse", "积分兑换资料查看次数", target_user_id):
        # 积分池在 MySQL，无法与 Redis 原子扣减；补记 seen，避免同一用户重复扣积分。
        await mark_daily_member(seen_key, member)
        return 0
    raise HTTPException(429, detail="今日完整浏览额度已用完")


async def _record_browse(db: AsyncSession, viewer_id: int, target_id: int) -> None:
    anonymous = await db.execute(text("SELECT anonymous_browse_enabled FROM user_privacy WHERE user_id = :user_id"), {"user_id": viewer_id})
    if anonymous.scalar():
        return
    await db.execute(text("INSERT INTO user_browse_history (user_id, target_user_id) VALUES (:user_id, :target_id)"), {"user_id": viewer_id, "target_id": target_id})


_EDUCATION_LABELS: dict[int, str] = {1: "高中及以下", 2: "大专", 3: "本科", 4: "硕士", 5: "博士"}

# 后台择偶单选的三值口径：0=不限 1=正向 2=可接受。文案在服务端出，前端不维护枚举。
_ENUM_TEXT: dict[str, dict[int, str]] = {
    "marriage": {0: "不限", 1: "未婚", 2: "离异", 3: "丧偶"},
    "housing": {0: "不限", 1: "有房", 2: "无房"},
    "smoking": {0: "不限", 1: "不吸烟", 2: "可接受吸烟"},
    "drinking": {0: "不限", 1: "不饮酒", 2: "可接受饮酒"},
}


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _income_min_text(income_min: Any, income_range: Any) -> str:
    """收入要求文案：优先后台单选档位，其次按 ``income_min``（元）转「x万以上」。"""
    if income_range:
        return str(income_range)
    if income_min is None:
        return ""
    try:
        wan = float(income_min) / 10000
    except (TypeError, ValueError):
        return ""
    if wan <= 0:
        return ""
    return f"{wan:.1f}".rstrip("0").rstrip(".") + "万以上"


def _preference_distance_text(accept_long_distance: bool, accept_cross_province: bool) -> str:
    """距离三选一，对应设计稿的三个选项。"""
    if accept_cross_province:
        return "距离不是问题"
    if accept_long_distance:
        return "异地也可以"
    return "最好同城"


async def _public_preference(db: AsyncSession, user_id: int) -> dict[str, Any] | None:
    """「Ta的理想型」：他人可见的择偶偏好展示归一。

    与 ``get_profile`` 同门禁（仅 ``view_profile`` 判定为 full 时调用），
    中文文案（``*_text``）在服务端合成，前端不维护枚举。
    ``extra_requirement`` 为自由文本（易泄露联系方式、易出现人身攻击），
    按 2026-10-27 决策**不对他人下发**。
    """
    row = (await db.execute(text("""SELECT age_min, age_max, height_min, height_max,
               education_min, education_requirement, income_min, income_range,
               marriage_status, marriage_requirement,
               housing_requirement, housing_expectation,
               smoking_requirement, smoking_expectation,
               drinking_requirement, drinking_expectation,
               preferred_province_code, preferred_city_codes,
               accept_long_distance, accept_cross_province,
               dating_goal, meeting_pace, children_intention, marriage_timeline,
               preferred_occupation
        FROM user_partner_preference WHERE user_id = :user_id"""), {"user_id": user_id})).mappings().first()
    if not row:
        return None
    data = dict(row)
    codes = data.get("preferred_city_codes")
    if isinstance(codes, str):
        try:
            codes = json.loads(codes)
        except json.JSONDecodeError:
            codes = []
    data["preferred_city_codes"] = codes if isinstance(codes, list) else []
    province_name = ""
    try:
        from app.services.regions import region_name

        data["preferred_region_text"] = "、".join(
            name for name in (region_name(str(code)) for code in data["preferred_city_codes"]) if name
        )
        province_code = data.get("preferred_province_code")
        province_name = region_name(str(province_code)) if province_code else ""
    except Exception:  # pragma: no cover - 区划数据异常不应影响详情页
        logger.exception("Failed to resolve preference region names")
        data["preferred_region_text"] = ""
    data["distance_text"] = _preference_distance_text(
        bool(data.get("accept_long_distance")), bool(data.get("accept_cross_province"))
    )
    education_min = data.get("education_min")
    data["education_min_text"] = _EDUCATION_LABELS.get(int(education_min), "") if education_min else ""
    data["income_min_text"] = _income_min_text(data.get("income_min"), data.get("income_range"))
    data["marriage_text"] = data.get("marriage_requirement") or _ENUM_TEXT["marriage"].get(
        _as_int(data.get("marriage_status")), ""
    )
    data["housing_text"] = data.get("housing_expectation") or _ENUM_TEXT["housing"].get(
        _as_int(data.get("housing_requirement")), ""
    )
    data["smoking_text"] = data.get("smoking_expectation") or _ENUM_TEXT["smoking"].get(
        _as_int(data.get("smoking_requirement")), ""
    )
    data["drinking_text"] = data.get("drinking_expectation") or _ENUM_TEXT["drinking"].get(
        _as_int(data.get("drinking_requirement")), ""
    )
    # 结婚时间/期望职业：C 端存中文文本，服务端同时给 *_text 便于前端不维护映射。
    data["marriage_timeline_text"] = data.get("marriage_timeline") or ""
    data["preferred_occupation_text"] = data.get("preferred_occupation") or ""
    # 期望地区：省名 + 城市名；只选省时也有值（此前只拼城市，导致整行不显示）。
    area_parts = [part for part in (province_name, data.get("preferred_region_text", "")) if part]
    data["expected_area"] = " ".join(area_parts)
    # 期待标签（relationship_expectation）不在 user_partner_preference 里，
    # 详情页已由 tag_selections 单独渲染，这里给空数组保证前端契约稳定。
    data["extras"] = []
    return data


async def public_preference(db: AsyncSession, viewer_id: int, target_id: int) -> dict[str, Any]:
    """他人择偶偏好（``GET /users/{user_id}/preference-public``）。

    刻意复用 ``view_profile`` 而不是另写一套门禁：
    - 维护态 / 自看拦截 / 可见性 / ``only_vip_can_see_detail`` 与详情页完全一致；
    - 额度走 ``_consume_browse``，同一天同一目标重复查看按「已计费」放行，
      不会因为多调一次接口二次扣次数。
    未解锁（full=False）或对方没填时返回 ``has_data=False``，前端静默隐藏。
    """
    if viewer_id == target_id:
        raise HTTPException(422, detail="查看自己的择偶要求请使用 /users/me/preferences")
    detail = await view_profile(db, viewer_id, target_id)
    preference = detail.preference
    if not preference:
        return {"has_data": False, "user_id": target_id}
    return {"has_data": True, "user_id": target_id, **preference}


async def view_profile(db: AsyncSession, viewer_id: int, target_id: int) -> PublicProfileResponse:
    if not await platform_browse_open(db):
        raise HTTPException(403, detail=await platform_maintenance_message(db))
    if viewer_id == target_id:
        raise HTTPException(422, detail="不能浏览自己的名片")
    skip = _skip_discovery_gates()
    await _ensure_target(db, viewer_id, target_id, skip_completion_gate=skip)
    rows = await _target_rows(db, viewer_id, [target_id], skip_completion_gate=skip)
    row = rows.get(target_id)
    if not row:
        raise HTTPException(404, detail="用户不存在或当前不可见")
    viewer = await _viewer_context(db, viewer_id)
    score, reason = _candidate_score(viewer, row)
    vip = await _is_vip(db, viewer_id)
    privacy_locked = bool(row.get("only_vip_can_see_detail")) and not vip
    quota = None if privacy_locked else await _consume_browse(db, viewer_id, score, vip, target_id)
    full = vip or (not privacy_locked and quota is not None)
    await _record_browse(db, viewer_id, target_id)
    card = _card(row, score, reason, detail_locked=not full)
    profile = await get_profile(db, target_id, public=True) if full else None
    if profile is not None:
        privacy = (await db.execute(text("SELECT hide_school, hide_company FROM user_privacy WHERE user_id = :user_id"), {"user_id": target_id})).mappings().first()
        if privacy:
            if privacy["hide_school"]:
                profile["education_level"] = None
                profile["school"] = None
            if privacy["hide_company"]:
                profile["occupation"] = None
                profile["industry"] = None
                profile["company"] = None
    # 「Ta的理想型」与 profile 同门禁：未解锁（full=False）不查库、不下发。
    preference = await _public_preference(db, target_id) if full else None
    await db.commit()
    return PublicProfileResponse(user_id=target_id, card=card, profile=profile, is_vip_viewer=vip, browse_quota_remaining=quota, can_apply=viewer["completion_score"] >= 100, preference=preference)


async def preview_card(db: AsyncSession, viewer_id: int, target_id: int) -> DiscoveryCardPreviewResponse:
    """首页卡片预览（``GET /users/{user_id}/card-preview``）。

    只服务首页的背景/相册/9 格补字段。可见性口径与 ``view_profile`` 完全一致
    （同一个 ``_ensure_target`` + ``_target_rows`` 谓词），差异只有三点：

    1. **不扣浏览额度**：不调用 ``_consume_browse``（否则打开首页就可能 429）；
    2. **不留痕**：不调用 ``_record_browse``（否则对方「谁看过我」会出现首页预取）；
    3. **不返回 profile**：只给公开卡字段 + 背景 + 首图，不是绕过计费看资料的通道。
    """
    if not await platform_browse_open(db):
        raise HTTPException(403, detail=await platform_maintenance_message(db))
    if viewer_id == target_id:
        raise HTTPException(422, detail="不能浏览自己的名片")
    skip = _skip_discovery_gates()
    await _ensure_target(db, viewer_id, target_id, skip_completion_gate=skip)
    rows = await _target_rows(db, viewer_id, [target_id], skip_completion_gate=skip)
    row = rows.get(target_id)
    if not row:
        raise HTTPException(404, detail="用户不存在或当前不可见")
    viewer = await _viewer_context(db, viewer_id)
    score, reason = _candidate_score(viewer, row)
    vip = await _is_vip(db, viewer_id)
    detail_locked = bool(row.get("only_vip_can_see_detail")) and not vip
    media = await _get_media(db, target_id, approved_only=True)
    backgrounds = [
        item["file_url"] for item in media if item.get("media_type") == "background" and item.get("file_url")
    ]
    photos = [item["file_url"] for item in media if item.get("media_type") == "photo" and item.get("file_url")][:1]
    income = row.get("income")
    # 口径与 profile.get_profile 一致（"1.5w"）；锁定态不下发，避免与卡片 income=None 矛盾。
    income_display = (
        f"{float(income) / 10000:.1f}".rstrip("0").rstrip(".") + "w" if income is not None and not detail_locked else None
    )
    return DiscoveryCardPreviewResponse(
        user_id=target_id,
        card=_card(row, score, reason, detail_locked=detail_locked),
        background_wall=backgrounds[0] if backgrounds else None,
        photos=photos,
        income_display=income_display,
    )


async def _target_rows(
    db: AsyncSession, viewer_id: int, target_ids: list[int], *, skip_completion_gate: bool = False
) -> dict[int, dict[str, Any]]:
    if not target_ids:
        return {}
    placeholders = ", ".join(f":target_{index}" for index in range(len(target_ids)))
    viewer = await _viewer_context(db, viewer_id)
    visibility = _visibility_predicate(
        viewer_id,
        viewer,
        await _is_vip(db, viewer_id),
        VisibilityScene.PROFILE,
        skip_completion_gate=skip_completion_gate,
    )
    params = {
        "viewer_id": viewer_id,
        **{f"target_{index}": value for index, value in enumerate(target_ids)},
    }
    params.update(visibility.params)
    result = await db.execute(
        text(CARD_SELECT + CARD_FROM + f""" WHERE u.id IN ({placeholders})
                 AND {visibility.clause}"""),
        params,
    )
    return {int(row["user_id"]): dict(row) for row in result.mappings().all()}


async def browse_history(db: AsyncSession, viewer_id: int, page: int, page_size: int) -> BrowseHistoryPage:
    viewer, vip, predicate = await _scene_visibility(
        db, viewer_id, VisibilityScene.HISTORY
    )
    offset = (page - 1) * page_size
    history_window = "" if vip else " AND h.created_at >= CURDATE()"
    visible = " AND " + predicate.clause
    params = {
        "user_id": viewer_id,
        **predicate.params,
        "limit": page_size,
        "offset": offset,
    }
    result = await db.execute(text(f"""SELECT h.target_user_id, MAX(h.created_at) AS viewed_at
        FROM user_browse_history h JOIN users u ON u.id = h.target_user_id
        LEFT JOIN user_privacy pr ON pr.user_id = u.id
        LEFT JOIN user_profile_completion c ON c.user_id = u.id
        WHERE h.user_id = :user_id{history_window}{visible}
        GROUP BY h.target_user_id ORDER BY viewed_at DESC LIMIT :limit OFFSET :offset"""), params)
    rows = list(result.mappings().all())
    targets = await _target_rows(db, viewer_id, [int(row["target_user_id"]) for row in rows])
    items = []
    for row in rows:
        target = targets.get(int(row["target_user_id"]))
        if target:
            score, reason = _candidate_score(viewer, target)
            items.append(BrowseHistoryItem(target=_card(target, score, reason, detail_locked=bool(target.get("only_vip_can_see_detail")) and not vip), viewed_at=row["viewed_at"]))
    count = await db.execute(text(f"""SELECT COUNT(DISTINCT h.target_user_id)
        FROM user_browse_history h JOIN users u ON u.id = h.target_user_id
        LEFT JOIN user_privacy pr ON pr.user_id = u.id
        LEFT JOIN user_profile_completion c ON c.user_id = u.id
        WHERE h.user_id = :user_id{history_window}{visible}"""), params)
    return BrowseHistoryPage(items=items, page=page, page_size=page_size, total=int(count.scalar() or 0))


async def visitors(db: AsyncSession, viewer_id: int, page: int, page_size: int) -> VisitorPage:
    viewer, vip, predicate = await _scene_visibility(
        db, viewer_id, VisibilityScene.VISITORS
    )
    visible = " AND " + predicate.clause
    params = {"viewer_id": viewer_id, "user_id": viewer_id, **predicate.params}
    count_result = await db.execute(text(f"""SELECT COUNT(DISTINCT h.user_id)
        FROM user_browse_history h JOIN users u ON u.id = h.user_id
        LEFT JOIN user_privacy pr ON pr.user_id = u.id
        LEFT JOIN user_profile_completion c ON c.user_id = u.id
        WHERE h.target_user_id = :viewer_id{visible}"""), params)
    count = int(count_result.scalar() or 0)
    if not vip:
        return VisitorPage(can_view_details=False, count=count, items=[], page=page, page_size=page_size, has_more=False)
    result = await db.execute(text(f"""SELECT h.user_id, MAX(h.created_at) AS viewed_at
        FROM user_browse_history h JOIN users u ON u.id = h.user_id
        LEFT JOIN user_privacy pr ON pr.user_id = u.id
        LEFT JOIN user_profile_completion c ON c.user_id = u.id
        WHERE h.target_user_id = :viewer_id{visible}
        GROUP BY h.user_id ORDER BY viewed_at DESC LIMIT :limit OFFSET :offset"""), {
            **params,
            "limit": page_size,
            "offset": (page - 1) * page_size,
        })
    rows = list(result.mappings().all())
    targets = await _target_rows(db, viewer_id, [int(row["user_id"]) for row in rows])
    items = [
        BrowseHistoryItem(
            target=_card(
                targets[int(row["user_id"])],
                *_candidate_score(viewer, targets[int(row["user_id"])]),
                detail_locked=bool(targets[int(row["user_id"])].get("only_vip_can_see_detail")) and not vip,
            ),
            viewed_at=row["viewed_at"],
        )
        for row in rows
        if int(row["user_id"]) in targets
    ]
    return VisitorPage(can_view_details=True, count=count, items=items, page=page, page_size=page_size, has_more=page * page_size < count)


async def visitor_count(db: AsyncSession, viewer_id: int, target_id: int) -> dict[str, int]:
    """Return only the deduplicated visitor count for a visible user."""
    await _ensure_target(db, viewer_id, target_id)
    result = await db.execute(
        text("SELECT COUNT(DISTINCT user_id) FROM user_browse_history WHERE target_user_id = :target_id"),
        {"target_id": target_id},
    )
    return {"user_id": target_id, "visitor_count": int(result.scalar() or 0)}


async def _ensure_target(
    db: AsyncSession, viewer_id: int, target_id: int, *, skip_completion_gate: bool | None = None
) -> None:
    """Raise 404 when the target is not visible to the viewer.

    ``skip_completion_gate=None`` follows the test-only DISCOVERY_SKIP_GATES
    switch, so展示链路（推荐/广场/搜索/详情）与交互链路（申请/收藏/爆灯/海报）
    使用同一套可见性口径：否则测试号在推荐里能看到对方、点进去却是 404。
    """
    if skip_completion_gate is None:
        skip_completion_gate = _skip_discovery_gates()
    if viewer_id == target_id:
        raise HTTPException(422, detail="不能对自己执行此操作")
    if target_id not in await _target_rows(db, viewer_id, [target_id], skip_completion_gate=skip_completion_gate):
        raise HTTPException(404, detail="目标用户不存在或当前不可见")


async def _lock_user_pair(db: AsyncSession, first_id: int, second_id: int) -> None:
    """Serialize writes involving the same two users without a schema migration."""
    left, right = sorted((first_id, second_id))
    await db.execute(
        text("SELECT id FROM users WHERE id IN (:left, :right) ORDER BY id FOR UPDATE"),
        {"left": left, "right": right},
    )


async def set_favorite(db: AsyncSession, viewer_id: int, target_id: int, enabled: bool) -> FavoriteResponse:
    await _ensure_target(db, viewer_id, target_id)
    if enabled:
        await db.execute(text("INSERT IGNORE INTO user_favorite (user_id, target_user_id, type) VALUES (:user_id, :target_id, 2)"), {"user_id": viewer_id, "target_id": target_id})
    else:
        await db.execute(text("DELETE FROM user_favorite WHERE user_id = :user_id AND target_user_id = :target_id AND type = 2"), {"user_id": viewer_id, "target_id": target_id})
    await db.commit()
    return FavoriteResponse(target_user_id=target_id, is_favorite=enabled)


async def list_favorites(db: AsyncSession, viewer_id: int, page: int, page_size: int) -> FavoritePage:
    viewer, vip, predicate = await _scene_visibility(
        db, viewer_id, VisibilityScene.FAVORITES
    )
    visible = " AND " + predicate.clause
    params = {
        "user_id": viewer_id,
        **predicate.params,
        "limit": page_size,
        "offset": (page - 1) * page_size,
    }
    total = int((await db.execute(text(f"""SELECT COUNT(*)
        FROM user_favorite f JOIN users u ON u.id = f.target_user_id
        LEFT JOIN user_privacy pr ON pr.user_id = u.id
        LEFT JOIN user_profile_completion c ON c.user_id = u.id
        WHERE f.user_id = :user_id AND f.type = 2{visible}"""), params)).scalar() or 0)
    result = await db.execute(text(f"""SELECT f.target_user_id
        FROM user_favorite f JOIN users u ON u.id = f.target_user_id
        LEFT JOIN user_privacy pr ON pr.user_id = u.id
        LEFT JOIN user_profile_completion c ON c.user_id = u.id
        WHERE f.user_id = :user_id AND f.type = 2{visible}
        ORDER BY f.created_at DESC, f.id DESC LIMIT :limit OFFSET :offset"""), params)
    targets = await _target_rows(db, viewer_id, [int(row[0]) for row in result])
    items = [
        _card(
            targets[target_id],
            *_candidate_score(viewer, targets[target_id]),
            detail_locked=bool(targets[target_id].get("only_vip_can_see_detail")) and not vip,
        )
        for target_id in targets
    ]
    return FavoritePage(items=items, page=page, page_size=page_size, total=total, has_more=page * page_size < total)


async def list_received_favorites(db: AsyncSession, viewer_id: int, page: int, page_size: int) -> FavoriteReceivedPage:
    """Return users who saved the viewer, while applying the same privacy rules as cards."""
    _, _, predicate = await _scene_visibility(
        db, viewer_id, VisibilityScene.FAVORITES
    )
    visible = " AND " + predicate.clause
    params = {
        "user_id": viewer_id,
        **predicate.params,
        "limit": page_size,
        "offset": (page - 1) * page_size,
    }
    total = int((await db.execute(text(f"""SELECT COUNT(*)
        FROM user_favorite f JOIN users u ON u.id = f.user_id
        LEFT JOIN user_privacy pr ON pr.user_id = u.id
        LEFT JOIN user_profile_completion c ON c.user_id = u.id
        WHERE f.target_user_id = :user_id AND f.type = 2{visible}"""), params)).scalar() or 0)
    result = await db.execute(text(f"""SELECT f.id, f.user_id, u.nickname, u.avatar, u.birthday,
        p.residence_city_code, f.created_at
        FROM user_favorite f JOIN users u ON u.id = f.user_id
        LEFT JOIN user_profile p ON p.user_id = u.id
        LEFT JOIN user_privacy pr ON pr.user_id = u.id
        LEFT JOIN user_profile_completion c ON c.user_id = u.id
        WHERE f.target_user_id = :user_id AND f.type = 2{visible}
        ORDER BY f.created_at DESC, f.id DESC LIMIT :limit OFFSET :offset"""), params)
    items = [
        FavoriteReceivedItem(
            id=int(row["id"]),
            user=RelationUserSummary(user_id=int(row["user_id"]), nickname=row["nickname"], avatar=row["avatar"], age=_calculate_age(row["birthday"]) if row["birthday"] else None, city_code=row["residence_city_code"]),
            relation="received",
            created_at=row["created_at"],
        )
        for row in result.mappings().all()
    ]
    return FavoriteReceivedPage(items=items, page=page, page_size=page_size, total=total, has_more=page * page_size < total)


async def _notify(db: AsyncSession, user_id: int, notification_type: str, title: str, content: str, related_user_id: int, related_id: int | None = None) -> None:
    target_type = "message" if notification_type.startswith("match_application") else "user"
    await emit_notification(
        db,
        recipient_user_id=user_id,
        actor_user_id=related_user_id,
        event_type=notification_type,
        title=title,
        content=content,
        target_type=target_type,
        target_id=related_id if related_id is not None else related_user_id,
    )


async def _consume_apply_quota(db: AsyncSession, viewer_id: int, vip: bool) -> bool:
    """扣一次申请额度。

    上限统一由 ``quotas.resolve_apply_limit`` 给出（与 ``/users/me/quotas`` 同源），
    本函数只负责扣减与积分加次兜底，不再自己解析配置。
    """
    key = await _quota_key("apply", viewer_id)
    limit = await resolve_apply_limit(db, viewer_id, is_vip=vip)
    if not await consume_daily(key, limit):
        if await consume_extra(db, viewer_id, "apply", "积分兑换申请次数"):
            return False
        raise HTTPException(429, detail="今日认识申请次数已用完")
    return True


async def _refund_quota_after_database_failure(key: str) -> None:
    try:
        await refund_daily(key)
    except Exception:
        logger.exception("Failed to refund daily quota after database failure")


def _forbidden(code: str, message: str) -> HTTPException:
    """带业务码的 403。

    ``detail`` 用 dict 形态（与 ``profile.py`` 的 ``GENDER_IMMUTABLE`` 一致），
    前端可按 ``code`` 分流到「完善资料 / 绑手机 / 实名认证」不同页面。
    为兼容仍读 ``message`` 的旧客户端，``app.main`` 的异常处理器会把
    ``detail.message`` 同时提到响应顶层 ``message``。
    """
    return HTTPException(403, detail={"code": code, "message": message, "retryable": False})


async def create_application(db: AsyncSession, viewer_id: int, target_id: int, request: ApplicationCreateRequest) -> ApplicationResponse:
    await ensure_user_allowed(db, viewer_id, "APPLICATION_RESTRICTED")
    await _lock_user_pair(db, viewer_id, target_id)
    await _ensure_target(db, viewer_id, target_id)
    await _expire_pending_applications(db)
    viewer = await _viewer_context(db, viewer_id)
    if not _skip_discovery_gates() and viewer["completion_score"] < 100:
        raise _forbidden("PROFILE_INCOMPLETE", "请先完善资料后再申请认识")
    if not viewer.get("phone"):
        raise _forbidden("PHONE_REQUIRED", "请先绑定手机号")
    line_perm = await match_line_permissions(db)
    # 卡片4「没有实名认证是否允许发起牵线」：no/未配置 = 禁止（与旧行为一致）
    if viewer.get("realname_status") != 2 and line_perm.get("realname_allowed") is not True:
        raise _forbidden("REALNAME_REQUIRED", "请先完成实名认证")
    # 2026-10-27 需求：未实名的接收方不再拦截「发起」这一步。
    # 申请照常写入 match_apply 并推送通知（个人中心-我的申请 / 消息-认识 均可见），
    # 接收方在点「同意 / 拒绝」时才校验实名（见 respond_application）。
    # 因此这里只读取对方实名状态，回填给前端做「对方需实名后才能处理」的提示；
    # 后台卡片4「是否允许被牵线」开关（be_linked_allowed）从此不再拦截发起。
    target_auth = await db.execute(text("SELECT COALESCE(realname_status, 0) AS realname_status FROM user_auth WHERE user_id = :target_id"), {"target_id": target_id})
    target_status = int((target_auth.mappings().first() or {}).get("realname_status") or 0)
    target_realname_required = target_status != 2
    existing = await db.execute(text("SELECT id, status FROM match_apply WHERE ((from_user_id = :from_id AND to_user_id = :to_id) OR (from_user_id = :to_id AND to_user_id = :from_id)) AND status IN (0, 1) LIMIT 1"), {"from_id": viewer_id, "to_id": target_id})
    if existing.first():
        raise HTTPException(409, detail="双方已有进行中的认识申请或匹配")
    quota_key = await _quota_key("apply", viewer_id)
    vip = await _is_vip(db, viewer_id)
    if await _consume_apply_quota(db, viewer_id, vip):
        await _record_quota_usage(db, viewer_id, "apply", "package" if vip else "free", "Daily application quota", target_id)
    expire_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=48)
    try:
        result = await db.execute(text("INSERT INTO match_apply (from_user_id, to_user_id, message, status, expire_at) VALUES (:from_id, :to_id, :message, 0, :expire_at)"), {"from_id": viewer_id, "to_id": target_id, "message": request.message, "expire_at": expire_at})
        await db.execute(text("INSERT IGNORE INTO user_swipe_record (user_id, target_user_id, action, scene) VALUES (:user_id, :target_id, 3, 'recommend')"), {"user_id": viewer_id, "target_id": target_id})
        await _notify(db, target_id, "match_application", "收到新的认识申请", request.message or "有人申请认识你", viewer_id, result.lastrowid)
        await db.commit()
    except Exception:
        try:
            await db.rollback()
        except Exception:
            logger.exception("Failed to roll back application create transaction")
        await _refund_quota_after_database_failure(quota_key)
        raise
    created = await db.execute(text("SELECT id, from_user_id, to_user_id, message, status, expire_at, created_at FROM match_apply WHERE id = :id"), {"id": result.lastrowid})
    return ApplicationResponse(**created.mappings().one(), target_realname_required=target_realname_required)


async def list_applications(db: AsyncSession, viewer_id: int, incoming: bool, page: int, page_size: int) -> ApplicationPage:
    await _expire_pending_applications(db)
    field = "to_user_id" if incoming else "from_user_id"
    candidate_alias = "fu" if incoming else "tu"
    # 2026-10-27 修复：收件箱不再复用「发现/推荐」的可见性谓词。
    # 旧实现用 VisibilityScene.INTERACTION，谓词里含 match_status=1、who_can_see_me IN (1,2,3)
    # 且默认带完整度门禁（list_applications 未传 skip_completion_gate，DISCOVERY_SKIP_GATES
    # 对它无效）。后果：对方脱单/暂停/设「完全私密」后，历史申请会从列表凭空消失（记录仍在
    # match_apply，用户以为被吞）；测试号（完整度 <100）收件箱恒空。
    # 收件箱只回答「这条申请还在不在」，因此只保留双向拉黑过滤。
    # 上方 fpr/fc/tpr/tc 四个 JOIN 保留：无副作用，后续排序/联表仍可用。
    visible = f""" AND NOT EXISTS (
        SELECT 1 FROM user_block bl
        WHERE (bl.user_id = :user_id AND bl.target_user_id = {candidate_alias}.id)
           OR (bl.user_id = {candidate_alias}.id AND bl.target_user_id = :user_id))"""
    params = {
        "user_id": viewer_id,
        "limit": page_size,
        "offset": (page - 1) * page_size,
    }
    total = int((await db.execute(text(f"""SELECT COUNT(*)
        FROM match_apply a
        JOIN users fu ON fu.id = a.from_user_id
        LEFT JOIN user_privacy fpr ON fpr.user_id = fu.id
        LEFT JOIN user_profile_completion fc ON fc.user_id = fu.id
        JOIN users tu ON tu.id = a.to_user_id
        LEFT JOIN user_privacy tpr ON tpr.user_id = tu.id
        LEFT JOIN user_profile_completion tc ON tc.user_id = tu.id
        WHERE a.{field} = :user_id{visible}"""), params)).scalar() or 0)
    result = await db.execute(text(f"""SELECT a.id, a.from_user_id, a.to_user_id, a.message, a.status, a.expire_at, a.created_at,
        fu.nickname AS from_nickname, fu.avatar AS from_avatar, fu.birthday AS from_birthday, fp.residence_city_code AS from_city_code,
        tu.nickname AS to_nickname, tu.avatar AS to_avatar, tu.birthday AS to_birthday, tp.residence_city_code AS to_city_code
        FROM match_apply a
        JOIN users fu ON fu.id = a.from_user_id
        LEFT JOIN user_profile fp ON fp.user_id = fu.id
        LEFT JOIN user_privacy fpr ON fpr.user_id = fu.id
        LEFT JOIN user_profile_completion fc ON fc.user_id = fu.id
        JOIN users tu ON tu.id = a.to_user_id
        LEFT JOIN user_profile tp ON tp.user_id = tu.id
        LEFT JOIN user_privacy tpr ON tpr.user_id = tu.id
        LEFT JOIN user_profile_completion tc ON tc.user_id = tu.id
        WHERE a.{field} = :user_id{visible}
        ORDER BY a.created_at DESC, a.id DESC LIMIT :limit OFFSET :offset"""), params)
    items = []
    for row in result.mappings().all():
        data = dict(row)
        # 响应契约同时要求顶层 from_user_id/to_user_id 与嵌套 from_user/to_user，
        # 因此先取出 ID 再构造嵌套对象，避免 pop 之后顶层字段缺失导致校验失败。
        from_user_id = data.pop("from_user_id")
        to_user_id = data.pop("to_user_id")
        data["from_user"] = RelationUserSummary(user_id=from_user_id, nickname=data.pop("from_nickname"), avatar=data.pop("from_avatar"), age=_calculate_age(data.pop("from_birthday")) if data.get("from_birthday") else None, city_code=data.pop("from_city_code"))
        data["to_user"] = RelationUserSummary(user_id=to_user_id, nickname=data.pop("to_nickname"), avatar=data.pop("to_avatar"), age=_calculate_age(data.pop("to_birthday")) if data.get("to_birthday") else None, city_code=data.pop("to_city_code"))
        data["from_user_id"] = from_user_id
        data["to_user_id"] = to_user_id
        items.append(ApplicationResponse(**data))
    return ApplicationPage(items=items, page=page, page_size=page_size, total=total, has_more=page * page_size < total)


DEFAULT_AUTO_GREETING = "你好呀，我们可以开始聊天啦！"


async def _auto_greeting(db: AsyncSession) -> str:
    """「同意认识申请」后自动发送的问候文案。

    运营可在后台 ``platform_permissions.auto_greeting_template`` 覆盖，
    未配置/配置为空时回落到 :data:`DEFAULT_AUTO_GREETING`（需求原文用字）。
    纯文本，不做变量替换：避免在没有消费方时引入模板引擎。
    """
    value = await get_runtime_value(db, "platform_permissions", "auto_greeting_template", DEFAULT_AUTO_GREETING)
    template = "" if value is None else str(value).strip()
    return template or DEFAULT_AUTO_GREETING


async def respond_application(db: AsyncSession, viewer_id: int, application_id: int, accepted: bool, request: ApplicationRejectRequest | None = None) -> ApplicationResponse:
    await _expire_pending_applications(db)
    result = await db.execute(text("SELECT id, from_user_id, to_user_id, message, status, expire_at, created_at FROM match_apply WHERE id = :id AND to_user_id = :user_id FOR UPDATE"), {"id": application_id, "user_id": viewer_id})
    row = result.mappings().first()
    if not row:
        raise HTTPException(404, detail="认识申请不存在")
    if row["status"] != 0:
        raise HTTPException(409, detail="当前申请已处理")
    await _ensure_target(db, viewer_id, int(row["from_user_id"]))
    # 2026-10-27 需求：回应方（同意/拒绝的人）必须已实名，未实名引导去认证。
    # 发起侧已放开（见 create_application），所以这里是唯一的实名闸口。
    # 受后台卡片4「没有实名认证是否允许发起牵线」开关约束（=yes 时放行），保持后台可控。
    line_perm = await match_line_permissions(db)
    if line_perm.get("realname_allowed") is not True:
        responder_auth = await db.execute(
            text("SELECT COALESCE(realname_status, 0) AS realname_status FROM user_auth WHERE user_id = :user_id"),
            {"user_id": viewer_id},
        )
        responder_status = int((responder_auth.mappings().first() or {}).get("realname_status") or 0)
        if responder_status != 2:
            raise _forbidden("REALNAME_REQUIRED", "请先完成实名认证后再回应认识申请")
    status = 1 if accepted else 2
    await db.execute(text("UPDATE match_apply SET status = :status, responded_at = UTC_TIMESTAMP(), updated_at = UTC_TIMESTAMP() WHERE id = :id"), {"status": status, "id": application_id})
    if accepted:
        for left, right in ((row["from_user_id"], row["to_user_id"]), (row["to_user_id"], row["from_user_id"])):
            await db.execute(text("INSERT INTO user_match (user_id, target_user_id, status) VALUES (:left, :right, 1) ON DUPLICATE KEY UPDATE status = 1, updated_at = UTC_TIMESTAMP()"), {"left": left, "right": right})
        first, second = sorted((row["from_user_id"], row["to_user_id"]))
        session = await db.execute(text("SELECT id FROM chat_session WHERE user1_id = :first AND user2_id = :second LIMIT 1"), {"first": first, "second": second})
        session_id = session.scalar()
        if session_id:
            # 复用已有会话时必须复位双方「隐藏」标记（DELETE /chat/sessions/{id} 会把对应列置 1）。
            # 否则用户此前隐藏过该会话，重新通过申请后聊天列表仍然看不到它，
            # 与需求「申请通过后这里会显示聊天」直接冲突。
            await db.execute(text("""UPDATE chat_session
                SET is_user1_hidden = 0, is_user2_hidden = 0, updated_at = UTC_TIMESTAMP()
                WHERE id = :session_id"""), {"session_id": session_id})
        else:
            created_session = await db.execute(text("INSERT INTO chat_session (user1_id, user2_id) VALUES (:first, :second)"), {"first": first, "second": second})
            session_id = int(created_session.lastrowid)
        greeting = await _auto_greeting(db)
        # 问候去重按幂等键而不是 content：文案一旦调整，content 全等比较会失效并插入第二条。
        greeting_key = f"accept-greeting:{application_id}"
        existing_greeting = await db.execute(text("""SELECT id FROM chat_message
            WHERE session_id = :session_id AND from_user_id = :from_user_id
              AND client_message_id = :client_message_id LIMIT 1"""),
            {"session_id": session_id, "from_user_id": viewer_id, "client_message_id": greeting_key})
        if not existing_greeting.scalar():
            await db.execute(text("""INSERT INTO chat_message
                (session_id, from_user_id, to_user_id, type, content, client_message_id, is_read)
                VALUES (:session_id, :from_user_id, :to_user_id, 6, :greeting, :client_message_id, 0)"""), {
                "session_id": session_id, "from_user_id": viewer_id,
                "to_user_id": row["from_user_id"], "greeting": greeting,
                "client_message_id": greeting_key,
            })
            unread_field = "unread_count_user1" if first == row["from_user_id"] else "unread_count_user2"
            await db.execute(text(f"""UPDATE chat_session SET last_message = :greeting,
                last_message_time = UTC_TIMESTAMP(), {unread_field} = {unread_field} + 1,
                updated_at = UTC_TIMESTAMP() WHERE id = :session_id"""),
                {"greeting": greeting, "session_id": session_id})
        await _notify(db, row["from_user_id"], "match_application_accepted", "认识申请已通过", "对方接受了你的认识申请", viewer_id, application_id)
    else:
        await _notify(db, row["from_user_id"], "match_application_rejected", "认识申请未通过", (request.reason if request else None) or "对方暂时婉拒了你的申请", viewer_id, application_id)
        created_at = row["created_at"]
        if created_at is not None and created_at.date() == datetime.now(UTC).date():
            await refund_daily(await _quota_key("apply", row["from_user_id"]))
            await db.execute(text("INSERT INTO user_quota_usage (user_id,quota_code,quota_date,source,reason,target_user_id) VALUES (:user_id,'apply',:quota_date,'refund','申请被拒绝，返还申请次数',:target_user_id)"), {"user_id": row["from_user_id"], "quota_date": date.today(), "target_user_id": viewer_id})
    await db.commit()
    updated = await db.execute(text("SELECT id, from_user_id, to_user_id, message, status, expire_at, created_at FROM match_apply WHERE id = :id"), {"id": application_id})
    return ApplicationResponse(**updated.mappings().one())


async def _expire_pending_applications(db: AsyncSession) -> None:
    await db.execute(text("""UPDATE match_apply
        SET status = 3, updated_at = UTC_TIMESTAMP()
        WHERE status = 0 AND expire_at IS NOT NULL AND expire_at <= UTC_TIMESTAMP()"""))


async def create_superlike(db: AsyncSession, viewer_id: int, target_id: int, idempotency_key: str) -> SuperLikeResponse:
    await _lock_user_pair(db, viewer_id, target_id)
    await _ensure_target(db, viewer_id, target_id)
    viewer_result = await db.execute(text("""SELECT u.phone, COALESCE(c.score, 0) AS completion_score,
        COALESCE(ua.realname_status, 0) AS realname_status
        FROM users u LEFT JOIN user_profile_completion c ON c.user_id = u.id
        LEFT JOIN user_auth ua ON ua.user_id = u.id
        WHERE u.id = :user_id FOR UPDATE"""), {"user_id": viewer_id})
    viewer = viewer_result.mappings().first()
    if not viewer or not viewer["phone"]:
        raise HTTPException(403, detail="请先绑定手机号")
    if not _skip_discovery_gates() and float(viewer["completion_score"] or 0) < 100:
        raise HTTPException(403, detail="请先完善资料后再爆灯")
    if int(viewer["realname_status"] or 0) != 2:
        raise HTTPException(403, detail="请先完成实名认证后再爆灯")
    order_no = "free-superlike-" + idempotency_key
    existing = await db.execute(text("""SELECT created_at FROM user_boost
        WHERE user_id = :user_id AND target_user_id = :target_id AND order_no = :order_no
        ORDER BY id DESC LIMIT 1"""), {"user_id": viewer_id, "target_id": target_id, "order_no": order_no})
    existing_row = existing.mappings().first()
    if existing_row:
        vip = await _is_vip(db, viewer_id)
        limit = await resolve_superlike_limit(db, viewer_id, is_vip=vip)
        used = await get_daily_used(await _quota_key("superlike", viewer_id))
        return SuperLikeResponse(target_user_id=target_id, remaining_today=max(0, limit - used), created_at=existing_row["created_at"])
    vip = await _is_vip(db, viewer_id)
    limit = await resolve_superlike_limit(db, viewer_id, is_vip=vip)
    key = await _quota_key("superlike", viewer_id)
    if not await consume_daily(key, limit):
        if await consume_extra(db, viewer_id, "superlike", "积分兑换爆灯次数", target_id):
            # The extra grant is consumed below as part of the same operation.
            pass
        else:
            raise HTTPException(429, detail="今日爆灯次数已用完")
    created_at = datetime.now(UTC).replace(tzinfo=None)
    try:
        await db.execute(text("""INSERT INTO user_boost (user_id, target_user_id, amount, order_no, start_at, end_at, status)
            VALUES (:user_id, :target_id, 0, :order_no, :start_at, :end_at, 1)"""), {
            "user_id": viewer_id, "target_id": target_id, "order_no": order_no,
            "start_at": created_at, "end_at": created_at + timedelta(days=1),
        })
        await _notify(db, target_id, "superlike", "收到爆灯", "有人对你发出了爆灯信号", viewer_id)
        await db.commit()
    except Exception:
        try:
            await db.rollback()
        except Exception:
            logger.exception("Failed to roll back superlike create transaction")
        await _refund_quota_after_database_failure(key)
        raise
    used = await get_daily_used(key)
    return SuperLikeResponse(target_user_id=target_id, remaining_today=max(0, limit - used), created_at=created_at)


async def list_superlikes(db: AsyncSession, viewer_id: int, direction: str, page: int, page_size: int) -> SuperLikePage:
    if direction not in {"sent", "received"}:
        raise ValueError("invalid superlike direction")
    owner_field = "b.user_id" if direction == "sent" else "b.target_user_id"
    other_field = "b.target_user_id" if direction == "sent" else "b.user_id"
    _, _, predicate = await _scene_visibility(
        db, viewer_id, VisibilityScene.FAVORITES
    )
    params = {
        "user_id": viewer_id,
        **predicate.params,
        "limit": page_size,
        "offset": (page - 1) * page_size,
    }
    # user_boost 同时承载「置顶助推」（order_type=4，写 user_id=target_user_id=自己），
    # 排除自指记录，否则自己买的置顶会被算成「我给自己爆灯 / 我收到一次爆灯」。
    where = f"{owner_field} = :user_id AND b.user_id <> b.target_user_id AND {predicate.clause}"
    total = int((await db.execute(text(f"""SELECT COUNT(*) FROM user_boost b
        JOIN users u ON u.id = {other_field}
        LEFT JOIN user_privacy pr ON pr.user_id = u.id
        LEFT JOIN user_profile_completion c ON c.user_id = u.id
        WHERE {where}"""), params)).scalar() or 0)
    result = await db.execute(text(f"""SELECT b.id, b.order_no, b.created_at, b.status,
        u.id AS other_id, u.nickname, u.avatar, u.birthday, p.residence_city_code,
        EXISTS (SELECT 1 FROM user_match m WHERE m.user_id = :user_id AND m.target_user_id = u.id AND m.status IN (1, 2)) AS matched
        FROM user_boost b JOIN users u ON u.id = {other_field}
        LEFT JOIN user_profile p ON p.user_id = u.id
        LEFT JOIN user_privacy pr ON pr.user_id = u.id
        LEFT JOIN user_profile_completion c ON c.user_id = u.id
        WHERE {where} ORDER BY b.created_at DESC, b.id DESC LIMIT :limit OFFSET :offset"""), params)
    items = [
        SuperLikeItem(
            id=int(row["id"]),
            user=RelationUserSummary(user_id=int(row["other_id"]), nickname=row["nickname"], avatar=row["avatar"], age=_calculate_age(row["birthday"]) if row["birthday"] else None, city_code=row["residence_city_code"]),
            direction=direction,
            status=int(row["status"] or 1),
            created_at=row["created_at"],
            matched=bool(row["matched"]),
            order_no=row["order_no"],
        )
        for row in result.mappings().all()
    ]
    return SuperLikePage(items=items, page=page, page_size=page_size, total=total, has_more=page * page_size < total)


async def create_poster(db: AsyncSession, viewer_id: int, target_id: int, template: int) -> Response:
    await _ensure_target(db, viewer_id, target_id)
    if not settings.wechat_app_id or not settings.wechat_app_secret:
        raise HTTPException(503, detail="微信小程序码服务未配置")
    profile = await get_profile(db, target_id)
    async with httpx.AsyncClient(timeout=10) as client:
        token_response = await client.get("https://api.weixin.qq.com/cgi-bin/token", params={"grant_type": "client_credential", "appid": settings.wechat_app_id, "secret": settings.wechat_app_secret})
        token_data = token_response.json()
        if token_data.get("errcode") or not token_data.get("access_token"):
            raise HTTPException(503, detail="微信访问令牌获取失败")
        qr_response = await client.post(f"https://api.weixin.qq.com/wxa/getwxacodeunlimit?access_token={token_data['access_token']}", json={"scene": f"uid={target_id}", "page": settings.wechat_mini_program_page, "check_path": False, "env_version": "release"})
    if "image" not in qr_response.headers.get("content-type", ""):
        raise HTTPException(503, detail="微信小程序码生成失败")
    qr = Image.open(__import__("io").BytesIO(qr_response.content)).convert("RGB")
    colors = [(247, 250, 252), (255, 246, 238), (241, 248, 245), (246, 244, 252), (244, 248, 255)]
    canvas = Image.new("RGB", (750, 1100), colors[(template - 1) % len(colors)])
    draw = ImageDraw.Draw(canvas)
    draw.text((60, 70), profile.get("nickname") or "Xuanshi AI", fill=(30, 41, 59), font=ImageFont.load_default(size=40))
    draw.text((60, 135), "发现真实、认真而有趣的连接", fill=(71, 85, 105), font=ImageFont.load_default(size=24))
    qr.thumbnail((480, 480))
    canvas.paste(qr, ((750 - qr.width) // 2, 300))
    output = __import__("io").BytesIO()
    canvas.save(output, format="PNG")
    return Response(content=output.getvalue(), media_type="image/png", headers={"Content-Disposition": f"inline; filename=profile-{target_id}.png"})

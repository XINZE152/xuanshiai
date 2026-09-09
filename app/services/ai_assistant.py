"""Phase-one AI features built on the existing profile, chat and discovery data."""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.redis import consume_daily, daily_quota_key
from app.schemas.ai import (
    AIAssistantMessageResponse,
    AIAssistantSessionPage,
    AIAssistantSessionResponse,
    AIMatchItem,
    AIMatchPage,
    AIProfilePolishRequest,
    AIProfilePolishResponse,
    AIProfileThoughtfulnessRequest,
    AIProfileThoughtfulnessResponse,
    AIProfileThoughtfulnessTodo,
    AISearchRequest,
    AISearchResponse,
)
from app.schemas.discovery import DiscoveryFilters
from app.services.ai_provider import complete, parse_json
from app.services.discovery import _candidate_score, _card, _fetch_rows, _viewer_context

MatchType = Literal["who_likes_me", "i_like", "material", "soul"]


async def _require_vip(db: AsyncSession, user_id: int) -> None:
    row = await db.execute(text("""SELECT 1 FROM user_membership
        WHERE user_id=:user_id AND status=1
          AND (start_at IS NULL OR start_at<=UTC_TIMESTAMP())
          AND (end_at IS NULL OR end_at>UTC_TIMESTAMP()) LIMIT 1"""), {"user_id": user_id})
    if not row.scalar():
        raise HTTPException(403, detail="AI功能仅限会员使用")


async def _consume_ai_quota(db: AsyncSession, user_id: int, code: str, limit: int) -> None:
    if not await consume_daily(daily_quota_key(f"ai:{code}", user_id), limit):
        raise HTTPException(429, detail="今日 AI 使用次数已用完")


async def create_assistant_session(db: AsyncSession, user_id: int, title: str | None) -> AIAssistantSessionResponse:
    await _require_vip(db, user_id)
    result = await db.execute(text("""INSERT INTO ai_assistant_session (user_id,title)
        VALUES (:user_id,:title)"""), {"user_id": user_id, "title": title or "AI助手会话"})
    await db.commit()
    row = (await db.execute(text("SELECT id,title,created_at,updated_at FROM ai_assistant_session WHERE id=:id"), {"id": result.lastrowid})).mappings().one()
    return AIAssistantSessionResponse(id=int(row["id"]), title=row["title"], message_count=0, created_at=row["created_at"], updated_at=row["updated_at"])


async def list_assistant_sessions(db: AsyncSession, user_id: int, page: int, page_size: int) -> AIAssistantSessionPage:
    await _require_vip(db, user_id)
    total = int((await db.execute(text("SELECT COUNT(*) FROM ai_assistant_session WHERE user_id=:user_id AND status=1"), {"user_id": user_id})).scalar() or 0)
    rows = (await db.execute(text("""SELECT s.id,s.title,s.created_at,s.updated_at,COUNT(m.id) message_count
        FROM ai_assistant_session s LEFT JOIN ai_assistant_message m ON m.session_id=s.id
        WHERE s.user_id=:user_id AND s.status=1 GROUP BY s.id ORDER BY s.updated_at DESC,s.id DESC
        LIMIT :limit OFFSET :offset"""), {"user_id": user_id, "limit": page_size, "offset": (page - 1) * page_size})).mappings().all()
    return AIAssistantSessionPage(items=[AIAssistantSessionResponse(id=int(r["id"]), title=r["title"], message_count=int(r["message_count"]), created_at=r["created_at"], updated_at=r["updated_at"]) for r in rows], page=page, page_size=page_size, total=total, has_more=page * page_size < total)


async def assistant_message(db: AsyncSession, user_id: int, session_id: int, content: str) -> AIAssistantMessageResponse:
    await _require_vip(db, user_id)
    session = (await db.execute(text("SELECT id FROM ai_assistant_session WHERE id=:id AND user_id=:user_id AND status=1"), {"id": session_id, "user_id": user_id})).scalar()
    if not session:
        raise HTTPException(404, detail="AI助手会话不存在")
    await _consume_ai_quota(db, user_id, "assistant", settings.ai_daily_assistant_limit)
    # The user explicitly chose to allow the assistant to inspect all of their
    # own chat records. Only the user's two-party messages are included.
    rows = (await db.execute(text("""SELECT from_user_id,content,created_at FROM chat_message
        WHERE (from_user_id=:user_id OR to_user_id=:user_id) AND type=1 AND revoked_at IS NULL
        ORDER BY created_at DESC LIMIT :limit"""), {"user_id": user_id, "limit": settings.ai_max_context_messages})).mappings().all()
    context = "\n".join(f"{'我' if int(r['from_user_id']) == user_id else '对方'}：{r['content']}" for r in reversed(rows))
    await db.execute(text("INSERT INTO ai_assistant_message (session_id,role,content) VALUES (:sid,'user',:content)"), {"sid": session_id, "content": content})
    prompt = f"你是婚恋沟通助手，只提供沟通建议，不做医疗、法律或高风险决定。\n聊天记录：\n{context}\n用户问题：{content}"
    answer = await complete([{"role": "system", "content": "你是谨慎、尊重隐私的婚恋沟通助手。"}, {"role": "user", "content": prompt}])
    result = await db.execute(text("INSERT INTO ai_assistant_message (session_id,role,content) VALUES (:sid,'assistant',:content)"), {"sid": session_id, "content": answer})
    await db.execute(text("UPDATE ai_assistant_session SET updated_at=UTC_TIMESTAMP() WHERE id=:id"), {"id": session_id})
    await db.commit()
    row = (await db.execute(text("SELECT id,session_id,role,content,created_at FROM ai_assistant_message WHERE id=:id"), {"id": result.lastrowid})).mappings().one()
    return AIAssistantMessageResponse(id=int(row["id"]), session_id=int(row["session_id"]), role="assistant", content=row["content"], created_at=row["created_at"])


async def polish_profile(db: AsyncSession, user_id: int, request: AIProfilePolishRequest) -> AIProfilePolishResponse:
    await _require_vip(db, user_id)
    await _consume_ai_quota(db, user_id, "polish", settings.ai_daily_polish_limit)
    content = await complete([{"role": "system", "content": "你只润色用户提供的原文，不添加未提供的事实。输出JSON：polished(string), changed_points(array[string])。"}, {"role": "user", "content": f"PROFILE_POLISH style={request.style} max_length={request.max_length}\n{request.content}"}], json_mode=True)
    data = parse_json(content)
    polished = str(data.get("polished") or request.content).strip()[:request.max_length]
    points = data.get("changed_points") if isinstance(data.get("changed_points"), list) else []
    return AIProfilePolishResponse(original=request.content, polished=polished, style=request.style, changed_points=[str(x) for x in points[:5]])


THOUGHTFULNESS_KEY_LABELS: dict[str, str] = {
    "basic_info": "基础信息",
    "self_intro": "自我介绍",
    "qa_answers": "关于我问答",
    "interest_tags": "兴趣标签",
    "personality_tags": "性格标签",
    "mbti": "MBTI",
    "avatar": "头像",
    "photos": "我的相册",
}
THOUGHTFULNESS_SYSTEM_PROMPT = (
    "你是认真婚恋平台的资料评审助手，评估用户填写资料的用心程度并给出具体可执行的改进建议。"
    "只基于提供的资料判断，不编造事实，不评价用户本人，只评价资料质量。"
    "文案不得承诺交友或婚恋结果，不得制造焦虑、催促或施压。"
    "输出JSON：score(int 0-100 整数), summary(string, 1-2句中文总结), "
    "todos(array of {key,label,advice,priority})。"
    "key只能取 basic_info/self_intro/qa_answers/interest_tags/personality_tags/mbti/avatar/photos 之一；"
    "label用中文展示名；advice是一句具体的修改建议(不超过80字)；"
    "todos按priority从高到低排序，最多5条，资料已经很好时返回空数组。priority取 high/medium/low。"
)


def _thoughtfulness_row_to_response(row: Any) -> AIProfileThoughtfulnessResponse:
    raw_todos = row["todos"]
    if isinstance(raw_todos, str):
        try:
            raw_todos = json.loads(raw_todos)
        except Exception:
            raw_todos = []
    todo_items = raw_todos if isinstance(raw_todos, list) else []
    todos: list[AIProfileThoughtfulnessTodo] = []
    for item in todo_items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "")
        if key not in THOUGHTFULNESS_KEY_LABELS:
            continue
        label = str(item.get("label") or "").strip()[:40] or THOUGHTFULNESS_KEY_LABELS[key]
        advice = str(item.get("advice") or "").strip()[:300]
        if not advice:
            continue
        priority = str(item.get("priority") or "medium")
        if priority not in ("high", "medium", "low"):
            priority = "medium"
        todos.append(AIProfileThoughtfulnessTodo(key=key, label=label, advice=advice, priority=priority))
    return AIProfileThoughtfulnessResponse(
        score=max(0, min(100, int(row["score"] or 0))),
        summary=str(row["summary"] or "").strip()[:500],
        todos=todos,
        generated_at=row["updated_at"] or row["created_at"],
    )


async def get_thoughtfulness(db: AsyncSession, user_id: int) -> AIProfileThoughtfulnessResponse:
    """返回当前用户最新一次用心度评审；从未评审过时返回 404（前端按「未评审」态处理）。"""
    row = (await db.execute(text("""SELECT score, summary, todos, created_at, updated_at
        FROM ai_profile_thoughtfulness WHERE user_id=:user_id"""), {"user_id": user_id})).mappings().first()
    if row is None:
        raise HTTPException(404, detail="尚未进行 AI 用心度评审")
    return _thoughtfulness_row_to_response(row)


async def analyze_thoughtfulness(db: AsyncSession, user_id: int, request: AIProfileThoughtfulnessRequest) -> AIProfileThoughtfulnessResponse:
    # 编辑页基础工具，不设 VIP 门槛：用心度是面向全员的资料改进指标（未来作为浏览他人资料的门槛）。
    await _consume_ai_quota(db, user_id, "thoughtfulness", settings.ai_daily_thoughtfulness_limit)
    profile_row = (await db.execute(text("""SELECT u.nickname, u.gender, u.birthday, u.is_married, u.avatar,
                       p.height, p.weight, p.occupation, p.industry, p.education_level, p.income,
                       p.residence_province_code, p.residence_city_code, p.residence_district_code,
                       p.hometown_province_code, p.hometown_city_code, p.hometown_district_code,
                       p.self_intro, p.interest_tags, p.personality_tags, p.mbti, p.tags
                FROM users u LEFT JOIN profiles p ON p.user_id = u.id WHERE u.id = :user_id"""),
            {"user_id": user_id})).mappings().first()
    if profile_row is None:
        raise HTTPException(404, detail="用户资料不存在")
    photo_count = int((await db.execute(text(
        "SELECT COUNT(*) FROM user_media WHERE user_id=:user_id AND deleted_at IS NULL AND media_type='photo'"),
        {"user_id": user_id})).scalar() or 0)

    facts: list[str] = []
    mapping = [
        ("性别", profile_row["gender"]), ("生日", profile_row["birthday"]), ("婚姻状况", profile_row["is_married"]),
        ("身高", profile_row["height"]), ("体重", profile_row["weight"]), ("职业", profile_row["occupation"]),
        ("行业", profile_row["industry"]), ("学历", profile_row["education_level"]), ("收入", profile_row["income"]),
        ("现居地", profile_row["residence_city_code"]), ("家乡", profile_row["hometown_city_code"]),
        ("自我介绍", profile_row["self_intro"]), ("兴趣标签", profile_row["interest_tags"]),
        ("性格标签", profile_row["personality_tags"]), ("MBTI", profile_row["mbti"]),
        ("标签选择", profile_row["tags"]), ("头像", "已上传" if profile_row["avatar"] else "未上传"),
    ]
    for label, value in mapping:
        if value is None or (isinstance(value, str) and value.strip() == ""):
            facts.append(f"{label}：未填写")
        else:
            facts.append(f"{label}：{value}")
    facts.append(f"相册照片数：{photo_count}")
    edited_keys = [k for k in (request.edited_keys or [])[:20] if k in THOUGHTFULNESS_KEY_LABELS]

    raw = await complete(
        [
            {"role": "system", "content": THOUGHTFULNESS_SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"THOUGHTFULNESS_REVIEW trigger={request.trigger} "
                f"edited_keys={','.join(edited_keys) if edited_keys else 'unknown'}\n"
                + "\n".join(facts)
            )},
        ],
        json_mode=True,
    )
    data = parse_json(raw)
    try:
        score = max(0, min(100, int(data.get("score"))))
    except (TypeError, ValueError):
        score = 0
    summary = str(data.get("summary") or "你的资料整体填写正常，可以继续完善细节。").strip()[:500]

    todo_items = data.get("todos") if isinstance(data.get("todos"), list) else []
    todos: list[AIProfileThoughtfulnessTodo] = []
    for item in todo_items[:8]:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "")
        if key not in THOUGHTFULNESS_KEY_LABELS:
            continue
        label = str(item.get("label") or "").strip()[:40] or THOUGHTFULNESS_KEY_LABELS[key]
        advice = str(item.get("advice") or "").strip()[:300]
        if not advice:
            continue
        priority = str(item.get("priority") or "medium")
        if priority not in ("high", "medium", "low"):
            priority = "medium"
        todos.append(AIProfileThoughtfulnessTodo(key=key, label=label, advice=advice, priority=priority))

    await db.execute(text("""INSERT INTO ai_profile_thoughtfulness
        (user_id, score, summary, todos, edited_keys, model_name)
        VALUES (:user_id, :score, :summary, :todos, :edited_keys, :model_name)
        ON DUPLICATE KEY UPDATE score=VALUES(score), summary=VALUES(summary), todos=VALUES(todos),
            edited_keys=VALUES(edited_keys), model_name=VALUES(model_name)"""), {
        "user_id": user_id,
        "score": score,
        "summary": summary,
        "todos": json.dumps([t.model_dump() for t in todos], ensure_ascii=False),
        "edited_keys": json.dumps(edited_keys, ensure_ascii=False),
        "model_name": settings.ai_model,
    })
    await db.commit()
    row = (await db.execute(text("""SELECT score, summary, todos, created_at, updated_at
        FROM ai_profile_thoughtfulness WHERE user_id=:user_id"""), {"user_id": user_id})).mappings().one()
    return _thoughtfulness_row_to_response(row)


async def parse_search(db: AsyncSession, user_id: int, request: AISearchRequest) -> AISearchResponse:
    await _require_vip(db, user_id)
    await _consume_ai_quota(db, user_id, "search", settings.ai_daily_search_limit)
    raw = await complete([{"role": "system", "content": "把自然语言婚恋搜索转换为JSON。只允许输出 filters、normalized_query、unresolved。filters只能包含 gender,age_min,age_max,city_code,marriage_status,education_min,height_min,height_max,income_min,income_max,tag。不要编造城市编码。"}, {"role": "user", "content": f"SEARCH_PARSE\n{request.query}"}], json_mode=True)
    data = parse_json(raw)
    filters = data.get("filters") if isinstance(data.get("filters"), dict) else {}
    allowed = set(DiscoveryFilters.model_fields) | {"tag"}
    filters = {k: v for k, v in filters.items() if k in allowed and v is not None}
    try:
        parsed = DiscoveryFilters(page=request.page, page_size=request.page_size, **{k: v for k, v in filters.items() if k != "tag"})
    except Exception:
        parsed = DiscoveryFilters(page=request.page, page_size=request.page_size)
        filters = {}
    from app.services.discovery import _fetch_rows
    rows = await _fetch_rows(db, user_id, parsed, plaza=True, tag=str(filters["tag"]) if filters.get("tag") else None, respect_preferences=False)
    viewer = await _viewer_context(db, user_id)
    scored = sorted([(_candidate_score(viewer, row), row) for row in rows], key=lambda x: x[0][0], reverse=True)
    start = (request.page - 1) * request.page_size
    selected = scored[start:start + request.page_size]
    viewer_vip = True
    from app.schemas.discovery import DiscoveryPage
    result_page = DiscoveryPage(items=[_card(row, score, reason, detail_locked=bool(row.get("only_vip_can_see_detail")) and not viewer_vip) for (score, reason), row in selected], page=request.page, page_size=request.page_size, total=len(scored), has_more=start + request.page_size < len(scored))
    return AISearchResponse(query=request.query, normalized_query=str(data.get("normalized_query") or request.query), filters=filters, unresolved=[str(x) for x in data.get("unresolved", []) if isinstance(x, (str, int))], results=result_page.model_dump())


def _breakdown(viewer: dict[str, Any], row: dict[str, Any], match_type: MatchType) -> dict[str, float]:
    score, _ = _candidate_score(viewer, row)
    tags = set(json.loads(viewer.get("interest_tags") or "[]") if isinstance(viewer.get("interest_tags"), str) else viewer.get("interest_tags") or [])
    candidate_tags = set(json.loads(row.get("interest_tags") or "[]") if isinstance(row.get("interest_tags"), str) else row.get("interest_tags") or [])
    overlap = min(100.0, len(tags & candidate_tags) * 20.0)
    if match_type == "material":
        return {"age": min(100.0, score), "location": 100.0 if viewer.get("residence_city_code") == row.get("residence_city_code") else 0.0, "preference": min(100.0, score * 0.7)}
    if match_type == "soul":
        return {"interest": overlap, "mbti": 70.0 if viewer.get("mbti") and row.get("mbti") else 0.0, "activity": 80.0 if row.get("last_active_at") else 0.0}
    return {"preference": score, "interest": overlap, "activity": 80.0 if row.get("last_active_at") else 0.0}


async def match_page(db: AsyncSession, user_id: int, match_type: MatchType, page: int, page_size: int) -> AIMatchPage:
    await _require_vip(db, user_id)
    await _consume_ai_quota(db, user_id, "match", settings.ai_daily_match_limit)
    viewer = await _viewer_context(db, user_id)
    rows = await _fetch_rows(db, user_id, DiscoveryFilters(page=1, page_size=20), plaza=True, respect_preferences=False)
    scored: list[tuple[float, dict[str, Any], dict[str, float]]] = []
    for row in rows:
        breakdown = _breakdown(viewer, row, match_type)
        score = round(sum(breakdown.values()) / max(1, len(breakdown)), 2)
        scored.append((score, row, breakdown))
    scored.sort(key=lambda x: x[0], reverse=True)
    start = (page - 1) * page_size
    selected = scored[start:start + page_size]
    items: list[AIMatchItem] = []
    for score, row, breakdown in selected:
        explanation = await complete([{"role": "system", "content": "根据给定分项生成简短、客观的JSON，不夸大成功概率。输出 reason(string), suggestions(array[string])。"}, {"role": "user", "content": f"MATCH_EXPLAIN type={match_type} score={score} breakdown={json.dumps(breakdown, ensure_ascii=False)}"}], json_mode=True)
        data = parse_json(explanation)
        items.append(AIMatchItem(user_id=int(row["user_id"]), nickname=row.get("nickname"), avatar=row.get("avatar"), match_type=match_type, match_score=score, score_breakdown=breakdown, match_reason=str(data.get("reason") or "资料存在一定匹配点"), suggestions=[str(x) for x in data.get("suggestions", []) if isinstance(x, str)][:3]))
    return AIMatchPage(match_type=match_type, items=items, page=page, page_size=page_size, total=len(scored), has_more=start + page_size < len(scored))


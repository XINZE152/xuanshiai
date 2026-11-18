"""恋 AI BOT 专属红娘会话：绑定、欢迎、轮次与落库。"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.redis import consume_daily, daily_quota_key, refund_daily
from app.schemas.aibot import (
    AibotAssignmentResponse,
    AibotSessionResponse,
    AibotTurnItem,
    AibotTurnResponse,
    AibotWelcomeResponse,
)
from app.services.aibot_prompts import (
    build_system_prompt,
    fallback_reply,
    get_agent_meta,
    list_agent_ids,
)
from app.services.ai.flags import AiFeature
from app.services.ai_provider import complete, parse_json
from app.services.content_filter import assert_text_allowed

logger = logging.getLogger(__name__)

_MAX_CONTEXT = 24
_WELCOME_FIXED = "欢迎来到宣誓爱，每个人都值得拥有专属红娘，遇见相伴一生的幸福伴侣。"


def _now_iso(value: Any = None) -> str:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    return datetime.utcnow().isoformat(sep=" ", timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _parse_json_list(raw: Any) -> list[Any]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="ignore")
    if isinstance(raw, str):
        text_value = raw.strip()
        if not text_value:
            return []
        try:
            parsed = json.loads(text_value)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def _choices_from(value: Any) -> list[str]:
    items = value if isinstance(value, list) else _parse_json_list(value)
    out: list[str] = []
    for item in items:
        s = str(item).strip()
        if s and s not in out:
            out.append(s)
        if len(out) >= 3:
            break
    return out


def _normalize_intent(value: Any) -> str:
    intent = str(value or "chat").strip().lower()
    allowed = {"chat", "recommend", "profile", "story", "mood", "report", "counsel"}
    return intent if intent in allowed else "chat"


def _actions_for_intent(intent: str) -> list[dict[str, Any]]:
    if intent == "recommend":
        return [{"type": "open_recommend"}]
    if intent == "report":
        return [{"type": "open_report"}]
    if intent == "profile":
        return [{"type": "open_profile"}]
    if intent == "story":
        return [{"type": "open_story"}]
    return []


async def _load_nickname(db: AsyncSession, user_id: int) -> str:
    try:
        row = (
            await db.execute(
                text(
                    """SELECT COALESCE(NULLIF(nickname, ''), '会员') AS name
                       FROM users WHERE id = :uid LIMIT 1"""
                ),
                {"uid": user_id},
            )
        ).mappings().first()
        if row and row.get("name"):
            return str(row["name"])
    except Exception:
        logger.debug("aibot users nickname lookup skipped", exc_info=True)
    return "会员"


async def _load_member_facts(db: AsyncSession, user_id: int) -> dict[str, Any]:
    """精准资料优先，空则回落 bot_intake 模糊文案，供红娘对话上下文。"""
    from app.services.aibot_profile_sync import facts_for_ai

    try:
        row = (
            await db.execute(
                text(
                    """SELECT u.gender, u.birthday, u.is_married,
                              p.height, p.weight, p.education_level, p.occupation, p.income,
                              p.residence_city_code, p.residence AS residence_text,
                              p.bot_intake_json
                       FROM users u
                       LEFT JOIN user_profile p ON p.user_id = u.id
                       WHERE u.id = :uid LIMIT 1"""
                ),
                {"uid": user_id},
            )
        ).mappings().first()
    except Exception:
        logger.debug("aibot member facts lookup skipped", exc_info=True)
        return {}
    if not row:
        return {}
    residence_display = None
    try:
        from app.services.regions import region_display

        residence_display = region_display(
            None, row.get("residence_city_code"), None
        ) or (str(row["residence_text"]).strip() if row.get("residence_text") else None)
    except Exception:
        residence_display = (
            str(row["residence_text"]).strip() if row.get("residence_text") else None
        )
    precise = {
        "gender": row.get("gender"),
        "birthday": row.get("birthday"),
        "is_married": row.get("is_married"),
        "height": row.get("height"),
        "weight": row.get("weight"),
        "education_level": row.get("education_level"),
        "occupation": row.get("occupation"),
        "income": row.get("income"),
        "residence_city_code": row.get("residence_city_code"),
        "residence_display": residence_display,
    }
    return facts_for_ai(precise=precise, bot_intake=row.get("bot_intake_json"))


async def get_assignment(db: AsyncSession, user_id: int) -> AibotAssignmentResponse:
    row = (
        await db.execute(
            text(
                """SELECT agent_id, assigned_at FROM aibot_user_assignment
                   WHERE user_id = :uid LIMIT 1"""
            ),
            {"uid": user_id},
        )
    ).mappings().first()
    if not row:
        return AibotAssignmentResponse(agent_id=None, assigned_at=None)
    return AibotAssignmentResponse(
        agent_id=str(row["agent_id"]),
        assigned_at=_now_iso(row.get("assigned_at")),
    )


async def put_assignment(db: AsyncSession, user_id: int, agent_id: str) -> AibotAssignmentResponse:
    if agent_id not in list_agent_ids():
        raise HTTPException(400, detail="无效的红娘编号")
    await db.execute(
        text(
            """INSERT INTO aibot_user_assignment (user_id, agent_id, assigned_at)
               VALUES (:uid, :agent_id, UTC_TIMESTAMP())
               ON DUPLICATE KEY UPDATE agent_id = VALUES(agent_id),
                   assigned_at = UTC_TIMESTAMP(), updated_at = UTC_TIMESTAMP()"""
        ),
        {"uid": user_id, "agent_id": agent_id},
    )
    # 换红娘时关闭旧会话，下次重新开新会话
    await db.execute(
        text(
            """UPDATE aibot_session SET status = 'closed', updated_at = UTC_TIMESTAMP()
               WHERE user_id = :uid AND status = 'active' AND agent_id <> :agent_id"""
        ),
        {"uid": user_id, "agent_id": agent_id},
    )
    await db.commit()
    return await get_assignment(db, user_id)


async def _list_turns(db: AsyncSession, session_id: str, limit: int = 200) -> list[AibotTurnItem]:
    rows = (
        await db.execute(
            text(
                """SELECT turn_id, role, content, intent, choices_json, actions_json, cards_json, created_at
                   FROM aibot_turn
                   WHERE session_id = :sid
                   ORDER BY id ASC
                   LIMIT :lim"""
            ),
            {"sid": session_id, "lim": limit},
        )
    ).mappings().all()
    items: list[AibotTurnItem] = []
    for row in rows:
        items.append(
            AibotTurnItem(
                turn_id=str(row["turn_id"]),
                role=str(row["role"]),
                content=str(row["content"] or ""),
                intent=str(row["intent"]) if row.get("intent") is not None else None,
                choices=_choices_from(row.get("choices_json")),
                actions=_parse_json_list(row.get("actions_json"))
                if not isinstance(row.get("actions_json"), list)
                else list(row.get("actions_json") or []),
                cards=row.get("cards_json"),
                created_at=_now_iso(row.get("created_at")),
            )
        )
    return items


async def _get_active_session_row(db: AsyncSession, user_id: int) -> Any:
    return (
        await db.execute(
            text(
                """SELECT session_id, agent_id, status, needs_welcome, last_intent
                   FROM aibot_session
                   WHERE user_id = :uid AND status = 'active'
                   ORDER BY updated_at DESC LIMIT 1"""
            ),
            {"uid": user_id},
        )
    ).mappings().first()


async def ensure_session(
    db: AsyncSession,
    user_id: int,
    agent_id: str | None = None,
) -> AibotSessionResponse:
    assignment = await get_assignment(db, user_id)
    resolved = agent_id or assignment.agent_id or "anian"
    if resolved not in list_agent_ids():
        resolved = "anian"
    if assignment.agent_id != resolved:
        await put_assignment(db, user_id, resolved)

    row = await _get_active_session_row(db, user_id)
    if row is None or str(row["agent_id"]) != resolved:
        if row is not None:
            await db.execute(
                text(
                    """UPDATE aibot_session SET status = 'closed', updated_at = UTC_TIMESTAMP()
                       WHERE session_id = :sid"""
                ),
                {"sid": str(row["session_id"])},
            )
        session_id = _new_id("abs")
        await db.execute(
            text(
                """INSERT INTO aibot_session
                       (session_id, user_id, agent_id, mode, status, needs_welcome)
                   VALUES (:sid, :uid, :agent_id, 'free', 'active', 1)"""
            ),
            {"sid": session_id, "uid": user_id, "agent_id": resolved},
        )
        await db.commit()
        return AibotSessionResponse(
            session_id=session_id,
            agent_id=resolved,
            status="active",
            needs_welcome=True,
            turns=[],
        )

    session_id = str(row["session_id"])
    turns = await _list_turns(db, session_id)
    needs_welcome = bool(row["needs_welcome"]) and len(turns) == 0
    return AibotSessionResponse(
        session_id=session_id,
        agent_id=str(row["agent_id"]),
        status=str(row["status"]),
        needs_welcome=needs_welcome,
        last_intent=str(row["last_intent"]) if row.get("last_intent") else None,
        turns=turns,
    )


async def _insert_turn(
    db: AsyncSession,
    *,
    session_id: str,
    role: str,
    content: str,
    intent: str | None = None,
    choices: list[str] | None = None,
    actions: list[dict[str, Any]] | None = None,
    cards: Any = None,
    idempotency_key: str | None = None,
) -> AibotTurnItem:
    turn_id = _new_id("abt")
    await db.execute(
        text(
            """INSERT INTO aibot_turn
                   (session_id, turn_id, role, content, intent, choices_json, actions_json, cards_json, idempotency_key)
               VALUES
                   (:sid, :tid, :role, :content, :intent, :choices_json, :actions_json, :cards_json, :idem)"""
        ),
        {
            "sid": session_id,
            "tid": turn_id,
            "role": role,
            "content": content,
            "intent": intent,
            "choices_json": json.dumps(choices or [], ensure_ascii=False),
            "actions_json": json.dumps(actions or [], ensure_ascii=False),
            "cards_json": json.dumps(cards, ensure_ascii=False) if cards is not None else None,
            "idem": idempotency_key,
        },
    )
    return AibotTurnItem(
        turn_id=turn_id,
        role=role,
        content=content,
        intent=intent,
        choices=choices or [],
        actions=actions or [],
        cards=cards,
        created_at=_now_iso(),
    )


async def post_welcome(db: AsyncSession, user_id: int, session_id: str) -> AibotWelcomeResponse:
    session = await ensure_session(db, user_id)
    if session.session_id != session_id:
        raise HTTPException(404, detail="会话不存在")
    existing = await _list_turns(db, session_id)
    if existing:
        await db.execute(
            text(
                """UPDATE aibot_session SET needs_welcome = 0, updated_at = UTC_TIMESTAMP()
                   WHERE session_id = :sid"""
            ),
            {"sid": session_id},
        )
        await db.commit()
        return AibotWelcomeResponse(
            session_id=session_id,
            agent_id=session.agent_id,
            messages=existing,
        )

    meta = get_agent_meta(session.agent_id)
    welcomes = {
        "anian": "我是阿念，你的专属红娘。感觉和条件我都会帮你看——今天想先聊聊，还是直接看恋爱报告？",
        "yuzhou": "我是予舟，专帮 30 内的男生把「不敢开口」练成「约得出来」。先聊你卡在哪一步？",
        "chengan": "我是承安，帮 30+ 的你少盲聊、多见面。条件先对齐——你更看重哪几条？",
        "nianchu": "我是念初，帮你把「聊得来」和「真适合」分开看。今天想先梳理边界，还是看看同频的人？",
        "wanqing": "我是晚晴，陪你把认真交往落到可见面、可谈的下一步。你更在意条件稳定，还是先对齐目标？",
    }
    hello = welcomes.get(
        session.agent_id,
        f"我是{meta['name']}，你的专属红娘。今天想先看恋爱报告，还是随便聊聊？",
    )

    fixed = await _insert_turn(
        db, session_id=session_id, role="system_fixed", content=_WELCOME_FIXED
    )
    # 开场入口由前端四宫格 guide 卡承接，不再下发底部快捷选项（避免与卡片重复）
    agent_hello = await _insert_turn(
        db,
        session_id=session_id,
        role="assistant",
        content=hello,
        intent="chat",
        choices=[],
    )
    await db.execute(
        text(
            """UPDATE aibot_session SET needs_welcome = 0, updated_at = UTC_TIMESTAMP()
               WHERE session_id = :sid"""
        ),
        {"sid": session_id},
    )
    await db.execute(
        text(
            """UPDATE aibot_user_assignment SET welcome_seen_at = UTC_TIMESTAMP()
               WHERE user_id = :uid"""
        ),
        {"uid": user_id},
    )
    await db.commit()
    return AibotWelcomeResponse(
        session_id=session_id,
        agent_id=session.agent_id,
        messages=[fixed, agent_hello],
    )


async def _generate_reply(
    db: AsyncSession,
    *,
    user_id: int,
    agent_id: str,
    session_id: str,
    user_text: str,
) -> dict[str, Any]:
    nickname = await _load_nickname(db, user_id)
    member_facts = await _load_member_facts(db, user_id)
    history_rows = (
        await db.execute(
            text(
                """SELECT role, content FROM aibot_turn
                   WHERE session_id = :sid AND role IN ('user', 'assistant')
                   ORDER BY id DESC LIMIT :lim"""
            ),
            {"sid": session_id, "lim": _MAX_CONTEXT},
        )
    ).mappings().all()
    history: list[dict[str, str]] = []
    for row in reversed(list(history_rows)):
        role = "user" if row["role"] == "user" else "assistant"
        content = str(row["content"] or "").strip()
        if content:
            history.append({"role": role, "content": content})

    # post_turn 先写入本轮 user 再调用本函数，history 末尾通常已是当前 user_text；
    # 再 append 一次会变成连续两条相同 user，DeepSeek json_object 易返回纯空白 content，
    # 上层再被映射成「AI 服务暂时不可用」。
    normalized_user = (user_text or "").strip()
    if (
        not history
        or history[-1].get("role") != "user"
        or history[-1].get("content") != normalized_user
    ):
        history.append({"role": "user", "content": normalized_user})

    messages = [
        {
            "role": "system",
            "content": build_system_prompt(agent_id, nickname, member_facts=member_facts or None),
        },
        *history,
    ]
    try:
        raw = await complete(
            messages,
            json_mode=True,
            scene="aibot_free_chat",
            feature=AiFeature.ADVISOR,
        )
        parsed = parse_json(raw)
        reply = str(parsed.get("reply") or "").strip()
        if not reply:
            raise ValueError("empty reply")
        intent = _normalize_intent(parsed.get("intent"))
        choices = _choices_from(parsed.get("choices"))
        return {"reply": reply, "intent": intent, "choices": choices}
    except HTTPException as exc:
        # 供应商空白/抖动时 complete 会 503；对用户改为兜底回复，避免整轮不可用。
        if getattr(exc, "status_code", None) == 503:
            logger.warning("aibot llm 503 fallback agent=%s", agent_id)
            return fallback_reply(agent_id, user_text)
        raise
    except Exception:
        logger.warning("aibot llm parse/fallback agent=%s", agent_id, exc_info=True)
        return fallback_reply(agent_id, user_text)


async def post_turn(
    db: AsyncSession,
    user_id: int,
    session_id: str,
    text_value: str,
    *,
    idempotency_key: str | None = None,
) -> AibotTurnResponse:
    question = (text_value or "").strip()
    if not question:
        raise HTTPException(400, detail="请输入内容")
    await assert_text_allowed(db, question, field="消息")

    session = await ensure_session(db, user_id)
    if session.session_id != session_id:
        raise HTTPException(404, detail="会话不存在")

    if idempotency_key:
        existing = (
            await db.execute(
                text(
                    """SELECT turn_id, role, content, intent, choices_json, actions_json, cards_json, created_at
                       FROM aibot_turn
                       WHERE session_id = :sid AND idempotency_key = :idem
                       LIMIT 1"""
                ),
                {"sid": session_id, "idem": idempotency_key},
            )
        ).mappings().first()
        if existing:
            # 幂等命中：找紧随其后的助手轮
            assistant = (
                await db.execute(
                    text(
                        """SELECT turn_id, role, content, intent, choices_json, actions_json, cards_json, created_at
                           FROM aibot_turn
                           WHERE session_id = :sid AND id > (
                             SELECT id FROM aibot_turn WHERE session_id = :sid AND idempotency_key = :idem LIMIT 1
                           ) AND role = 'assistant'
                           ORDER BY id ASC LIMIT 1"""
                    ),
                    {"sid": session_id, "idem": idempotency_key},
                )
            ).mappings().first()
            user_turn = AibotTurnItem(
                turn_id=str(existing["turn_id"]),
                role="user",
                content=str(existing["content"] or ""),
                created_at=_now_iso(existing.get("created_at")),
            )
            if assistant:
                choices = _choices_from(assistant.get("choices_json"))
                intent = _normalize_intent(assistant.get("intent"))
                assistant_turn = AibotTurnItem(
                    turn_id=str(assistant["turn_id"]),
                    role="assistant",
                    content=str(assistant["content"] or ""),
                    intent=intent,
                    choices=choices,
                    actions=_actions_for_intent(intent),
                    created_at=_now_iso(assistant.get("created_at")),
                )
                return AibotTurnResponse(
                    turn_id=assistant_turn.turn_id,
                    reply=assistant_turn.content,
                    intent=intent,
                    choices=choices,
                    actions=assistant_turn.actions,
                    user_turn=user_turn,
                    assistant_turn=assistant_turn,
                    session_id=session_id,
                )

    quota_key = daily_quota_key("aibot-chat", user_id)
    limit = max(int(settings.ai_daily_advisor_limit), 30)
    if not await consume_daily(quota_key, limit):
        raise HTTPException(429, detail=f"今日红娘对话已达 {limit} 次上限")

    try:
        user_turn = await _insert_turn(
            db,
            session_id=session_id,
            role="user",
            content=question,
            idempotency_key=idempotency_key,
        )
        await db.flush()
        generated = await _generate_reply(
            db,
            user_id=user_id,
            agent_id=session.agent_id,
            session_id=session_id,
            user_text=question,
        )
        intent = _normalize_intent(generated.get("intent"))
        choices = _choices_from(generated.get("choices"))
        actions = _actions_for_intent(intent)
        reply = str(generated.get("reply") or "").strip() or "我在，你继续说。"
        assistant_turn = await _insert_turn(
            db,
            session_id=session_id,
            role="assistant",
            content=reply,
            intent=intent,
            choices=choices,
            actions=actions,
        )
        await db.execute(
            text(
                """UPDATE aibot_session
                   SET last_intent = :intent, needs_welcome = 0, updated_at = UTC_TIMESTAMP()
                   WHERE session_id = :sid"""
            ),
            {"intent": intent, "sid": session_id},
        )
        await db.commit()
    except Exception:
        await db.rollback()
        try:
            await refund_daily(quota_key)
        except Exception:
            logger.warning("aibot quota refund failed", exc_info=True)
        raise

    return AibotTurnResponse(
        turn_id=assistant_turn.turn_id,
        reply=reply,
        intent=intent,
        choices=choices,
        actions=actions,
        user_turn=user_turn,
        assistant_turn=assistant_turn,
        session_id=session_id,
    )

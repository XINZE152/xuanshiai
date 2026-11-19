"""恋 AI BOT 聊天时间线：完整 UI 消息落库 + 游标分页上翻。"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 40
_MAX_LIMIT = 80


def _new_message_id() -> str:
    return "atm-" + uuid.uuid4().hex[:20]


def _now_iso(value: Any = None) -> str:
    if value is None:
        return datetime.utcnow().replace(microsecond=0).isoformat(sep=" ")
    if hasattr(value, "isoformat"):
        return value.isoformat(sep=" ", timespec="seconds")
    return str(value)


def _row_to_item(row: Any) -> dict[str, Any]:
    payload = row.get("payload_json")
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8", errors="ignore")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = None
    created = row.get("created_at")
    created_ms = 0
    if created is not None and hasattr(created, "timestamp"):
        try:
            created_ms = int(created.timestamp() * 1000)
        except Exception:
            created_ms = 0
    return {
        "id": int(row["id"]),
        "message_id": str(row["message_id"]),
        "role": str(row.get("role") or "assistant"),
        "kind": str(row.get("kind") or "text"),
        "content": str(row.get("content") or ""),
        "payload": payload if isinstance(payload, dict) else payload,
        "client_msg_id": str(row["client_msg_id"]) if row.get("client_msg_id") else None,
        "session_id": str(row["session_id"]) if row.get("session_id") else None,
        "agent_id": str(row.get("agent_id") or ""),
        "created_at": _now_iso(created),
        "created_at_ms": created_ms,
    }


async def append_timeline_message(
    db: AsyncSession,
    *,
    user_id: int,
    agent_id: str,
    role: str,
    kind: str = "text",
    content: str = "",
    payload: Any = None,
    session_id: str | None = None,
    client_msg_id: str | None = None,
    message_id: str | None = None,
) -> dict[str, Any]:
    """写入一条时间线；同一 client_msg_id 幂等返回已有行。"""
    agent = (agent_id or "").strip()[:32]
    if not agent:
        raise HTTPException(400, detail="缺少 agent_id")
    role_s = (role or "assistant").strip()[:16] or "assistant"
    kind_s = (kind or "text").strip()[:32] or "text"
    if kind_s == "date":
        # 日期条由客户端按 created_at 插入，不落库
        raise HTTPException(400, detail="日期条不落库")
    client_key = (client_msg_id or "").strip()[:128] or None
    if client_key:
        existing = (
            await db.execute(
                text(
                    """SELECT id, message_id, user_id, agent_id, session_id, role, kind,
                              content, payload_json, client_msg_id, created_at
                       FROM aibot_timeline_message
                       WHERE user_id = :uid AND agent_id = :aid AND client_msg_id = :cid
                       LIMIT 1"""
                ),
                {"uid": user_id, "aid": agent, "cid": client_key},
            )
        ).mappings().first()
        if existing:
            return _row_to_item(existing)

    mid = (message_id or "").strip()[:64] or _new_message_id()
    payload_json = None
    if payload is not None:
        try:
            payload_json = json.dumps(payload, ensure_ascii=False)
        except Exception:
            payload_json = json.dumps({"raw": str(payload)}, ensure_ascii=False)

    await db.execute(
        text(
            """INSERT INTO aibot_timeline_message
                   (message_id, user_id, agent_id, session_id, role, kind, content,
                    payload_json, client_msg_id)
               VALUES
                   (:mid, :uid, :aid, :sid, :role, :kind, :content, :payload, :cid)"""
        ),
        {
            "mid": mid,
            "uid": user_id,
            "aid": agent,
            "sid": (session_id or "").strip()[:64] or None,
            "role": role_s,
            "kind": kind_s,
            "content": (content or "")[:8000],
            "payload": payload_json,
            "cid": client_key,
        },
    )
    row = (
        await db.execute(
            text(
                """SELECT id, message_id, user_id, agent_id, session_id, role, kind,
                          content, payload_json, client_msg_id, created_at
                   FROM aibot_timeline_message
                   WHERE message_id = :mid LIMIT 1"""
            ),
            {"mid": mid},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(500, detail="时间线写入失败")
    return _row_to_item(row)


async def list_timeline(
    db: AsyncSession,
    *,
    user_id: int,
    agent_id: str,
    before_id: int | None = None,
    limit: int = _DEFAULT_LIMIT,
) -> dict[str, Any]:
    """按 id 倒序取一页，返回时间正序 items + has_more。"""
    agent = (agent_id or "").strip()[:32]
    if not agent:
        raise HTTPException(400, detail="缺少 agent_id")
    lim = max(1, min(int(limit or _DEFAULT_LIMIT), _MAX_LIMIT))
    params: dict[str, Any] = {"uid": user_id, "aid": agent, "lim": lim + 1}
    where = "user_id = :uid AND agent_id = :aid"
    if before_id is not None and int(before_id) > 0:
        where += " AND id < :before_id"
        params["before_id"] = int(before_id)

    rows = (
        await db.execute(
            text(
                f"""SELECT id, message_id, user_id, agent_id, session_id, role, kind,
                           content, payload_json, client_msg_id, created_at
                    FROM aibot_timeline_message
                    WHERE {where}
                    ORDER BY id DESC
                    LIMIT :lim"""
            ),
            params,
        )
    ).mappings().all()
    has_more = len(rows) > lim
    page = list(rows[:lim])
    page.reverse()  # 时间正序给前端
    items = [_row_to_item(r) for r in page]
    oldest_id = int(items[0]["id"]) if items else None
    newest_id = int(items[-1]["id"]) if items else None
    return {
        "items": items,
        "has_more": has_more,
        "oldest_id": oldest_id,
        "newest_id": newest_id,
        "limit": lim,
    }


async def append_text_pair_from_turn(
    db: AsyncSession,
    *,
    user_id: int,
    agent_id: str,
    session_id: str,
    user_content: str,
    assistant_content: str,
    user_turn_id: str | None = None,
    assistant_turn_id: str | None = None,
    intent: str | None = None,
    choices: list[str] | None = None,
) -> None:
    """自由聊一轮：user + assistant 文本同步进时间线（失败不影响主流程）。"""
    try:
        await append_timeline_message(
            db,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            role="user",
            kind="text",
            content=user_content,
            client_msg_id=("turn-u-" + (user_turn_id or uuid.uuid4().hex[:12])),
            payload={"source": "aibot_turn"},
        )
        await append_timeline_message(
            db,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            role="assistant",
            kind="text",
            content=assistant_content,
            client_msg_id=("turn-a-" + (assistant_turn_id or uuid.uuid4().hex[:12])),
            payload={
                "source": "aibot_turn",
                "intent": intent,
                "choices": choices or [],
            },
        )
    except Exception:
        logger.warning("aibot timeline turn sync skipped", exc_info=True)

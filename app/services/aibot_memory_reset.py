"""测试期：按红娘清零 BOT 记忆（时间线 / 会话 / 报告 / bot_intake 桶）。

关 ``aibot_allow_memory_reset`` 后接口直接 403，便于上线前关掉。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.aibot_profile_sync import (
    allow_memory_reset,
    clear_bot_intake_for_agent,
)
from app.services.aibot_prompts import list_agent_ids

logger = logging.getLogger(__name__)


async def reset_agent_memory(
    db: AsyncSession,
    user_id: int,
    agent_id: str,
) -> dict[str, Any]:
    if not allow_memory_reset():
        raise HTTPException(403, detail="记忆清零已关闭")
    aid = (agent_id or "").strip()
    if aid not in list_agent_ids():
        raise HTTPException(400, detail="未知红娘")

    deleted: dict[str, int] = {
        "timeline": 0,
        "turns": 0,
        "sessions": 0,
        "reports": 0,
    }

    # 1) 时间线
    r = await db.execute(
        text(
            """DELETE FROM aibot_timeline_message
               WHERE user_id = :uid AND agent_id = :aid"""
        ),
        {"uid": user_id, "aid": aid},
    )
    deleted["timeline"] = int(r.rowcount or 0)

    # 2) 会话 turns + 关 session
    session_rows = (
        await db.execute(
            text(
                """SELECT session_id FROM aibot_session
                   WHERE user_id = :uid AND agent_id = :aid"""
            ),
            {"uid": user_id, "aid": aid},
        )
    ).mappings().all()
    for row in session_rows:
        sid = str(row["session_id"])
        tr = await db.execute(
            text("DELETE FROM aibot_turn WHERE session_id = :sid"),
            {"sid": sid},
        )
        deleted["turns"] += int(tr.rowcount or 0)
    sr = await db.execute(
        text(
            """UPDATE aibot_session
               SET status = 'closed', needs_welcome = 1, updated_at = UTC_TIMESTAMP()
               WHERE user_id = :uid AND agent_id = :aid"""
        ),
        {"uid": user_id, "aid": aid},
    )
    deleted["sessions"] = int(sr.rowcount or 0)

    # 3) 报告
    rr = await db.execute(
        text(
            """DELETE FROM aibot_love_report
               WHERE user_id = :uid AND agent_id = :aid"""
        ),
        {"uid": user_id, "aid": aid},
    )
    deleted["reports"] = int(rr.rowcount or 0)

    # 4) bot_intake 桶
    bot_row = (
        await db.execute(
            text("SELECT bot_intake_json FROM user_profile WHERE user_id = :uid"),
            {"uid": user_id},
        )
    ).mappings().first()
    existing = bot_row["bot_intake_json"] if bot_row else None
    cleared = clear_bot_intake_for_agent(existing, aid)
    await db.execute(
        text(
            """UPDATE user_profile
               SET bot_intake_json = :bot
               WHERE user_id = :uid"""
        ),
        {"uid": user_id, "bot": json.dumps(cleared, ensure_ascii=False)},
    )

    await db.commit()
    logger.info(
        "aibot memory reset user=%s agent=%s deleted=%s",
        user_id,
        aid,
        deleted,
    )
    return {
        "agent_id": aid,
        "ok": True,
        "deleted": deleted,
        "message": "已清空该红娘侧记忆，可重新开始",
    }

"""Reusable service-layer helpers for the Moxiang realtime protocol.

This module deliberately has no import from ``app.api.routes``.  WebSocket
routes provide the transport object, while the bridge and route share the same
safe serialization and journey/history projections.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

from sqlalchemy import text as sql_text

from app.db.session import session_factory as default_session_factory
from app.services.ai.journey import compose_journey_build_context

logger = logging.getLogger(__name__)


async def send_json(ws: Any, message: Mapping[str, Any]) -> None:
    """Send one JSON message and tolerate a peer that already disconnected."""
    try:
        await ws.send_text(json.dumps(dict(message), ensure_ascii=False))
    except Exception:  # noqa: BLE001
        logger.debug("moxiang_ws_send_failed: connection likely closed")


async def build_journey_context(
    session_id: str,
    subject: str,
    *,
    session_factory: Any = default_session_factory,
) -> str | None:
    """Project persisted candidates into safe build-mode context."""
    if not session_id or session_factory is None:
        return None
    try:
        async with session_factory() as db:
            return await compose_journey_build_context(
                db, session_id=session_id, subject=subject
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "moxiang_build_context_failed session_id=%s err=%s",
            session_id,
            type(exc).__name__,
        )
        return None


async def load_master_history(
    db: Any,
    session_id: str,
    *,
    limit: int = 24,
) -> list[dict[str, str]]:
    """Load recent persisted dialogue in chronological order."""
    result = await db.execute(
        sql_text(
            "SELECT role, answer_text FROM ai_profile_turn "
            "WHERE session_id = :session_id AND status = 'saved' "
            "ORDER BY turn_no DESC LIMIT :limit"
        ),
        {"session_id": session_id, "limit": limit},
    )
    rows = list(result.mappings().all())
    rows.reverse()
    return [
        {
            "role": str(row.get("role") or ""),
            "content": str(row.get("answer_text") or ""),
        }
        for row in rows
        if str(row.get("role") or "") in {"user", "assistant"}
        and str(row.get("answer_text") or "").strip()
    ]


__all__ = ["build_journey_context", "load_master_history", "send_json"]

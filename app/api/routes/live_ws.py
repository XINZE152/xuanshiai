"""直播相亲业务事件 WebSocket。"""

import asyncio

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from redis.exceptions import RedisError
from sqlalchemy import text

from app.core.redis import redis_client
from app.core.security import decode_access_token
from app.db.session import session_factory

router = APIRouter(prefix="/live")


@router.websocket("/sessions/{session_id}/events")
async def live_events(websocket: WebSocket, session_id: int, token: str = Query(...)) -> None:
    try:
        payload = decode_access_token(token)
        user_id = int(payload["sub"])
    except (ValueError, KeyError):
        await websocket.close(code=1008, reason="invalid token")
        return
    if session_factory is None:
        await websocket.close(code=1011, reason="database unavailable")
        return
    async with session_factory() as db:
        access = await db.execute(
            text(
                "SELECT s.id,s.status,s.state_version FROM live_session s "
                "LEFT JOIN live_registration r "
                "ON r.session_id=s.id AND r.user_id=:uid "
                "AND r.status IN ('RESERVED','CHECKED_IN') "
                "LEFT JOIN live_session_role role ON role.session_id=s.id "
                "AND role.user_id=:uid AND role.status=1 "
                "WHERE s.id=:sid AND (r.id IS NOT NULL OR role.id IS NOT NULL) LIMIT 1"
            ),
            {"sid": session_id, "uid": user_id},
        )
        session = access.mappings().first()
        if not session:
            await websocket.close(code=1008, reason="session access denied")
            return
        seats = (
            await db.execute(
                text(
                    "SELECT seat_no,user_id,status FROM live_stage_seat "
                    "WHERE session_id=:sid ORDER BY seat_no"
                ),
                {"sid": session_id},
            )
        ).mappings().all()
    await websocket.accept()
    await websocket.send_json({"event_type": "connection.ready", "session_id": session_id, "user_id": user_id})
    await websocket.send_json(
        {
            "event_type": "session.snapshot",
            "session_id": session_id,
            "version": int(session["state_version"]),
            "payload": {
                "status": session["status"],
                "seats": [dict(seat) for seat in seats],
            },
        }
    )
    pubsub = redis_client.pubsub()
    try:
        await pubsub.subscribe(f"live:session:{session_id}")
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=20)
            if message and message.get("data"):
                await websocket.send_text(str(message["data"]))
            else:
                await websocket.send_json({"event_type": "heartbeat", "session_id": session_id})
            await asyncio.sleep(0.05)
    except (RedisError, WebSocketDisconnect):
        return
    finally:
        await pubsub.unsubscribe(f"live:session:{session_id}")
        await pubsub.aclose()

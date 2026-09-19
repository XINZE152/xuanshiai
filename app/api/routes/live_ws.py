"""直播相亲业务事件 WebSocket。"""

import asyncio

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from redis.exceptions import RedisError
from sqlalchemy import text

from app.core.redis import consume_once, redis_client
from app.core.security import decode_live_ws_ticket
from app.db.session import session_factory
from app.services import live as service
from app.services.live import list_events_after

router = APIRouter(prefix="/live")


@router.websocket("/sessions/{session_id}/events")
async def live_events(
    websocket: WebSocket,
    session_id: int,
    ticket: str = Query(...),
    last_version: int = Query(0, ge=0),
) -> None:
    try:
        payload = decode_live_ws_ticket(ticket)
        user_id = int(payload["sub"])
        ticket_session_id = int(payload["sid"])
        ticket_id = payload["jti"]
    except (ValueError, KeyError):
        await websocket.close(code=1008, reason="invalid ticket")
        return
    if ticket_session_id != session_id:
        await websocket.close(code=1008, reason="ticket session mismatch")
        return
    try:
        if not await consume_once(f"live:ws-ticket:{ticket_id}", 60):
            await websocket.close(code=1008, reason="ticket already used")
            return
    except Exception:
        await websocket.close(code=1011, reason="ticket service unavailable")
        return
    if session_factory is None:
        await websocket.close(code=1011, reason="database unavailable")
        return
    async with session_factory() as db:
        try:
            session = await service.ensure_live_event_access(db, session_id, user_id)
        except HTTPException:
            await websocket.close(code=1008, reason="session access denied")
            return
        if not session:
            return
        pubsub = redis_client.pubsub()
        try:
            await pubsub.subscribe(f"live:session:{session_id}")
        except RedisError:
            await pubsub.aclose()
            await websocket.close(code=1011, reason="realtime service unavailable")
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
        missed_events = (
            await list_events_after(db, session_id, last_version, 200)
            if last_version > 0
            else []
        )
    await websocket.accept()
    await websocket.send_json({"event_type": "connection.ready", "session_id": session_id, "user_id": user_id})
    for event in missed_events:
        event["replayed"] = True
        await websocket.send_json(event)
    await websocket.send_json(
        {
            "event_type": "session.snapshot",
            "session_id": session_id,
            "state_version": int(session["state_version"]),
            "event_version": (
                missed_events[-1]["event_version"] if missed_events else last_version
            ),
            "payload": {
                "status": session["status"],
                "seats": [dict(seat) for seat in seats],
            },
        }
    )
    try:
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=20)
            if message and message.get("data"):
                await websocket.send_text(str(message["data"]))
            else:
                await websocket.send_json({"event_type": "heartbeat", "session_id": session_id})
            async with session_factory() as check_db:
                current = await check_db.execute(
                    text(
                        "SELECT 1 FROM user_session WHERE id=:sid AND user_id=:uid AND status=1 "
                        "AND revoked_at IS NULL AND access_expire_at>UTC_TIMESTAMP()"
                    ),
                    {"uid": user_id, "sid": ticket_session_id},
                )
                session_is_valid = bool(current.scalar())
            if not session_is_valid:
                await websocket.close(code=1008, reason="session revoked")
                return
            await asyncio.sleep(0.05)
    except (RedisError, WebSocketDisconnect):
        return
    finally:
        await pubsub.unsubscribe(f"live:session:{session_id}")
        await pubsub.aclose()

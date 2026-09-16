"""Tencent live callback endpoint."""

import hashlib
import hmac
import json
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db

router = APIRouter(prefix="/live/callbacks")


@router.post("/tencent", include_in_schema=False)
async def tencent_callback(
    request: Request,
    x_tencent_signature: str | None = Header(None, alias="X-Tencent-Signature"),
    x_tencent_timestamp: str | None = Header(None, alias="X-Tencent-Timestamp"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    """Verify and accept a provider callback without logging its payload."""
    secret = settings.tencent_live_callback_secret
    if secret is None:
        raise HTTPException(503, detail="Tencent live callback is not configured")
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            declared_length = int(content_length)
        except ValueError as exc:
            raise HTTPException(422, detail="Invalid Content-Length") from exc
        if declared_length > settings.tencent_live_callback_max_body_bytes:
            raise HTTPException(413, detail="Tencent callback payload is too large")
    raw_body = await request.body()
    if len(raw_body) > settings.tencent_live_callback_max_body_bytes:
        raise HTTPException(413, detail="Tencent callback payload is too large")
    try:
        callback_timestamp = int(x_tencent_timestamp or "")
    except ValueError as exc:
        raise HTTPException(401, detail="Invalid Tencent callback timestamp") from exc
    if abs(int(time.time()) - callback_timestamp) > settings.tencent_live_callback_max_skew_seconds:
        raise HTTPException(401, detail="Expired Tencent callback timestamp")
    expected = hmac.new(
        secret.get_secret_value().encode(),
        x_tencent_timestamp.encode() + b"." + raw_body,
        hashlib.sha256,
    ).hexdigest()
    if not x_tencent_signature or not hmac.compare_digest(expected, x_tencent_signature):
        raise HTTPException(401, detail="Invalid Tencent callback signature")
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(422, detail="Invalid Tencent callback payload") from exc
    event_id = payload.get("EventId") or payload.get("event_id")
    if not event_id:
        raise HTTPException(422, detail="Tencent callback event id is required")
    event_type = payload.get("EventType") or payload.get("event_type")
    allowed_event_types = {
        item.strip()
        for item in settings.tencent_live_callback_event_types_raw.split(",")
        if item.strip()
    }
    if not event_type or str(event_type) not in allowed_event_types:
        raise HTTPException(422, detail="Tencent callback event type is not allowed")
    raw_session_id = payload.get("RoomId") or payload.get("room_id")
    try:
        session_id = int(raw_session_id) if raw_session_id is not None else None
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, detail="Invalid Tencent callback room id") from exc
    payload_hash = hashlib.sha256(raw_body).hexdigest()
    try:
        await db.execute(
            text(
                "INSERT INTO live_provider_event "
                "(provider,provider_event_id,event_type,session_id,payload_hash,event_time,status) "
                "VALUES ('TENCENT',:event_id,:event_type,:session_id,:payload_hash,"
                "FROM_UNIXTIME(:event_time),'RECEIVED')"
            ),
            {
                "event_id": str(event_id),
                "event_type": str(event_type) if event_type is not None else None,
                "session_id": session_id,
                "payload_hash": payload_hash,
                "event_time": callback_timestamp,
            },
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing_hash = (
            await db.execute(
                text(
                    "SELECT payload_hash FROM live_provider_event "
                    "WHERE provider='TENCENT' AND provider_event_id=:event_id"
                ),
                {"event_id": str(event_id)},
            )
        ).scalar()
        if existing_hash != payload_hash:
            raise HTTPException(409, detail="Tencent callback event id conflict")
    return {"accepted": True}

"""直播相亲核心领域服务。"""

import json
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser
from app.core.redis import redis_client
from app.schemas.live import LiveSessionCreate
from app.services.content_filter import assert_text_allowed
from app.services.restrictions import ensure_user_allowed


TRANSITIONS = {
    "DRAFT": {"SCHEDULED"}, "SCHEDULED": {"CHECK_IN", "CLOSED"},
    "CHECK_IN": {"WARMUP", "CLOSED"}, "WARMUP": {"OPEN", "CLOSED"},
    "OPEN": {"INTRO", "CLOSED"}, "INTRO": {"MATCHMAKER_QA", "CLOSED"},
    "MATCHMAKER_QA": {"HEART_LIGHT", "CLOSED"}, "HEART_LIGHT": {"SELECT", "CLOSED"},
    "SELECT": {"GUIDED_EXCHANGE", "CLOSED"}, "GUIDED_EXCHANGE": {"DOUBLE_CONFIRM", "CLOSED"},
    "DOUBLE_CONFIRM": {"APPLY_KNOW", "CLOSED"}, "APPLY_KNOW": {"CLOSED"}, "CLOSED": set(),
}

INTERACTION_STATUS = {
    "HEART_LIGHT": "HEART_LIGHT",
    "SELECT": "SELECT",
    "CONFIRM": "DOUBLE_CONFIRM",
}


def _session(row: dict) -> dict:
    result = dict(row)
    result["recording_enabled"] = bool(result["recording_enabled"])
    return result


async def publish_event(session_id: int, event_type: str, version: int, payload: dict) -> None:
    message = json.dumps(
        {
            "event_id": uuid4().hex,
            "session_id": session_id,
            "version": version,
            "event_type": event_type,
            "occurred_at": datetime.now(UTC).isoformat(),
            "payload": payload,
        },
        ensure_ascii=False,
        default=str,
    )
    try:
        await redis_client.publish(f"live:session:{session_id}", message)
    except RedisError:
        return


async def get_session(db: AsyncSession, session_id: int, *, lock: bool = False) -> dict:
    suffix = " FOR UPDATE" if lock else ""
    row = (await db.execute(text("SELECT * FROM live_session WHERE id = :id" + suffix), {"id": session_id})).mappings().first()
    if not row:
        raise HTTPException(404, detail="直播场次不存在")
    return _session(dict(row))


async def list_sessions(db: AsyncSession, status: str | None) -> dict:
    where = "WHERE status = :status" if status else "WHERE status <> 'DRAFT'"
    params = {"status": status} if status else {}
    rows = (await db.execute(text(f"SELECT * FROM live_session {where} ORDER BY scheduled_at DESC LIMIT 100"), params)).mappings().all()
    total = (await db.execute(text(f"SELECT COUNT(*) FROM live_session {where}"), params)).scalar_one()
    return {"items": [_session(dict(row)) for row in rows], "total": int(total)}


async def create_session(db: AsyncSession, actor_id: int, body: LiveSessionCreate) -> dict:
    host_exists = (
        await db.execute(text("SELECT 1 FROM users WHERE id=:id AND status=1"), {"id": body.host_user_id})
    ).scalar()
    if not host_exists:
        raise HTTPException(422, detail="主持人不存在或账号不可用")
    result = await db.execute(text("""INSERT INTO live_session
        (title, city_code, scheduled_at, host_user_id, max_stage_seats, recording_enabled, rules_text, created_by)
        VALUES (:title, :city_code, :scheduled_at, :host_user_id, :max_stage_seats, :recording_enabled, :rules_text, :actor)"""),
        {**body.model_dump(), "recording_enabled": int(body.recording_enabled), "actor": actor_id})
    session_id = int(result.lastrowid)
    await db.execute(text("INSERT INTO live_session_role (session_id, user_id, role_code) VALUES (:sid, :uid, 'HOST')"), {"sid": session_id, "uid": body.host_user_id})
    for seat_no in range(1, body.max_stage_seats + 1):
        await db.execute(text("INSERT INTO live_stage_seat (session_id, seat_no) VALUES (:sid, :seat)"), {"sid": session_id, "seat": seat_no})
    await db.execute(text("INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id) VALUES (:actor, 'live.session.create', 'live_session', :sid)"), {"actor": actor_id, "sid": session_id})
    await db.commit()
    return await get_session(db, session_id)


async def require_controller(db: AsyncSession, session_id: int, user_id: int) -> dict:
    session = await get_session(db, session_id)
    if session["host_user_id"] == user_id:
        return session
    role = (await db.execute(text("SELECT 1 FROM live_session_role WHERE session_id=:sid AND user_id=:uid AND role_code IN ('HOST','MATCHMAKER','MODERATOR') AND status=1"), {"sid": session_id, "uid": user_id})).scalar()
    if not role:
        raise HTTPException(403, detail="没有该场次控制权限")
    return session


async def assign_role(
    db: AsyncSession,
    session_id: int,
    actor_id: int,
    user_id: int,
    role_code: str,
    enabled: bool,
) -> dict:
    await get_session(db, session_id)
    user_exists = (
        await db.execute(text("SELECT 1 FROM users WHERE id=:id AND status=1"), {"id": user_id})
    ).scalar()
    if not user_exists:
        raise HTTPException(422, detail="用户不存在或账号不可用")
    await db.execute(
        text(
            "INSERT INTO live_session_role (session_id,user_id,role_code,status) "
            "VALUES (:sid,:uid,:role,:status) ON DUPLICATE KEY UPDATE status=:status"
        ),
        {"sid": session_id, "uid": user_id, "role": role_code, "status": int(enabled)},
    )
    await db.execute(
        text(
            "INSERT INTO business_audit_log "
            "(actor_user_id,action,resource_type,resource_id,reason) "
            "VALUES (:actor,'live.role.assign','live_session',:sid,:reason)"
        ),
        {
            "actor": actor_id,
            "sid": session_id,
            "reason": f"{role_code}:{user_id}:{'enabled' if enabled else 'disabled'}",
        },
    )
    await db.commit()
    return {
        "session_id": session_id,
        "user_id": user_id,
        "role_code": role_code,
        "enabled": enabled,
    }


async def transition_session(db: AsyncSession, session_id: int, actor_id: int, to_status: str, expected_version: int, reason: str | None) -> dict:
    await require_controller(db, session_id, actor_id)
    session = await get_session(db, session_id, lock=True)
    if session["state_version"] != expected_version:
        await db.rollback()
        raise HTTPException(409, detail="场次状态版本已变化，请刷新后重试")
    if to_status not in TRANSITIONS.get(session["status"], set()):
        await db.rollback()
        raise HTTPException(409, detail=f"不允许从 {session['status']} 迁移到 {to_status}")
    version = expected_version + 1
    updated = await db.execute(text("UPDATE live_session SET status=:status, state_version=:version, closed_at=CASE WHEN :status='CLOSED' THEN UTC_TIMESTAMP() ELSE closed_at END WHERE id=:id AND state_version=:expected"), {"status": to_status, "version": version, "id": session_id, "expected": expected_version})
    if updated.rowcount != 1:
        await db.rollback()
        raise HTTPException(409, detail="场次状态版本已变化，请刷新后重试")
    await db.execute(text("INSERT INTO live_state_transition (session_id, from_status, to_status, state_version, actor_user_id, reason) VALUES (:sid,:old,:new,:version,:actor,:reason)"), {"sid": session_id, "old": session["status"], "new": to_status, "version": version, "actor": actor_id, "reason": reason})
    await db.commit()
    await publish_event(session_id, "session.status_changed", version, {"from_status": session["status"], "to_status": to_status})
    return await get_session(db, session_id)


async def reserve(db: AsyncSession, session_id: int, current: CurrentUser) -> dict:
    session = await get_session(db, session_id)
    if session["status"] not in {"SCHEDULED", "CHECK_IN"}:
        raise HTTPException(409, detail="当前场次不可预约")
    if current.realname_status != 2 or current.face_verified != 1:
        raise HTTPException(403, detail="请先完成实名认证和人脸认证")
    adult = (
        await db.execute(
            text(
                "SELECT 1 FROM users WHERE id=:id AND birthday IS NOT NULL "
                "AND TIMESTAMPDIFF(YEAR,birthday,CURDATE())>=18"
            ),
            {"id": current.id},
        )
    ).scalar()
    if not adult:
        raise HTTPException(403, detail="直播相亲仅对已满 18 周岁的用户开放")
    await ensure_user_allowed(db, current.id, "TOTAL_BAN")
    await db.execute(text("""INSERT INTO live_registration (session_id,user_id,status) VALUES (:sid,:uid,'RESERVED')
        ON DUPLICATE KEY UPDATE status=IF(status='CANCELLED','RESERVED',status), updated_at=UTC_TIMESTAMP()"""), {"sid": session_id, "uid": current.id})
    await db.commit()
    return await registration(db, session_id, current.id)


async def registration(db: AsyncSession, session_id: int, user_id: int) -> dict:
    row = (await db.execute(text("SELECT * FROM live_registration WHERE session_id=:sid AND user_id=:uid"), {"sid": session_id, "uid": user_id})).mappings().first()
    if not row:
        raise HTTPException(404, detail="尚未预约该场次")
    result = dict(row)
    result["device_check_passed"] = bool(result["device_check_passed"])
    return result


async def update_registration(db: AsyncSession, session_id: int, user_id: int, action: str, passed: bool | None = None) -> dict:
    session = await get_session(db, session_id)
    current = await registration(db, session_id, user_id)
    if action == "cancel":
        if current["status"] == "CHECKED_IN":
            raise HTTPException(409, detail="已签到用户请通过离场流程退出")
        sql = "UPDATE live_registration SET status='CANCELLED' WHERE session_id=:sid AND user_id=:uid"
    elif action == "device":
        if current["status"] == "CANCELLED":
            raise HTTPException(409, detail="预约已取消")
        sql = "UPDATE live_registration SET device_check_passed=:passed WHERE session_id=:sid AND user_id=:uid"
    else:
        if session["status"] != "CHECK_IN":
            raise HTTPException(409, detail="当前不在签到环节")
        if current["status"] == "CANCELLED":
            raise HTTPException(409, detail="预约已取消")
        if not current["device_check_passed"]:
            raise HTTPException(409, detail="请先通过设备检测")
        sql = "UPDATE live_registration SET status='CHECKED_IN', checked_in_at=UTC_TIMESTAMP() WHERE session_id=:sid AND user_id=:uid"
    await db.execute(text(sql), {"sid": session_id, "uid": user_id, "passed": int(bool(passed))})
    await db.commit()
    return await registration(db, session_id, user_id)


async def invite(db: AsyncSession, session_id: int, actor_id: int, user_id: int, seat_no: int) -> dict:
    session = await require_controller(db, session_id, actor_id)
    if session["status"] not in {"WARMUP", "OPEN", "INTRO", "MATCHMAKER_QA", "HEART_LIGHT", "SELECT", "GUIDED_EXCHANGE", "DOUBLE_CONFIRM"}:
        raise HTTPException(409, detail="当前场次状态不可邀请上台")
    reg = await registration(db, session_id, user_id)
    if reg["status"] != "CHECKED_IN" or not reg["device_check_passed"]:
        raise HTTPException(409, detail="用户未签到或设备检测未通过")
    occupied = (
        await db.execute(
            text(
                "SELECT 1 FROM live_stage_seat WHERE session_id=:sid AND user_id=:uid "
                "AND status IN ('INVITED','ON_STAGE') LIMIT 1 FOR UPDATE"
            ),
            {"sid": session_id, "uid": user_id},
        )
    ).scalar()
    if occupied:
        raise HTTPException(409, detail="用户已有有效邀请或已在台上")
    token = uuid4().hex
    result = await db.execute(text("""UPDATE live_stage_seat SET user_id=:uid,status='INVITED',invited_by=:actor,
        invitation_token=:token,invitation_expires_at=DATE_ADD(UTC_TIMESTAMP(), INTERVAL 60 SECOND),left_at=NULL
        WHERE session_id=:sid AND seat_no=:seat AND status='EMPTY'"""), {"uid": user_id, "actor": actor_id, "token": token, "sid": session_id, "seat": seat_no})
    if result.rowcount != 1:
        await db.rollback()
        raise HTTPException(409, detail="席位不可用")
    await db.commit()
    row = (await db.execute(text("SELECT session_id,seat_no,user_id,status,invitation_token,invitation_expires_at FROM live_stage_seat WHERE session_id=:sid AND seat_no=:seat"), {"sid": session_id, "seat": seat_no})).mappings().one()
    await publish_event(
        session_id,
        "seat.invited",
        session["state_version"],
        {"seat_no": seat_no, "user_id": user_id},
    )
    return dict(row)


async def decide_invite(db: AsyncSession, session_id: int, user_id: int, token: str, decision: str) -> dict:
    session = await get_session(db, session_id)
    if session["status"] in {"DRAFT", "SCHEDULED", "CHECK_IN", "CLOSED"}:
        raise HTTPException(409, detail="当前场次状态不可响应上台邀请")
    status = "ON_STAGE" if decision == "accept" else "EMPTY"
    result = await db.execute(text("""UPDATE live_stage_seat SET status=:status,
        joined_at=CASE WHEN :status='ON_STAGE' THEN UTC_TIMESTAMP() ELSE joined_at END,
        user_id=CASE WHEN :status='EMPTY' THEN NULL ELSE user_id END, invitation_token=NULL, invitation_expires_at=NULL
        WHERE session_id=:sid AND user_id=:uid AND invitation_token=:token AND status='INVITED' AND invitation_expires_at>UTC_TIMESTAMP()"""), {"status": status, "sid": session_id, "uid": user_id, "token": token})
    if result.rowcount != 1:
        await db.rollback()
        raise HTTPException(409, detail="邀请无效或已过期")
    await db.commit()
    await publish_event(
        session_id,
        "seat.joined" if decision == "accept" else "seat.rejected",
        session["state_version"],
        {"user_id": user_id},
    )
    return {"session_id": session_id, "user_id": user_id, "status": status}


async def leave_stage(db: AsyncSession, session_id: int, user_id: int) -> dict:
    session = await get_session(db, session_id)
    result = await db.execute(
        text(
            "UPDATE live_stage_seat SET status='EMPTY',user_id=NULL,left_at=UTC_TIMESTAMP(),"
            "invitation_token=NULL,invitation_expires_at=NULL WHERE session_id=:sid "
            "AND user_id=:uid AND status='ON_STAGE'"
        ),
        {"sid": session_id, "uid": user_id},
    )
    if result.rowcount != 1:
        await db.rollback()
        raise HTTPException(409, detail="当前用户不在直播舞台")
    await db.commit()
    await publish_event(
        session_id, "seat.left", session["state_version"], {"user_id": user_id}
    )
    return {"session_id": session_id, "user_id": user_id, "status": "LEFT"}


async def remove_from_stage(
    db: AsyncSession,
    session_id: int,
    actor_id: int,
    user_id: int,
    reason: str,
) -> dict:
    session = await require_controller(db, session_id, actor_id)
    result = await db.execute(
        text(
            "UPDATE live_stage_seat SET status='EMPTY',user_id=NULL,left_at=UTC_TIMESTAMP(),"
            "invitation_token=NULL,invitation_expires_at=NULL WHERE session_id=:sid "
            "AND user_id=:uid AND status IN ('INVITED','ON_STAGE')"
        ),
        {"sid": session_id, "uid": user_id},
    )
    if result.rowcount != 1:
        await db.rollback()
        raise HTTPException(409, detail="目标用户没有有效邀请且不在舞台")
    await db.execute(
        text(
            "INSERT INTO live_moderation_action "
            "(session_id,actor_user_id,target_user_id,action_type,reason) "
            "VALUES (:sid,:actor,:target,'REMOVE_FROM_STAGE',:reason)"
        ),
        {"sid": session_id, "actor": actor_id, "target": user_id, "reason": reason},
    )
    await db.commit()
    await publish_event(
        session_id,
        "moderation.user_removed",
        session["state_version"],
        {"user_id": user_id, "reason": reason},
    )
    return {"session_id": session_id, "user_id": user_id, "status": "REMOVED"}


async def create_interaction(db: AsyncSession, session_id: int, user_id: int, target_id: int, kind: str, key: str) -> dict:
    session = await get_session(db, session_id)
    required = INTERACTION_STATUS[kind]
    if session["status"] != required:
        raise HTTPException(409, detail="当前环节不允许该操作")
    if user_id == target_id:
        raise HTTPException(422, detail="不能选择自己")
    participants = (
        await db.execute(
            text(
                "SELECT user_id FROM live_stage_seat WHERE session_id=:sid "
                "AND status='ON_STAGE' AND user_id IN (:actor,:target)"
            ),
            {"sid": session_id, "actor": user_id, "target": target_id},
        )
    ).scalars().all()
    if set(map(int, participants)) != {user_id, target_id}:
        raise HTTPException(403, detail="互动双方必须均在当前直播舞台")
    if kind in {"SELECT", "CONFIRM"}:
        prerequisite = "HEART_LIGHT" if kind == "SELECT" else "SELECT"
        existing = (
            await db.execute(
                text(
                    "SELECT 1 FROM live_interaction WHERE session_id=:sid "
                    "AND actor_user_id=:actor AND target_user_id=:target "
                    "AND interaction_type=:kind AND status='ACTIVE' LIMIT 1"
                ),
                {"sid": session_id, "actor": user_id, "target": target_id, "kind": prerequisite},
            )
        ).scalar()
        if not existing:
            raise HTTPException(409, detail=f"请先完成 {prerequisite} 操作")
    try:
        result = await db.execute(text("INSERT INTO live_interaction (session_id,actor_user_id,target_user_id,interaction_type,idempotency_key) VALUES (:sid,:uid,:target,:kind,:key)"), {"sid": session_id, "uid": user_id, "target": target_id, "kind": kind, "key": key})
        interaction_id = int(result.lastrowid)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = (await db.execute(text("SELECT * FROM live_interaction WHERE actor_user_id=:uid AND idempotency_key=:key"), {"uid": user_id, "key": key})).mappings().first()
        if not existing or int(existing["session_id"]) != session_id or int(existing["target_user_id"]) != target_id or existing["interaction_type"] != kind:
            raise HTTPException(409, detail="Idempotency-Key 已用于其他操作")
        return dict(existing)
    row = (await db.execute(text("SELECT * FROM live_interaction WHERE id=:id"), {"id": interaction_id})).mappings().one()
    await publish_event(session_id, "interaction.created", session["state_version"], {"interaction_type": kind, "actor_user_id": user_id, "target_user_id": target_id})
    return dict(row)


async def create_report(db: AsyncSession, session_id: int, user_id: int, target_id: int, category: str, description: str | None) -> dict:
    await get_session(db, session_id)
    if user_id == target_id:
        raise HTTPException(422, detail="不能举报自己")
    await assert_text_allowed(db, description, field="举报说明")
    result = await db.execute(text("INSERT INTO live_report (session_id,reporter_user_id,target_user_id,category,description) VALUES (:sid,:uid,:target,:category,:description)"), {"sid": session_id, "uid": user_id, "target": target_id, "category": category, "description": description})
    await db.commit()
    return {"id": int(result.lastrowid), "status": "PENDING"}

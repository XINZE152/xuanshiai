"""直播相亲用户端接口。"""

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, get_current_user, get_realname_verified_user
from app.db.session import get_db
from app.schemas.live import (
    LiveActionResponse,
    LiveDeviceCheckRequest,
    LiveInteractionRequest,
    LiveInteractionResponse,
    LiveInviteDecision,
    LiveRegistrationResponse,
    LiveReportRequest,
    LiveReportResponse,
    LiveRtcTicketResponse,
    LiveSessionPage,
    LiveSessionResponse,
    LiveStatus,
)
from app.services import live as service
from app.services.live_tencent import LiveProviderUnavailable, generate_user_sig

router = APIRouter(prefix="/live")


@router.get("/sessions", response_model=LiveSessionPage, summary="查询直播场次")
async def sessions(status: LiveStatus | None = Query(None), db: AsyncSession = Depends(get_db)) -> LiveSessionPage:
    return LiveSessionPage(**await service.list_sessions(db, status))


@router.get("/sessions/{session_id}", response_model=LiveSessionResponse, summary="查询直播场次详情")
async def session_detail(session_id: int = Path(..., ge=1), db: AsyncSession = Depends(get_db)) -> LiveSessionResponse:
    return LiveSessionResponse(**await service.get_session(db, session_id))


@router.get("/sessions/{session_id}/reservation/me", response_model=LiveRegistrationResponse, summary="查询我的直播预约")
async def my_reservation(
    session_id: int = Path(..., ge=1),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LiveRegistrationResponse:
    await service.get_session(db, session_id)
    return LiveRegistrationResponse(**await service.registration(db, session_id, current.id))


@router.post("/sessions/{session_id}/reservations", response_model=LiveRegistrationResponse, status_code=201, summary="预约直播场次")
async def reserve(session_id: int = Path(..., ge=1), current: CurrentUser = Depends(get_realname_verified_user), db: AsyncSession = Depends(get_db)) -> LiveRegistrationResponse:
    return await service.reserve(db, session_id, current)


@router.delete("/sessions/{session_id}/reservations", response_model=LiveRegistrationResponse, summary="取消直播预约")
async def cancel(session_id: int = Path(..., ge=1), current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> LiveRegistrationResponse:
    return await service.update_registration(db, session_id, current.id, "cancel")


@router.post("/sessions/{session_id}/device-check", response_model=LiveRegistrationResponse, summary="提交设备检测结果")
async def device_check(body: LiveDeviceCheckRequest, session_id: int = Path(..., ge=1), current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> LiveRegistrationResponse:
    return await service.update_registration(db, session_id, current.id, "device", body.passed)


@router.post("/sessions/{session_id}/check-in", response_model=LiveRegistrationResponse, summary="直播签到")
async def check_in(session_id: int = Path(..., ge=1), current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> LiveRegistrationResponse:
    return await service.update_registration(db, session_id, current.id, "checkin")


@router.post("/sessions/{session_id}/invitations/respond", response_model=LiveActionResponse, summary="响应上台邀请")
async def respond_invitation(body: LiveInviteDecision, session_id: int = Path(..., ge=1), current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> LiveActionResponse:
    return await service.decide_invite(db, session_id, current.id, body.invitation_token, body.decision)


@router.post("/sessions/{session_id}/stage/leave", response_model=LiveActionResponse, summary="主动离开直播舞台")
async def leave_stage(
    session_id: int = Path(..., ge=1),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LiveActionResponse:
    return await service.leave_stage(db, session_id, current.id)


@router.post("/sessions/{session_id}/interactions", response_model=LiveInteractionResponse, status_code=201, summary="提交亮灯、选择或确认")
async def interact(body: LiveInteractionRequest, session_id: int = Path(..., ge=1), idempotency_key: str | None = Header(None, alias="Idempotency-Key"), current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> LiveInteractionResponse:
    if not idempotency_key or not 8 <= len(idempotency_key) <= 128:
        raise HTTPException(422, detail="请提供 8-128 个字符的 Idempotency-Key")
    return LiveInteractionResponse(**await service.create_interaction(db, session_id, current.id, body.target_user_id, body.interaction_type, idempotency_key))


@router.post("/sessions/{session_id}/reports", response_model=LiveReportResponse, status_code=201, summary="举报直播用户")
async def report(body: LiveReportRequest, session_id: int = Path(..., ge=1), current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> LiveReportResponse:
    return await service.create_report(db, session_id, current.id, body.target_user_id, body.category, body.description)


@router.post("/sessions/{session_id}/rtc-ticket", response_model=LiveRtcTicketResponse, summary="领取腾讯 TRTC 凭证")
async def rtc_ticket(session_id: int = Path(..., ge=1), current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> LiveRtcTicketResponse:
    session = await service.get_session(db, session_id)
    role = (await db.execute(text("SELECT role_code FROM live_session_role WHERE session_id=:sid AND user_id=:uid AND status=1 ORDER BY FIELD(role_code,'HOST','MATCHMAKER','GUEST','MODERATOR') LIMIT 1"), {"sid": session_id, "uid": current.id})).scalar()
    if not role:
        on_stage = (await db.execute(text("SELECT 1 FROM live_stage_seat WHERE session_id=:sid AND user_id=:uid AND status='ON_STAGE'"), {"sid": session_id, "uid": current.id})).scalar()
        if not on_stage:
            raise HTTPException(403, detail="当前用户没有加入 RTC 的资格")
        role = "GUEST"
    if session["status"] in {"DRAFT", "SCHEDULED", "CLOSED"}:
        raise HTTPException(409, detail="当前场次状态不可领取 RTC 凭证")
    try:
        app_id, user_sig, ttl = generate_user_sig(str(current.id))
    except LiveProviderUnavailable as exc:
        raise HTTPException(503, detail=str(exc)) from exc
    return LiveRtcTicketResponse(sdk_app_id=app_id, room_id=session_id, user_id=str(current.id), user_sig=user_sig, expires_in=ttl, role=str(role))

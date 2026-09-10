"""直播主持与红娘控制接口。"""

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas.live import (
    LiveInviteRequest,
    LiveInviteResponse,
    LiveActionResponse,
    LiveSeatRemoveRequest,
    LiveSessionResponse,
    LiveTransitionRequest,
)
from app.services import live as service

router = APIRouter(prefix="/live/host")


@router.post("/sessions/{session_id}/transition", response_model=LiveSessionResponse, summary="推进直播场次状态")
async def transition(body: LiveTransitionRequest, session_id: int = Path(..., ge=1), current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> LiveSessionResponse:
    return LiveSessionResponse(**await service.transition_session(db, session_id, current.id, body.to_status, body.expected_version, body.reason))


@router.post("/sessions/{session_id}/invitations", response_model=LiveInviteResponse, status_code=201, summary="邀请已签到用户上台")
async def invite(body: LiveInviteRequest, session_id: int = Path(..., ge=1), current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> LiveInviteResponse:
    return LiveInviteResponse(**await service.invite(db, session_id, current.id, body.user_id, body.seat_no))


@router.post("/sessions/{session_id}/stage/remove", response_model=LiveActionResponse, summary="强制用户下台")
async def remove_from_stage(
    body: LiveSeatRemoveRequest,
    session_id: int = Path(..., ge=1),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LiveActionResponse:
    return await service.remove_from_stage(
        db, session_id, current.id, body.user_id, body.reason
    )

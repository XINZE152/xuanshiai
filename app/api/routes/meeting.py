"""线下约见接口。"""

from fastapi import APIRouter, Body, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, get_current_admin, get_verified_user
from app.db.session import get_db
from app.schemas.meeting import (
    MeetingFeedbackCreate,
    MeetingRecordResponse,
    MeetingRequestCreate,
    MatchmakerMeetingRequestCreate,
    MeetingRequestResponse,
    MeetingScheduleCreate,
    MeetingStatusUpdate,
)
from app.services.meeting import (
    create_feedback,
    create_meeting_request,
    create_matchmaker_meeting_request,
    list_my_meeting_requests,
    schedule_meeting,
    update_meeting_request,
)

router = APIRouter(prefix="/matchmaker/meetings")
admin_router = APIRouter(prefix="/admin/matchmaker/meetings")


@router.post("/requests", response_model=MeetingRequestResponse, status_code=201, summary="提交约见申请")
async def create_request(body: MeetingRequestCreate = Body(...), current: CurrentUser = Depends(get_verified_user), db: AsyncSession = Depends(get_db)) -> MeetingRequestResponse:
    return await create_meeting_request(db, current, body)


@router.post("/requests/from-service", response_model=MeetingRequestResponse, status_code=201, summary="红娘基于服务单发起约见")
async def create_request_from_service(
    body: MatchmakerMeetingRequestCreate = Body(...),
    current: CurrentUser = Depends(get_verified_user),
    db: AsyncSession = Depends(get_db),
) -> MeetingRequestResponse:
    return await create_matchmaker_meeting_request(db, current, body)


@router.get("/requests/mine", response_model=list[MeetingRequestResponse], summary="查询我的约见申请")
async def mine_requests(current: CurrentUser = Depends(get_verified_user), db: AsyncSession = Depends(get_db)) -> list[MeetingRequestResponse]:
    return await list_my_meeting_requests(db, current)


@router.patch("/requests/{request_id}", response_model=MeetingRequestResponse, summary="处理约见申请")
async def update_request(request_id: int = Path(..., ge=1), body: MeetingStatusUpdate = Body(...), current: CurrentUser = Depends(get_verified_user), db: AsyncSession = Depends(get_db)) -> MeetingRequestResponse:
    return await update_meeting_request(db, current, request_id, body)


@router.post("/{meeting_id}/feedback", status_code=204, summary="提交约见反馈")
async def feedback(meeting_id: int = Path(..., ge=1), body: MeetingFeedbackCreate = Body(...), current: CurrentUser = Depends(get_verified_user), db: AsyncSession = Depends(get_db)) -> None:
    await create_feedback(db, current, meeting_id, body)


@admin_router.post("/requests/{request_id}/schedule", response_model=MeetingRecordResponse, status_code=201, summary="安排约会")
async def schedule(request_id: int = Path(..., ge=1), body: MeetingScheduleCreate = Body(...), admin: CurrentUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)) -> MeetingRecordResponse:
    return await schedule_meeting(db, admin, request_id, body)

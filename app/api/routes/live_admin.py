"""直播运营管理接口。"""

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, get_current_admin
from app.db.session import get_db
from app.schemas.live import (
    LiveRoleAssignment,
    LiveRoleAssignmentResponse,
    LiveSessionCreate,
    LiveSessionResponse,
)
from app.services import live as service

router = APIRouter(prefix="/admin/live")


@router.post("/sessions", response_model=LiveSessionResponse, status_code=201, summary="创建直播场次")
async def create(body: LiveSessionCreate, current: CurrentUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)) -> LiveSessionResponse:
    return LiveSessionResponse(**await service.create_session(db, current.id, body))


@router.put("/sessions/{session_id}/roles", response_model=LiveRoleAssignmentResponse, summary="分配直播场次角色")
async def assign_role(
    body: LiveRoleAssignment,
    session_id: int = Path(..., ge=1),
    current: CurrentUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> LiveRoleAssignmentResponse:
    return await service.assign_role(
        db,
        session_id,
        current.id,
        body.user_id,
        body.role_code,
        body.enabled,
    )

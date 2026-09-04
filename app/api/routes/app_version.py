from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas.app_version import AppVersionResponse
from app.services.app_version import check_version

router = APIRouter(prefix="/app")


@router.get("/version", response_model=AppVersionResponse, summary="检查应用更新")
async def version(
    platform: str = Query(...),
    version: str = Query(...),
    _current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AppVersionResponse:
    return await check_version(db, platform, version)

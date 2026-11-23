"""安全中心：治理公示与反诈内容。

前端 ``pagesSub/profileExtra/security.uvue`` 依赖本接口；
此前只做了页面没有后端路由，导致全站「安全中心数据加载失败」（404）。
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas.security import SecurityCenterResponse
from app.services.security_center import get_security_center

router = APIRouter()


@router.get(
    "/security/center",
    response_model=SecurityCenterResponse,
    summary="安全中心（治理公示 / 反诈内容 / 客服入口）",
)
async def security_center(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SecurityCenterResponse:
    return await get_security_center(db)

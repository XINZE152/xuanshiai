"""当前用户资料接口。"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas.auth import CompletionResponse, ProfileResponse, ProfileUpdateRequest
from app.services.auth import get_profile, recalculate_completion, update_profile

router = APIRouter(prefix="/users/me")


@router.get("/profile", response_model=ProfileResponse, summary="获取个人资料")
async def profile(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    """返回当前用户的基础资料和资料完整度。"""
    return await get_profile(db, current.id)


@router.patch("/profile", response_model=ProfileResponse, summary="修改个人资料")
async def edit_profile(body: ProfileUpdateRequest, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    """更新当前用户资料并重新计算资料完整度。"""
    return await update_profile(db, current.id, body)


@router.get("/completion", response_model=CompletionResponse, summary="查询资料完整度")
async def completion(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    """返回资料完整度以及浏览、申请、聊天权限状态。"""
    score = await recalculate_completion(db, current.id)
    await db.commit()
    return {
        "score": score,
        "missing_items": ["请完善未完成的资料项"],
        "can_browse": score >= 40,
        "can_apply": score >= 80 and current.realname_status == 2,
        "can_chat": score >= 80 and current.realname_status == 2,
    }

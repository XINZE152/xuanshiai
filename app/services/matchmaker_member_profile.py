"""服务红娘代为查看与维护会员资料。

实现全部复用 app.services.profile 中按 user_id 参数化的读写逻辑，红娘侧只在两处放宽：
1. 允许修改性别（会员自助编辑时性别一经提交不可改，红娘需要纠正录入错误）；
2. 代传的头像 / 相册 / 背景墙直接视为审核通过，保存后立即可在会员主页和广场展示。

鉴权由路由层的 require_workspace 保证，仅激活状态的服务红娘可调用。
"""

from typing import Any

from fastapi import HTTPException, UploadFile
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.auth import (
    CompletionResponse,
    NicknameUpdateResponse,
    PreferenceUpdateRequest,
    ProfileUpdateRequest,
)
from app.services.profile import (
    delete_photo,
    get_completion,
    get_preferences,
    get_profile,
    update_nickname,
    update_preferences,
    update_profile,
    upload_avatar,
    upload_background,
    upload_photo,
)


async def _ensure_member(db: AsyncSession, member_id: int) -> None:
    """确认目标会员存在，避免红娘对不存在的 id 写入脏数据。"""
    result = await db.execute(text("SELECT id FROM users WHERE id = :member_id"), {"member_id": member_id})
    if result.scalar() is None:
        raise HTTPException(404, detail="会员不存在")


async def get_member_profile(db: AsyncSession, member_id: int) -> dict[str, Any]:
    await _ensure_member(db, member_id)
    return await get_profile(db, member_id)


async def update_member_profile(
    db: AsyncSession, member_id: int, request: ProfileUpdateRequest
) -> dict[str, Any]:
    await _ensure_member(db, member_id)
    return await update_profile(db, member_id, request, allow_gender_change=True)


async def get_member_preferences(db: AsyncSession, member_id: int) -> dict[str, Any]:
    await _ensure_member(db, member_id)
    return await get_preferences(db, member_id)


async def update_member_preferences(
    db: AsyncSession, member_id: int, request: PreferenceUpdateRequest
) -> dict[str, Any]:
    await _ensure_member(db, member_id)
    return await update_preferences(db, member_id, request)


async def get_member_completion(db: AsyncSession, member_id: int) -> CompletionResponse:
    await _ensure_member(db, member_id)
    return await get_completion(db, member_id)


async def update_member_nickname(
    db: AsyncSession, member_id: int, nickname: str
) -> NicknameUpdateResponse:
    await _ensure_member(db, member_id)
    return await update_nickname(db, member_id, nickname)


async def upload_member_avatar(db: AsyncSession, member_id: int, file: UploadFile) -> dict[str, Any]:
    await _ensure_member(db, member_id)
    return await upload_avatar(db, member_id, file, auto_approve=True)


async def upload_member_background(db: AsyncSession, member_id: int, file: UploadFile) -> dict[str, Any]:
    await _ensure_member(db, member_id)
    return await upload_background(db, member_id, file, auto_approve=True)


async def upload_member_photo(db: AsyncSession, member_id: int, file: UploadFile) -> dict[str, Any]:
    await _ensure_member(db, member_id)
    return await upload_photo(db, member_id, file, auto_approve=True)


async def delete_member_photo(db: AsyncSession, member_id: int, media_id: int) -> None:
    await _ensure_member(db, member_id)
    await delete_photo(db, member_id, media_id)

"""匿名游客预览路由（GUEST-PREVIEW-20261115）。

无需登录；完整契约见 docs/api/游客预览.md。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.api.routes.auth import request_ip
from app.schemas.public_preview import (
    PublicActivityPreviewPage,
    PublicCommunityPreviewPage,
    PublicDiscoveryPreviewPage,
)
from app.services import public_preview

router = APIRouter(prefix="/public")


@router.get(
    "/discovery/preview",
    response_model=PublicDiscoveryPreviewPage,
    summary="游客推荐名片预览（匿名只读）",
)
async def discovery_preview(
    request: Request,
    page: int = Query(1, ge=1, le=100),
    page_size: int = Query(6, ge=1, le=10),
    db: AsyncSession = Depends(get_db),
) -> PublicDiscoveryPreviewPage:
    return await public_preview.get_public_discovery_preview(
        db, ip=request_ip(request), page=page, page_size=page_size
    )


@router.get(
    "/community/preview",
    response_model=PublicCommunityPreviewPage,
    summary="游客社区动态预览（匿名只读）",
)
async def community_preview(
    request: Request,
    page: int = Query(1, ge=1, le=100),
    page_size: int = Query(6, ge=1, le=10),
    db: AsyncSession = Depends(get_db),
) -> PublicCommunityPreviewPage:
    return await public_preview.get_public_community_preview(
        db, ip=request_ip(request), page=page, page_size=page_size
    )


@router.get(
    "/activities/preview",
    response_model=PublicActivityPreviewPage,
    summary="游客线下活动预览（匿名只读）",
)
async def activities_preview(
    request: Request,
    page: int = Query(1, ge=1, le=100),
    page_size: int = Query(6, ge=1, le=10),
    db: AsyncSession = Depends(get_db),
) -> PublicActivityPreviewPage:
    return await public_preview.get_public_activities_preview(
        db, ip=request_ip(request), page=page, page_size=page_size
    )
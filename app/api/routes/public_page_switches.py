"""匿名页面显示开关下发（PAGE-SWITCH-20261116）。

无需登录；完整契约见 docs/api/页面开关.md。前端应静默消费本接口
（不弹 loading/错误 toast），429/断网时 fail-open 到缓存或默认全开。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.api.routes.auth import request_ip
from app.core.config import settings
from app.core.redis import consume_rate_limit
from app.schemas.ai_common import AiCapabilitiesResponse
from app.schemas.public_page_switches import (
    PublicPageSwitchOperation,
    PublicPageSwitchesResponse,
)
from app.services import page_switches
from app.services.ai import runtime_gates
from app.services.ai.flags import public_ai_capabilities
from app.services.runtime_config import (
    platform_browse_open,
    platform_maintenance_message,
    platform_registration_open,
)

router = APIRouter(prefix="/public")

PAGE_SWITCHES_RATE_LIMIT = 30
PAGE_SWITCHES_WINDOW_SECONDS = 60


@router.get(
    "/page-switches",
    response_model=PublicPageSwitchesResponse,
    summary="页面显示开关（匿名只读）",
)
async def get_public_page_switches(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> PublicPageSwitchesResponse:
    allowed = await consume_rate_limit(
        f"public:page-switches:{request_ip(request)}",
        PAGE_SWITCHES_RATE_LIMIT,
        PAGE_SWITCHES_WINDOW_SECONDS,
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "PUBLIC_PAGE_SWITCHES_RATE_LIMITED",
                "message": "请求太频繁，请稍后再试",
                "retryable": True,
            },
        )
    await runtime_gates.maybe_refresh(db)
    payload = await page_switches.page_switches_payload(db)
    operation = PublicPageSwitchOperation(
        registration_enabled=await platform_registration_open(db),
        browse_enabled=await platform_browse_open(db),
        maintenance_message=await platform_maintenance_message(db),
    )
    return PublicPageSwitchesResponse(
        **payload,
        operation=operation,
        ai=AiCapabilitiesResponse(**public_ai_capabilities(settings)),
    )
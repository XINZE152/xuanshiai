"""健康检查和服务就绪接口。"""

from fastapi import APIRouter

from app.core.config import settings


router = APIRouter()


@router.get("/health", summary="服务健康检查")
async def health_check() -> dict[str, str]:
    """返回服务状态，不依赖外部数据库或缓存服务。"""
    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.environment,
    }

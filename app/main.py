"""FastAPI application factory and process-level configuration."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.core.config import settings


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    application.mount("/storage/uploads", StaticFiles(directory=settings.upload_dir), name="uploads")
    application.include_router(api_router, prefix=settings.api_prefix)

    @application.get("/", tags=["system"])
    async def root() -> dict[str, str]:
        """Return a small service discovery response."""
        return {
            "service": settings.app_name,
            "version": settings.app_version,
            "docs": "/docs" if settings.docs_enabled else "disabled",
        }

    return application


app = create_app()

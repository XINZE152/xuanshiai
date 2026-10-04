"""FastAPI application factory and process-level configuration."""

import asyncio
import logging
import mimetypes
import re
import time
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from uuid import uuid4

from urllib.parse import parse_qs
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from app.api.router import OPENAPI_TAGS, api_router
from app.api.routes.admin_home import legacy_router as admin_home_legacy_router
from app.core.config import settings
from app.core.logging import configure_logging, request_id_context
from app.db.session import engine, session_factory
from app.services.media_access import MediaAccessUnavailable, verify_media_access

configure_logging(settings)
logger = logging.getLogger(__name__)
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class _ProtectedStorageFiles(StaticFiles):
    """Keep private media (AI voice TTS, follow-up attachments, ...) out of
    the anonymous static surface; category decisions live in media_access."""

    async def get_response(self, path: str, scope):
        raw_query = scope.get("query_string", b"")
        query = parse_qs(raw_query.decode("ascii", errors="ignore"))
        try:
            allowed = await verify_media_access(path, query)
        except MediaAccessUnavailable:
            return Response(status_code=503)
        if not allowed:
            return Response(status_code=403)
        return await super().get_response(path, scope)


async def initialize_database_on_startup() -> None:
    """Run the existing synchronous, idempotent initializer outside the event loop."""
    if not settings.auto_init_db:
        logger.info("AUTO_INIT_DB=false，跳过数据库自动初始化")
        return
    logger.info("正在执行数据库自动初始化...")
    try:
        from database_setup_marriage import initialize_database

        await asyncio.to_thread(initialize_database)
    except Exception as exc:
        logger.exception("数据库自动初始化失败")
        raise RuntimeError("数据库自动初始化失败，请检查 DATABASE_URL 和 MySQL 服务") from exc
    logger.info("数据库自动初始化完成")


async def verify_ai_advisor_knowledge_on_startup() -> None:
    """启动期校验 AI 军师知识版本与配置一致（非开发环境不一致即 fail-fast）。"""
    if session_factory is None:
        return
    from app.services.ai_advisor import verify_knowledge_version

    try:
        async with session_factory() as db:
            await verify_knowledge_version(db)
    except RuntimeError:
        raise
    except Exception as exc:  # 知识表尚未建好等情况不应阻断启动，仅告警
        logger.warning("AI 军师知识版本校验未能执行：%s", exc)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    logger.info(
        "application_starting environment=%s version=%s",
        settings.environment,
        settings.app_version,
    )
    await initialize_database_on_startup()
    await verify_ai_advisor_knowledge_on_startup()
    yield
    if engine is not None:
        await engine.dispose()
    logger.info("application_stopping")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    application = FastAPI(
        # The router already carries the public /api/v1 prefix. Keeping a
        # second /api root path makes local requests resolve as /api/api/v1.
        root_path="",
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        lifespan=lifespan,
        openapi_tags=OPENAPI_TAGS,
    )
    # ... 其余代码保持不变 ...

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.middleware("http")
    async def log_requests(request: Request, call_next) -> Response:
        """Log request boundaries without recording bodies or credentials."""
        supplied_request_id = request.headers.get("X-Request-ID", "")
        request_id = (
            supplied_request_id
            if REQUEST_ID_PATTERN.fullmatch(supplied_request_id)
            else uuid4().hex
        )
        context_token = request_id_context.set(request_id)
        started_at = time.perf_counter()
        client_host = request.client.host if request.client else "-"
        logger.info(
            "request_started method=%s path=%s client=%s",
            request.method,
            request.url.path,
            client_host,
        )
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - started_at) * 1000
            logger.exception(
                "request_failed method=%s path=%s status=500 duration_ms=%.2f client=%s",
                request.method,
                request.url.path,
                duration_ms,
                client_host,
            )
            raise
        else:
            duration_ms = (time.perf_counter() - started_at) * 1000
            status_code = response.status_code
            log_method = (
                logger.error
                if status_code >= 500
                else logger.warning
                if status_code >= 400
                else logger.info
            )
            log_method(
                "request_completed method=%s path=%s status=%s duration_ms=%.2f client=%s content_length=%s",
                request.method,
                request.url.path,
                status_code,
                duration_ms,
                client_host,
                response.headers.get("content-length", "-"),
            )
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            request_id_context.reset(context_token)

    # Minimal Linux images may not register WebP in Python's MIME table.
    mimetypes.add_type("image/webp", ".webp")
    mimetypes.add_type("video/mp4", ".mp4")
    mimetypes.add_type("audio/mpeg", ".mp3")
    mimetypes.add_type("audio/ogg", ".ogg")
    mimetypes.add_type("audio/wav", ".wav")

    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    application.mount(
        "/storage/uploads",
        _ProtectedStorageFiles(directory=settings.upload_dir),
        name="uploads",
    )
    application.include_router(api_router, prefix=settings.api_prefix)
    # The captured production admin uses these compatibility paths without /api/v1.
    application.include_router(admin_home_legacy_router)

    @application.get("/", tags=["系统"])
    async def root() -> dict[str, str]:
        """Return a small service discovery response."""
        return {
            "service": settings.app_name,
            "version": settings.app_version,
            "docs": "/docs" if settings.docs_enabled else "disabled",
        }

    return application


app = create_app()

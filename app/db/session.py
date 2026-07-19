"""Async SQLAlchemy engine and request-scoped database sessions."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings


try:
    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_recycle=1800,
    )
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
except ModuleNotFoundError:
    # Keep health and OpenAPI endpoints importable before optional DB extras are installed.
    engine = None
    session_factory = None


async def get_db() -> AsyncIterator[AsyncSession]:
    if session_factory is None:
        raise RuntimeError("数据库驱动未安装，请安装项目运行依赖")
    async with session_factory() as session:
        yield session

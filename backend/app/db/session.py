""" 异步数据库 engine/session 工厂"""
from collections.abc import AsyncIterator
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
)

AsyncSessionLocal:async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    expire_on_commit = False,
    autoflush = False,
)
async def get_session() -> AsyncIterator[AsyncSession]:
    """ FastAPI 依赖注入，提供请求级 AsyncSession """
    async with AsyncSessionLocal() as session:
        yield session

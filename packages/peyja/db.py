"""اتصال دیتابیس (async) — SQLAlchemy 2."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from peyja.config import get_settings

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine, _session_factory
    if _engine is None:
        settings = get_settings()
        url = settings.effective_database_url
        kwargs: dict = {"echo": False, "pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs.pop("pool_pre_ping", None)
        _engine = create_async_engine(url, **kwargs)
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _session_factory is not None
    return _session_factory


async def ensure_schema() -> None:
    """ساخت جداول در صورت نبود (راحتی توسعه/استقرار SQLite — برای Postgres: Alembic)."""
    from peyja.models import Base

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def session_scope() -> AsyncIterator[AsyncSession]:
    """جلسه‌ی کاری کوتاه برای CLI/worker."""
    factory = get_session_factory()
    async with factory() as session:
        yield session

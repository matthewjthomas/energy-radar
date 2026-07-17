"""Async SQLAlchemy engine/session setup and TimescaleDB initialization."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models import Base

logger = logging.getLogger(__name__)

settings = get_settings()

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)

# Tables that should become TimescaleDB hypertables partitioned on `time`.
_HYPERTABLES = ["readings", "weather_observations", "weather_forecasts"]


async def init_db() -> None:
    """Create tables and convert the time-series tables into hypertables."""
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb;"))
        await conn.run_sync(Base.metadata.create_all)
        for table in _HYPERTABLES:
            try:
                await conn.execute(
                    text(
                        f"SELECT create_hypertable('{table}', by_range('time'), "
                        "if_not_exists => TRUE, migrate_data => TRUE);"
                    )
                )
            except Exception:  # noqa: BLE001 - best effort, table may already be a hypertable
                logger.warning("Could not create hypertable for %s", table, exc_info=True)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that yields a database session."""
    async with async_session_factory() as session:
        yield session

"""Shared pytest fixtures."""
from __future__ import annotations

import os

# Configure test environment before application modules import the database engine.
os.environ.setdefault(
    "DATABASE_URL",
    os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://energyradar:changeme@localhost:5433/energyradar",
    ),
)
os.environ.setdefault("APP_TIMEZONE", "UTC")
os.environ.setdefault("HA_URL", "")
os.environ.setdefault("HA_TOKEN", "")

import socket
from urllib.parse import urlparse

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.db as db_module
from app.config import get_settings
from app.main import create_app
from app.models import (
    Base,
    EventMarker,
    ForecastBias,
    HAEntityConfig,
    Location,
    PricingConfig,
    Reading,
    ThermostatConfig,
    ThermostatReading,
    UsageForecastSnapshot,
    WeatherForecast,
    WeatherObservation,
)

_MODELS_TO_CLEAN = [
    UsageForecastSnapshot,
    ForecastBias,
    EventMarker,
    Reading,
    ThermostatReading,
    WeatherForecast,
    WeatherObservation,
    HAEntityConfig,
    ThermostatConfig,
    PricingConfig,
    Location,
]


async def _clean_database(session) -> None:
    for model in _MODELS_TO_CLEAN:
        await session.execute(delete(model))
    await session.commit()


def pytest_configure() -> None:
    get_settings.cache_clear()


def _database_reachable() -> bool:
    url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


@pytest.fixture
def requires_database() -> None:
    if not _database_reachable():
        pytest.skip("Postgres/TimescaleDB is not available for integration tests")


@pytest_asyncio.fixture
async def async_client(requires_database):
    """Async HTTP client sharing one event loop with the database engine."""
    get_settings.cache_clear()
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    db_module.engine = engine
    db_module.async_session_factory = async_sessionmaker(engine, expire_on_commit=False)

    application = create_app(enable_scheduler=False)
    transport = ASGITransport(app=application)
    async with application.router.lifespan_context(application):
        async with db_module.async_session_factory() as session:
            await _clean_database(session)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client

    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(requires_database):
    """Database session for integration tests."""
    get_settings.cache_clear()
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    db_module.engine = engine
    db_module.async_session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb;"))
        await conn.run_sync(Base.metadata.create_all)
        for table in db_module._HYPERTABLES:
            try:
                await conn.execute(
                    text(
                        f"SELECT create_hypertable('{table}', by_range('time'), "
                        "if_not_exists => TRUE, migrate_data => TRUE);"
                    )
                )
            except Exception:
                pass

    async with db_module.async_session_factory() as session:
        await _clean_database(session)
        yield session
        await _clean_database(session)

    await engine.dispose()

"""Background jobs: polling Home Assistant and refreshing weather data."""
from __future__ import annotations

import datetime as dt
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import get_settings
from app.db import session_scope
from app.ha_client import HomeAssistantClient, HomeAssistantError
from app.models import HAEntityConfig, Location, Reading, WeatherForecast, WeatherObservation
from app.weather_client import get_forecast_weather, get_historical_weather

logger = logging.getLogger(__name__)


async def poll_ha_readings() -> None:
    settings = get_settings()
    if not settings.ha_configured:
        return

    client = HomeAssistantClient(settings.ha_url, settings.ha_token)
    now = dt.datetime.now(dt.timezone.utc)

    async with session_scope() as session:
        configs = (
            await session.execute(select(HAEntityConfig).where(HAEntityConfig.enabled.is_(True)))
        ).scalars().all()
        if not configs:
            return

        for cfg in configs:
            last_reading = (
                await session.execute(
                    select(Reading)
                    .where(Reading.entity_id == cfg.entity_id, Reading.source_type == cfg.source_type)
                    .order_by(Reading.time.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

            start = last_reading.time if last_reading else now - dt.timedelta(days=2)
            prev_value = last_reading.raw_value if last_reading else None

            try:
                points = await client.get_history(cfg.entity_id, start, now)
            except HomeAssistantError:
                logger.warning("Failed to fetch HA history for %s", cfg.entity_id, exc_info=True)
                continue

            rows = []
            for timestamp, value in points:
                if last_reading and timestamp <= last_reading.time:
                    continue
                consumption: float | None
                if cfg.is_cumulative:
                    consumption = value - prev_value if prev_value is not None and value >= prev_value else None
                else:
                    consumption = value
                prev_value = value
                rows.append(
                    {
                        "time": timestamp,
                        "source_type": cfg.source_type,
                        "entity_id": cfg.entity_id,
                        "raw_value": value,
                        "consumption": consumption,
                    }
                )

            if rows:
                stmt = pg_insert(Reading).values(rows)
                stmt = stmt.on_conflict_do_nothing(index_elements=["time", "source_type", "entity_id"])
                await session.execute(stmt)

        await session.commit()


async def poll_weather_historical() -> None:
    """Backfill actual weather observations up to yesterday (archive has a short lag)."""
    async with session_scope() as session:
        location = (await session.execute(select(Location).limit(1))).scalar_one_or_none()
        if location is None:
            return

        latest = (
            await session.execute(
                select(WeatherObservation).order_by(WeatherObservation.time.desc()).limit(1)
            )
        ).scalar_one_or_none()

        yesterday = dt.date.today() - dt.timedelta(days=1)
        start_date = latest.time.date() + dt.timedelta(days=1) if latest else yesterday - dt.timedelta(days=90)
        if start_date > yesterday:
            return

        records = await get_historical_weather(
            location.latitude, location.longitude, start_date, yesterday, location.timezone
        )
        if not records:
            return

        stmt = pg_insert(WeatherObservation).values(records)
        stmt = stmt.on_conflict_do_update(
            index_elements=["time"],
            set_={
                "temperature_c": stmt.excluded.temperature_c,
                "apparent_temperature_c": stmt.excluded.apparent_temperature_c,
                "humidity_pct": stmt.excluded.humidity_pct,
                "precipitation_mm": stmt.excluded.precipitation_mm,
                "wind_speed_kph": stmt.excluded.wind_speed_kph,
            },
        )
        await session.execute(stmt)
        await session.commit()


async def poll_weather_forecast() -> None:
    """Refresh the rolling weather forecast for the configured location."""
    async with session_scope() as session:
        location = (await session.execute(select(Location).limit(1))).scalar_one_or_none()
        if location is None:
            return

        records = await get_forecast_weather(location.latitude, location.longitude, location.timezone)
        if not records:
            return

        generated_at = dt.datetime.now(dt.timezone.utc)
        for record in records:
            record["generated_at"] = generated_at

        stmt = pg_insert(WeatherForecast).values(records)
        stmt = stmt.on_conflict_do_update(
            index_elements=["time"],
            set_={
                "generated_at": stmt.excluded.generated_at,
                "temperature_c": stmt.excluded.temperature_c,
                "apparent_temperature_c": stmt.excluded.apparent_temperature_c,
                "humidity_pct": stmt.excluded.humidity_pct,
                "precipitation_mm": stmt.excluded.precipitation_mm,
                "precipitation_probability_pct": stmt.excluded.precipitation_probability_pct,
                "wind_speed_kph": stmt.excluded.wind_speed_kph,
            },
        )
        await session.execute(stmt)
        await session.commit()


def create_scheduler() -> AsyncIOScheduler:
    settings = get_settings()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        poll_ha_readings,
        IntervalTrigger(minutes=settings.ha_poll_interval_minutes),
        id="poll_ha_readings",
        next_run_time=dt.datetime.now(),
    )
    scheduler.add_job(
        poll_weather_forecast,
        IntervalTrigger(hours=settings.weather_forecast_interval_hours),
        id="poll_weather_forecast",
        next_run_time=dt.datetime.now(),
    )
    scheduler.add_job(
        poll_weather_historical,
        IntervalTrigger(hours=6),
        id="poll_weather_historical",
        next_run_time=dt.datetime.now(),
    )
    return scheduler

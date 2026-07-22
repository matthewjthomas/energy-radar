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


def _rows_from_history(
    cfg: HAEntityConfig,
    points: list[tuple[dt.datetime, float]],
    last_reading: Reading | None,
) -> list[dict]:
    prev_value = last_reading.raw_value if last_reading else None
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
    return rows


async def _rows_from_statistics(
    client: HomeAssistantClient,
    cfg: HAEntityConfig,
    start: dt.datetime,
    end: dt.datetime,
    last_reading: Reading | None,
) -> list[dict] | None:
    """Build Reading rows from HA long-term statistics.

    Returns None (rather than an empty list) when statistics aren't usable for
    this entity at all, so the caller can fall back to raw history instead of
    treating "no new data yet" as "this entity has no long-term stats".
    """
    try:
        points = await client.get_statistics(cfg.entity_id, start, end, period="hour")
    except HomeAssistantError as exc:
        logger.warning(
            "HA long-term statistics unavailable for %s (%s), falling back to raw history",
            cfg.entity_id,
            exc,
        )
        return None

    if not points and last_reading is None:
        # Brand new mapping with zero statistics at all likely means this entity
        # doesn't have long-term statistics enabled; let the caller fall back.
        logger.info("No HA long-term statistics returned for %s, falling back to raw history", cfg.entity_id)
        return None

    # Do not seed prev_value from last_reading.raw_value – raw history and HA
    # long-term statistics use different cumulative baselines (e.g. daily-reset
    # "Today" sensors vs lifetime sum in statistics), so let the stats series
    # establish its own baseline from the 2h context window fetched above.
    prev_value = None
    rows = []
    usable_points = 0
    for point in points:
        timestamp = point["time"]
        if cfg.is_cumulative:
            value = point["sum"] if point["sum"] is not None else point["state"]
        else:
            value = point["mean"] if point["mean"] is not None else point["state"]
        if value is None:
            continue
        usable_points += 1
        consumption: float | None
        if cfg.is_cumulative:
            consumption = value - prev_value if prev_value is not None and value >= prev_value else None
        else:
            consumption = value
        prev_value = value
        # Append ALL points, including the 2h context window before last_reading.
        # on_conflict_do_nothing deduplicates rows that already exist.  Including
        # context points means a stats jump that falls inside the window (e.g.
        # because a raw-history row pushed last_reading past a stats hour boundary)
        # is captured with the correct consumption rather than silently absorbed
        # into prev_value and discarded.
        rows.append(
            {
                "time": timestamp,
                "source_type": cfg.source_type,
                "entity_id": cfg.entity_id,
                "raw_value": value,
                "consumption": consumption,
            }
        )

    if points and usable_points == 0:
        # Statistics exist for this entity, but none of the fetched points had a
        # usable sum/state/mean value for the configured mode (e.g. mapped as
        # cumulative but HA never records a "sum" for it). Fall back rather than
        # silently reporting zero data forever.
        logger.warning(
            "HA long-term statistics for %s had no usable %s values, falling back to raw history",
            cfg.entity_id,
            "sum" if cfg.is_cumulative else "mean/state",
        )
        return None

    return rows


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

            # Long-term statistics are retained indefinitely by default (unlike raw
            # state history, which HA typically purges after ~10 days), so they're
            # the preferred source for a deep initial backfill.
            # Extend the window 2 hours before last_reading so _rows_from_statistics
            # can calibrate prev_value from within the statistics series before
            # computing the first new delta (avoids scale mismatch when transitioning
            # from raw-history readings to statistics-based readings).
            stats_start = (
                last_reading.time - dt.timedelta(hours=2)
                if last_reading
                else now - dt.timedelta(days=settings.ha_stats_lookback_days)
            )
            rows = await _rows_from_statistics(client, cfg, stats_start, now, last_reading)

            if rows is None:
                # No long-term statistics available for this entity at all -
                # fall back to raw history (limited to whatever HA has retained).
                history_start = last_reading.time if last_reading else now - dt.timedelta(days=2)
                try:
                    history_points = await client.get_history(cfg.entity_id, history_start, now)
                except HomeAssistantError:
                    logger.warning("Failed to fetch HA history for %s", cfg.entity_id, exc_info=True)
                    continue
                rows = _rows_from_history(cfg, history_points, last_reading)
                logger.info("Fetched %d raw-history rows for %s", len(rows), cfg.entity_id)
            else:
                logger.info("Fetched %d long-term-statistics rows for %s", len(rows), cfg.entity_id)

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

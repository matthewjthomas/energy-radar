"""Background jobs: polling Home Assistant and refreshing weather data."""
from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import get_settings
from app.db import session_scope
from app.forecast_calibration import run_daily_forecast_calibration
from app.ha_client import HomeAssistantClient, HomeAssistantError
from app.models import HAEntityConfig, Location, Reading, ThermostatConfig, ThermostatReading, WeatherForecast, WeatherObservation
from app.weather_client import get_forecast_weather, get_historical_weather

logger = logging.getLogger(__name__)

# Postgres/asyncpg caps bind parameters per statement (~32k–65k depending on
# build). Chunk large upserts so first-run weather backfills don't fail.
_INSERT_BATCH_SIZE = 500
# How many calendar days of archive weather to pull per scheduler tick.
_WEATHER_BACKFILL_CHUNK_DAYS = 30
# Raw HA history is more reliable than long-term statistics for recent days.
# Use complete local calendar days so one day never mixes the two data sources.
_RECENT_HISTORY_DAYS = 7


def _recent_history_start(
    now: dt.datetime, timezone_name: str
) -> dt.datetime:
    """Return UTC midnight at the start of the recent local-day window."""
    tz = ZoneInfo(timezone_name)
    local_today = now.astimezone(tz).date()
    first_day = local_today - dt.timedelta(days=_RECENT_HISTORY_DAYS - 1)
    return dt.datetime.combine(first_day, dt.time.min, tzinfo=tz).astimezone(
        dt.timezone.utc
    )


def _batched(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


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


def _rows_from_history_points(
    cfg: HAEntityConfig,
    points: list[tuple[dt.datetime, float]],
) -> list[dict]:
    """Build reading rows for an entire history window (used for recent catch-up)."""
    prev_value = None
    rows = []
    for timestamp, value in sorted(points, key=lambda p: p[0]):
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


async def _upsert_readings(session, rows: list[dict]) -> None:
    if not rows:
        return
    for batch in _batched(rows, _INSERT_BATCH_SIZE):
        stmt = pg_insert(Reading).values(batch)
        stmt = stmt.on_conflict_do_update(
            index_elements=["time", "source_type", "entity_id"],
            set_={
                "raw_value": stmt.excluded.raw_value,
                "consumption": func.coalesce(stmt.excluded.consumption, Reading.consumption),
            },
        )
        await session.execute(stmt)


async def _clear_recent_readings(session, cfg: HAEntityConfig, since: dt.datetime) -> None:
    await session.execute(
        delete(Reading).where(
            Reading.entity_id == cfg.entity_id,
            Reading.source_type == cfg.source_type,
            Reading.time >= since,
        )
    )


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
            recent_start = _recent_history_start(now, settings.app_timezone)

            history_rows: list[dict] = []
            try:
                history_points = await client.get_history(cfg.entity_id, recent_start, now)
                history_rows = _rows_from_history_points(cfg, history_points)
            except HomeAssistantError:
                logger.warning("Failed to fetch recent HA history for %s", cfg.entity_id, exc_info=True)

            # Always probe statistics up to the calendar boundary. This avoids
            # guessing based on the latest row, which may itself be raw history.
            stats_probe_start = min(
                stats_start, recent_start - dt.timedelta(hours=2)
            )
            fetched = await _rows_from_statistics(
                client, cfg, stats_probe_start, recent_start, last_reading
            )
            uses_statistics = fetched is not None
            stats_rows = fetched or []

            if not uses_statistics:
                # Entity has no long-term statistics — incremental raw history only.
                history_start = last_reading.time if last_reading else now - dt.timedelta(days=2)
                try:
                    history_points = await client.get_history(cfg.entity_id, history_start, now)
                except HomeAssistantError:
                    logger.warning("Failed to fetch HA history for %s", cfg.entity_id, exc_info=True)
                    continue
                rows = _rows_from_history(cfg, history_points, last_reading)
                logger.info("Fetched %d raw-history rows for %s", len(rows), cfg.entity_id)
                if rows:
                    await _upsert_readings(session, rows)
                continue

            # Entity uses statistics for older data; always refresh the recent window
            # from raw history so we don't double-count stats + state-change rows.
            await _clear_recent_readings(session, cfg, recent_start)
            if history_rows:
                await _upsert_readings(session, history_rows)
            if stats_rows:
                older_stats = [row for row in stats_rows if row["time"] < recent_start]
                if older_stats:
                    await _upsert_readings(session, older_stats)
            logger.info(
                "Refreshed %s: %d recent history rows (%s -> now), %d older stats rows",
                cfg.entity_id,
                len(history_rows),
                recent_start.isoformat(),
                len(stats_rows),
            )

        await session.commit()


async def poll_thermostat_readings() -> None:
    settings = get_settings()
    if not settings.ha_configured:
        return

    client = HomeAssistantClient(settings.ha_url, settings.ha_token)
    now = dt.datetime.now(dt.timezone.utc)

    async with session_scope() as session:
        configs = (
            await session.execute(
                select(ThermostatConfig).where(ThermostatConfig.enabled.is_(True))
            )
        ).scalars().all()
        if not configs:
            return

        for cfg in configs:
            last_reading = (
                await session.execute(
                    select(ThermostatReading)
                    .where(ThermostatReading.entity_id == cfg.entity_id)
                    .order_by(ThermostatReading.time.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

            history_start = (
                last_reading.time if last_reading else now - dt.timedelta(days=settings.ha_stats_lookback_days)
            )
            rows: list[dict] = []
            try:
                history_points = await client.get_climate_history(cfg.entity_id, history_start, now)
            except HomeAssistantError:
                logger.warning("Failed to fetch HA climate history for %s", cfg.entity_id, exc_info=True)
                history_points = []

            for timestamp, payload in history_points:
                if last_reading and timestamp <= last_reading.time:
                    continue
                rows.append(
                    {
                        "time": timestamp,
                        "entity_id": cfg.entity_id,
                        "setpoint_c": payload.get("setpoint_c"),
                        "current_temp_c": payload.get("current_temp_c"),
                        "hvac_mode": payload.get("hvac_mode"),
                        "hvac_action": payload.get("hvac_action"),
                    }
                )

            if not rows:
                latest = await client.get_climate_state(cfg.entity_id)
                if latest and (last_reading is None or latest[0] > last_reading.time):
                    timestamp, payload = latest
                    rows.append(
                        {
                            "time": timestamp,
                            "entity_id": cfg.entity_id,
                            "setpoint_c": payload.get("setpoint_c"),
                            "current_temp_c": payload.get("current_temp_c"),
                            "hvac_mode": payload.get("hvac_mode"),
                            "hvac_action": payload.get("hvac_action"),
                        }
                    )

            if rows:
                for batch in _batched(rows, _INSERT_BATCH_SIZE):
                    stmt = pg_insert(ThermostatReading).values(batch)
                    stmt = stmt.on_conflict_do_nothing(index_elements=["time", "entity_id"])
                    await session.execute(stmt)
                logger.info("Stored %d thermostat rows for %s", len(rows), cfg.entity_id)

        await session.commit()


async def poll_weather_historical() -> None:
    """Backfill actual weather observations up to yesterday (archive has a short lag)."""
    settings = get_settings()
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
        start_date = (
            latest.time.date() + dt.timedelta(days=1)
            if latest
            else yesterday - dt.timedelta(days=settings.weather_lookback_days)
        )
        if start_date > yesterday:
            return

        # Backfill in chunks so a year+ of hourly rows doesn't exceed Postgres
        # parameter limits or stall a Pi on first startup.
        end_date = min(start_date + dt.timedelta(days=_WEATHER_BACKFILL_CHUNK_DAYS - 1), yesterday)

        records = await get_historical_weather(
            location.latitude, location.longitude, start_date, end_date, location.timezone
        )
        if not records:
            return

        for batch in _batched(records, _INSERT_BATCH_SIZE):
            stmt = pg_insert(WeatherObservation).values(batch)
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
        logger.info(
            "Stored %d weather observations for %s to %s (%s days remaining to yesterday)",
            len(records),
            start_date,
            end_date,
            max((yesterday - end_date).days, 0),
        )


async def poll_weather_forecast() -> None:
    """Refresh the rolling weather forecast for the configured location.

    Also pulls a short past_days window so recent hours are available for the
    usage model before the Open-Meteo archive publishes them.
    """
    async with session_scope() as session:
        location = (await session.execute(select(Location).limit(1))).scalar_one_or_none()
        if location is None:
            return

        records = await get_forecast_weather(
            location.latitude,
            location.longitude,
            location.timezone,
            past_days=7,
        )
        if not records:
            return

        generated_at = dt.datetime.now(dt.timezone.utc)
        for record in records:
            record["generated_at"] = generated_at

        for batch in _batched(records, _INSERT_BATCH_SIZE):
            stmt = pg_insert(WeatherForecast).values(batch)
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


async def run_daily_forecast_calibration_job() -> None:
    """Score yesterday's forecasts, refresh bias, and store today's snapshot."""
    try:
        await run_daily_forecast_calibration()
    except Exception:
        logger.exception("Daily forecast calibration failed")


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
        poll_thermostat_readings,
        IntervalTrigger(minutes=settings.ha_poll_interval_minutes),
        id="poll_thermostat_readings",
        next_run_time=dt.datetime.now(),
    )
    scheduler.add_job(
        poll_weather_historical,
        IntervalTrigger(hours=6),
        id="poll_weather_historical",
        next_run_time=dt.datetime.now(),
    )
    scheduler.add_job(
        run_daily_forecast_calibration_job,
        CronTrigger(
            hour=settings.forecast_calibration_hour,
            minute=5,
            timezone=settings.app_timezone,
        ),
        id="daily_forecast_calibration",
    )
    # Catch up after restarts if yesterday has not been scored yet.
    scheduler.add_job(
        run_daily_forecast_calibration_job,
        id="forecast_calibration_startup",
        next_run_time=dt.datetime.now() + dt.timedelta(minutes=2),
    )
    return scheduler

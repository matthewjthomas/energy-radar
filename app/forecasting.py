"""Shared usage model building and forecast generation."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import (
    RegressionResult,
    aggregate_daily_thermostat,
    aggregate_daily_usage,
    aggregate_daily_weather,
    fit_proxy_model_from_thermostat,
    fit_usage_model,
    forecast_thermostat_profile,
    forecast_usage,
    route_model_for_source,
)
from app.config import get_settings
from app.models import (
    HAEntityConfig,
    PricingConfig,
    Reading,
    SourceType,
    ThermostatConfig,
    ThermostatReading,
    WeatherForecast,
    WeatherObservation,
)


def local_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().app_timezone)


async def enabled_sources(session: AsyncSession) -> list[SourceType]:
    rows = (
        await session.execute(
            select(HAEntityConfig.source_type).where(HAEntityConfig.enabled.is_(True)).distinct()
        )
    ).scalars().all()
    return sorted(set(rows), key=lambda s: s.value)


async def thermostat_config(session: AsyncSession) -> ThermostatConfig | None:
    return (await session.execute(select(ThermostatConfig).limit(1))).scalar_one_or_none()


async def forecastable_sources(session: AsyncSession) -> list[SourceType]:
    """Sources with meter data and/or thermostat-backed estimates."""
    sources = set(await enabled_sources(session))
    thermo = await thermostat_config(session)
    if thermo is None or not thermo.enabled:
        return sorted(sources, key=lambda s: s.value)

    heat = thermo.heating_fuel
    if heat.value in ("electric", "heat_pump", "dual", "unknown"):
        sources.add(SourceType.electricity)
    if heat.value in ("gas", "dual", "unknown"):
        sources.add(SourceType.gas)
    if thermo.cooling_fuel.value in ("electric", "unknown"):
        sources.add(SourceType.electricity)
    return sorted(sources, key=lambda s: s.value)


async def thermostat_readings(
    session: AsyncSession, start: dt.datetime, end: dt.datetime
) -> list[tuple[dt.datetime, dict]]:
    config = await thermostat_config(session)
    if config is None or not config.enabled:
        return []
    rows = (
        await session.execute(
            select(ThermostatReading).where(
                ThermostatReading.entity_id == config.entity_id,
                ThermostatReading.time >= start,
                ThermostatReading.time <= end,
            )
        )
    ).scalars().all()
    tz = local_tz()
    return [
        (
            row.time.astimezone(tz),
            {
                "setpoint_c": row.setpoint_c,
                "current_temp_c": row.current_temp_c,
                "hvac_mode": row.hvac_mode,
                "hvac_action": row.hvac_action,
            },
        )
        for row in rows
    ]


async def readings_for_source(
    session: AsyncSession, source: SourceType, start: dt.datetime, end: dt.datetime
) -> list[tuple[dt.datetime, float]]:
    rows = (
        await session.execute(
            select(Reading.time, Reading.consumption, Reading.raw_value).where(
                Reading.source_type == source, Reading.time >= start, Reading.time <= end
            )
        )
    ).all()
    tz = local_tz()
    return [(row.time.astimezone(tz), row.consumption, row.raw_value) for row in rows]


async def weather_records(
    session: AsyncSession, start: dt.datetime, end: dt.datetime, include_forecast: bool = True
) -> list[dict]:
    tz = local_tz()
    obs_rows = (
        await session.execute(
            select(WeatherObservation).where(WeatherObservation.time >= start, WeatherObservation.time <= end)
        )
    ).scalars().all()
    records = [
        {
            "time": o.time.astimezone(tz),
            "temperature_c": o.temperature_c,
            "apparent_temperature_c": o.apparent_temperature_c,
            "humidity_pct": o.humidity_pct,
            "precipitation_mm": o.precipitation_mm,
            "wind_speed_kph": o.wind_speed_kph,
        }
        for o in obs_rows
    ]
    if include_forecast:
        latest_obs_time = max((o.time for o in obs_rows), default=start)
        fc_rows = (
            await session.execute(
                select(WeatherForecast).where(
                    WeatherForecast.time > latest_obs_time, WeatherForecast.time <= end
                )
            )
        ).scalars().all()
        records += [
            {
                "time": f.time.astimezone(tz),
                "temperature_c": f.temperature_c,
                "apparent_temperature_c": f.apparent_temperature_c,
                "humidity_pct": f.humidity_pct,
                "precipitation_mm": f.precipitation_mm,
                "wind_speed_kph": f.wind_speed_kph,
            }
            for f in fc_rows
        ]
    return records


async def pricing_map(session: AsyncSession) -> dict[SourceType, float]:
    rows = (await session.execute(select(PricingConfig))).scalars().all()
    return {r.source_type: r.price_per_unit for r in rows}


def full_usage_days(
    readings: list[tuple[dt.datetime, float]], today: dt.date
) -> set[dt.date]:
    times_by_day: dict[dt.date, list[dt.datetime]] = {}
    for ts, _ in readings:
        times_by_day.setdefault(ts.date(), []).append(ts)
    full_days: set[dt.date] = set()
    for day, times in times_by_day.items():
        if day >= today:
            continue
        span_hours = (max(times) - min(times)).total_seconds() / 3600
        if span_hours >= 12 or len(times) >= 12:
            full_days.add(day)
    return full_days


async def daily_usage_for_date(
    session: AsyncSession, source: SourceType, day: dt.date
) -> float | None:
    """Return total usage for a calendar day when the day has enough meter coverage."""
    tz = local_tz()
    start_dt = dt.datetime.combine(day, dt.time.min, tzinfo=tz)
    end_dt = dt.datetime.combine(day, dt.time.max, tzinfo=tz)
    readings = await readings_for_source(session, source, start_dt, end_dt)
    if day not in full_usage_days(readings, day + dt.timedelta(days=1)):
        return None
    daily = aggregate_daily_usage(readings)
    return daily.get(day)


async def build_usage_model(
    session: AsyncSession, source: SourceType
) -> tuple[
    RegressionResult | None,
    dict[dt.date, float],
    dict[dt.date, dict[str, float]],
    dict[dt.date, dict[str, float]],
    ThermostatConfig | None,
]:
    settings = get_settings()
    end_dt = dt.datetime.now(dt.timezone.utc)
    start_dt = end_dt - dt.timedelta(days=settings.weather_lookback_days)
    readings = await readings_for_source(session, source, start_dt, end_dt)
    weather = await weather_records(session, start_dt, end_dt, include_forecast=True)
    thermostat_cfg = await thermostat_config(session)
    thermostat_by_date = aggregate_daily_thermostat(
        await thermostat_readings(session, start_dt, end_dt)
    )

    tz = local_tz()
    today = dt.datetime.now(tz).date()
    full_days = full_usage_days(readings, today)

    usage_by_date = {d: v for d, v in aggregate_daily_usage(readings).items() if d in full_days}
    weather_by_date = aggregate_daily_weather(weather)
    weather_by_date = {d: w for d, w in weather_by_date.items() if d < today}

    model = None
    if len(usage_by_date) >= 3:
        thermo_subset = thermostat_by_date if thermostat_by_date else None
        model = fit_usage_model(
            usage_by_date,
            weather_by_date,
            thermo_subset,
            recency_half_life_days=settings.forecast_recency_half_life_days,
            reference_date=today,
        )
        if model and thermostat_cfg:
            model = route_model_for_source(
                model,
                source,
                thermostat_cfg.heating_fuel,
                thermostat_cfg.cooling_fuel,
                thermostat_cfg.heating_gas_fraction,
            )

    if model is None and thermostat_by_date and thermostat_cfg:
        shared_weather = {d: w for d, w in weather_by_date.items() if d in thermostat_by_date}
        proxy = fit_proxy_model_from_thermostat(shared_weather, thermostat_by_date)
        if proxy:
            model = route_model_for_source(
                proxy,
                source,
                thermostat_cfg.heating_fuel,
                thermostat_cfg.cooling_fuel,
                thermostat_cfg.heating_gas_fraction,
            )
            model.is_estimated = True
            model.estimation_method = "thermostat_proxy"

    return model, usage_by_date, weather_by_date, thermostat_by_date, thermostat_cfg


async def generate_raw_usage_forecast(
    session: AsyncSession,
    source: SourceType,
    days: int = 14,
    *,
    as_of: dt.datetime | None = None,
) -> tuple[dict[dt.date, float], RegressionResult, dict[dt.date, float]]:
    """Project usage from the fitted model and current weather forecast."""
    model, _, weather_by_date, thermostat_by_date, _ = await build_usage_model(session, source)
    if model is None:
        raise ValueError("Not enough historical data yet to build a forecast.")

    now = as_of or dt.datetime.now(dt.timezone.utc)
    tz = local_tz()
    fc_records = await weather_records(
        session, now, now + dt.timedelta(days=days), include_forecast=True
    )
    future_weather = aggregate_daily_weather(
        [r for r in fc_records if r["time"] > now.astimezone(tz)]
    )
    future_thermostat = forecast_thermostat_profile(
        thermostat_by_date,
        list(future_weather.keys()),
        weather_by_date,
        future_weather,
    )
    predicted = forecast_usage(model, future_weather, future_thermostat or None)

    daily_high: dict[dt.date, float] = {}
    for record in fc_records:
        if record["temperature_c"] is None:
            continue
        day = record["time"].astimezone(tz).date()
        if day not in daily_high or record["temperature_c"] > daily_high[day]:
            daily_high[day] = record["temperature_c"]

    return predicted, model, daily_high

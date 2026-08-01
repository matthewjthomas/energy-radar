"""Core data API: usage, weather, forecasts, correlation, and trend detection."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import (
    aggregate_daily_thermostat,
    aggregate_daily_usage,
    aggregate_daily_weather,
    aggregate_monthly_avg_temp,
    aggregate_monthly_usage,
    detect_trend_shifts,
    evaluate_event_impact,
    fit_proxy_model_from_thermostat,
    fit_usage_model,
    forecast_usage,
    route_model_for_source,
)
from app.config import get_settings
from app.db import get_session
from app.models import (
    EventMarker,
    HAEntityConfig,
    Location,
    PricingConfig,
    Reading,
    SourceType,
    ThermostatConfig,
    ThermostatReading,
    WeatherForecast,
    WeatherObservation,
)
from app.schemas import (
    CorrelationResult,
    EventImpact,
    EventMarkerOut,
    ForecastPoint,
    MonthlySummary,
    ThermostatConfigOut,
    ThermostatPoint,
    TrendShift,
    UsagePoint,
    WeatherPoint,
)

router = APIRouter(prefix="/api", tags=["data"])


def _local_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().app_timezone)


def _default_range() -> tuple[dt.datetime, dt.datetime]:
    end = dt.datetime.now(dt.timezone.utc)
    start = end - dt.timedelta(days=7)
    return start, end


def _parse_range(start: dt.date | None, end: dt.date | None) -> tuple[dt.datetime, dt.datetime]:
    if start is None or end is None:
        return _default_range()
    tz = _local_tz()
    start_dt = dt.datetime.combine(start, dt.time.min, tzinfo=tz)
    end_dt = dt.datetime.combine(end, dt.time.max, tzinfo=tz)
    return start_dt, end_dt


async def _enabled_sources(session: AsyncSession) -> list[SourceType]:
    rows = (
        await session.execute(
            select(HAEntityConfig.source_type).where(HAEntityConfig.enabled.is_(True)).distinct()
        )
    ).scalars().all()
    return sorted(set(rows), key=lambda s: s.value)


async def _thermostat_config(session: AsyncSession) -> ThermostatConfig | None:
    return (await session.execute(select(ThermostatConfig).limit(1))).scalar_one_or_none()


async def _thermostat_readings(
    session: AsyncSession, start: dt.datetime, end: dt.datetime
) -> list[tuple[dt.datetime, dict]]:
    config = await _thermostat_config(session)
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
    tz = _local_tz()
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


def _forecast_thermostat_profile(
    thermostat_by_date: dict[dt.date, dict[str, float]], days: list[dt.date]
) -> dict[dt.date, dict[str, float]]:
    if not thermostat_by_date:
        return {}
    avg_setpoint = sum(v["avg_setpoint_c"] for v in thermostat_by_date.values()) / len(thermostat_by_date)
    avg_heat = sum(v["heat_hours"] for v in thermostat_by_date.values()) / len(thermostat_by_date)
    avg_cool = sum(v["cool_hours"] for v in thermostat_by_date.values()) / len(thermostat_by_date)
    return {
        day: {
            "avg_setpoint_c": avg_setpoint,
            "heat_hours": avg_heat,
            "cool_hours": avg_cool,
        }
        for day in days
    }


async def _readings_for_source(
    session: AsyncSession, source: SourceType, start: dt.datetime, end: dt.datetime
) -> list[tuple[dt.datetime, float]]:
    rows = (
        await session.execute(
            select(Reading.time, Reading.consumption).where(
                Reading.source_type == source, Reading.time >= start, Reading.time <= end
            )
        )
    ).all()
    tz = _local_tz()
    return [(row.time.astimezone(tz), row.consumption) for row in rows]


async def _weather_records(
    session: AsyncSession, start: dt.datetime, end: dt.datetime, include_forecast: bool = True
) -> list[dict]:
    tz = _local_tz()
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


async def _pricing_map(session: AsyncSession) -> dict[SourceType, float]:
    rows = (await session.execute(select(PricingConfig))).scalars().all()
    return {r.source_type: r.price_per_unit for r in rows}


@router.get("/sources", response_model=list[SourceType])
async def get_enabled_sources(session: AsyncSession = Depends(get_session)):
    return await _enabled_sources(session)


@router.get("/sources/units", response_model=dict[str, str])
async def get_source_units(session: AsyncSession = Depends(get_session)):
    """Best-effort display unit per enabled source, taken from its mapped HA entity."""
    rows = (
        await session.execute(select(HAEntityConfig).where(HAEntityConfig.enabled.is_(True)))
    ).scalars().all()
    units: dict[str, str] = {}
    for row in rows:
        if row.unit and row.source_type.value not in units:
            units[row.source_type.value] = row.unit
    return units


@router.get("/usage", response_model=dict[str, list[UsagePoint]])
async def get_usage(
    start: dt.date | None = None,
    end: dt.date | None = None,
    session: AsyncSession = Depends(get_session),
):
    start_dt, end_dt = _parse_range(start, end)
    sources = await _enabled_sources(session)
    pricing = await _pricing_map(session)

    result: dict[str, list[UsagePoint]] = {}
    for source in sources:
        readings = await _readings_for_source(session, source, start_dt, end_dt)
        daily = aggregate_daily_usage(readings)
        price = pricing.get(source)
        result[source.value] = [
            UsagePoint(date=day, value=value, cost=(value * price if price else None))
            for day, value in sorted(daily.items())
        ]
    return result


@router.get("/weather", response_model=list[WeatherPoint])
async def get_weather(
    start: dt.date | None = None,
    end: dt.date | None = None,
    session: AsyncSession = Depends(get_session),
):
    start_dt, end_dt = _parse_range(start, end)
    records = await _weather_records(session, start_dt, end_dt)
    return [WeatherPoint(**r) for r in sorted(records, key=lambda r: r["time"])]


@router.get("/usage/monthly", response_model=list[MonthlySummary])
async def get_monthly_usage(session: AsyncSession = Depends(get_session)):
    """Cumulative usage totals and average temperature for each calendar month."""
    settings = get_settings()
    end_dt = dt.datetime.now(dt.timezone.utc)
    start_dt = end_dt - dt.timedelta(days=settings.weather_lookback_days)
    sources = await _enabled_sources(session)
    pricing = await _pricing_map(session)

    usage_by_month: dict[tuple[int, int], dict[str, float]] = {}
    cost_by_month: dict[tuple[int, int], dict[str, float | None]] = {}
    for source in sources:
        readings = await _readings_for_source(session, source, start_dt, end_dt)
        for (year, month), total in aggregate_monthly_usage(aggregate_daily_usage(readings)).items():
            usage_by_month.setdefault((year, month), {})[source.value] = total
            price = pricing.get(source)
            cost_by_month.setdefault((year, month), {})[source.value] = (
                total * price if price else None
            )

    weather = await _weather_records(session, start_dt, end_dt, include_forecast=False)
    temps_by_month = aggregate_monthly_avg_temp(aggregate_daily_weather(weather))

    months = sorted(set(usage_by_month) | set(temps_by_month), reverse=True)
    return [
        MonthlySummary(
            year=year,
            month=month,
            usage=usage_by_month.get((year, month), {}),
            cost=cost_by_month.get((year, month), {}),
            avg_temp_c=temps_by_month.get((year, month)),
        )
        for year, month in months
    ]


async def _build_model(session: AsyncSession, source: SourceType):
    # Training window matches the weather/HA lookback so the model uses all
    # available historical data as it accumulates over months and seasons.
    settings = get_settings()
    end_dt = dt.datetime.now(dt.timezone.utc)
    start_dt = end_dt - dt.timedelta(days=settings.weather_lookback_days)
    readings = await _readings_for_source(session, source, start_dt, end_dt)
    weather = await _weather_records(session, start_dt, end_dt, include_forecast=True)
    thermostat_cfg = await _thermostat_config(session)
    thermostat_by_date = aggregate_daily_thermostat(
        await _thermostat_readings(session, start_dt, end_dt)
    )

    tz = _local_tz()
    today = dt.datetime.now(tz).date()
    times_by_day: dict[dt.date, list[dt.datetime]] = {}
    for ts, _ in readings:
        times_by_day.setdefault(ts.date(), []).append(ts)
    full_days = set()
    for day, times in times_by_day.items():
        if day >= today:
            continue
        span_hours = (max(times) - min(times)).total_seconds() / 3600
        if span_hours >= 12 or len(times) >= 12:
            full_days.add(day)

    usage_by_date = {d: v for d, v in aggregate_daily_usage(readings).items() if d in full_days}
    weather_by_date = aggregate_daily_weather(weather)
    weather_by_date = {d: w for d, w in weather_by_date.items() if d < today}

    model = None
    if len(usage_by_date) >= 3:
        thermo_subset = thermostat_by_date if thermostat_by_date else None
        model = fit_usage_model(usage_by_date, weather_by_date, thermo_subset)

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


@router.get("/hvac", response_model=ThermostatConfigOut | None)
async def get_hvac_config(session: AsyncSession = Depends(get_session)):
    return await _thermostat_config(session)


@router.get("/thermostat", response_model=list[ThermostatPoint])
async def get_thermostat_series(
    start: dt.date | None = None,
    end: dt.date | None = None,
    session: AsyncSession = Depends(get_session),
):
    start_dt, end_dt = _parse_range(start, end)
    readings = await _thermostat_readings(session, start_dt, end_dt)
    return [
        ThermostatPoint(
            time=ts,
            setpoint_c=payload.get("setpoint_c"),
            current_temp_c=payload.get("current_temp_c"),
            hvac_mode=payload.get("hvac_mode"),
            hvac_action=payload.get("hvac_action"),
        )
        for ts, payload in readings
    ]


@router.get("/correlation", response_model=CorrelationResult)
async def get_correlation(source: SourceType, session: AsyncSession = Depends(get_session)):
    model, _, _, _, _ = await _build_model(session, source)
    if model is None:
        raise HTTPException(400, "Not enough historical data yet to compute a correlation.")
    return CorrelationResult(
        source_type=source,
        intercept=model.intercept,
        hdd_coef=model.hdd_coef,
        cdd_coef=model.cdd_coef,
        setpoint_coef=model.setpoint_coef,
        heat_hours_coef=model.heat_hours_coef,
        cool_hours_coef=model.cool_hours_coef,
        r_squared=model.r_squared,
        n_samples=model.n_samples,
        is_estimated=model.is_estimated,
        estimation_method=model.estimation_method,
    )


@router.get("/forecast/usage", response_model=list[ForecastPoint])
async def get_usage_forecast(
    source: SourceType, days: int = Query(14, ge=1, le=16), session: AsyncSession = Depends(get_session)
):
    model, _, _, thermostat_by_date, _ = await _build_model(session, source)
    if model is None:
        raise HTTPException(400, "Not enough historical data yet to build a forecast.")

    now = dt.datetime.now(dt.timezone.utc)
    tz = _local_tz()
    fc_records = await _weather_records(session, now, now + dt.timedelta(days=days), include_forecast=True)
    future_weather = aggregate_daily_weather([r for r in fc_records if r["time"] > now.astimezone(tz)])
    future_thermostat = _forecast_thermostat_profile(thermostat_by_date, list(future_weather.keys()))
    predicted = forecast_usage(model, future_weather, future_thermostat or None)

    daily_high: dict[dt.date, float] = {}
    for r in fc_records:
        if r["temperature_c"] is None:
            continue
        day = r["time"].astimezone(tz).date()
        if day not in daily_high or r["temperature_c"] > daily_high[day]:
            daily_high[day] = r["temperature_c"]

    pricing = await _pricing_map(session)
    price = pricing.get(source)
    return [
        ForecastPoint(
            date=day,
            predicted_value=value,
            predicted_cost=(value * price if price else None),
            high_temp_c=daily_high.get(day),
            is_estimated=model.is_estimated,
        )
        for day, value in sorted(predicted.items())
    ]


@router.get("/trends", response_model=list[TrendShift])
async def get_trends(source: SourceType, session: AsyncSession = Depends(get_session)):
    model, _, _, _, _ = await _build_model(session, source)
    if model is None or model.is_estimated:
        return []
    return [TrendShift(**s) for s in detect_trend_shifts(model)]


@router.get("/events/impact", response_model=list[EventImpact])
async def get_event_impacts(source: SourceType, session: AsyncSession = Depends(get_session)):
    _, usage_by_date, _, _, _ = await _build_model(session, source)
    events = (await session.execute(select(EventMarker).order_by(EventMarker.event_date))).scalars().all()
    impacts = []
    for event in events:
        impact = evaluate_event_impact(usage_by_date, event.event_date)
        impacts.append(EventImpact(event=EventMarkerOut.model_validate(event), **impact))
    return impacts

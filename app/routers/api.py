"""Core data API: usage, weather, forecasts, correlation, and trend detection."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import (
    aggregate_daily_usage,
    aggregate_daily_weather,
    detect_trend_shifts,
    evaluate_event_impact,
    fit_usage_model,
    forecast_usage,
)
from app.config import get_settings
from app.db import get_session
from app.models import EventMarker, HAEntityConfig, Location, PricingConfig, Reading, SourceType, WeatherForecast, WeatherObservation
from app.schemas import (
    CorrelationResult,
    EventImpact,
    EventMarkerOut,
    ForecastPoint,
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


async def _build_model(session: AsyncSession, source: SourceType):
    # Use up to a year of history for a robust weather/usage regression.
    end_dt = dt.datetime.now(dt.timezone.utc)
    start_dt = end_dt - dt.timedelta(days=365)
    readings = await _readings_for_source(session, source, start_dt, end_dt)
    weather = await _weather_records(session, start_dt, end_dt, include_forecast=False)
    usage_by_date = aggregate_daily_usage(readings)
    weather_by_date = aggregate_daily_weather(weather)
    model = fit_usage_model(usage_by_date, weather_by_date)
    return model, usage_by_date, weather_by_date


@router.get("/correlation", response_model=CorrelationResult)
async def get_correlation(source: SourceType, session: AsyncSession = Depends(get_session)):
    model, _, _ = await _build_model(session, source)
    if model is None:
        raise HTTPException(400, "Not enough historical data yet to compute a correlation.")
    return CorrelationResult(
        source_type=source,
        intercept=model.intercept,
        hdd_coef=model.hdd_coef,
        cdd_coef=model.cdd_coef,
        r_squared=model.r_squared,
        n_samples=model.n_samples,
    )


@router.get("/forecast/usage", response_model=list[ForecastPoint])
async def get_usage_forecast(
    source: SourceType, days: int = Query(14, ge=1, le=16), session: AsyncSession = Depends(get_session)
):
    model, _, _ = await _build_model(session, source)
    if model is None:
        raise HTTPException(400, "Not enough historical data yet to build a forecast.")

    now = dt.datetime.now(dt.timezone.utc)
    fc_records = await _weather_records(session, now, now + dt.timedelta(days=days), include_forecast=True)
    future_weather = aggregate_daily_weather([r for r in fc_records if r["time"] > now.astimezone(_local_tz())])
    predicted = forecast_usage(model, future_weather)

    pricing = await _pricing_map(session)
    price = pricing.get(source)
    return [
        ForecastPoint(date=day, predicted_value=value, predicted_cost=(value * price if price else None))
        for day, value in sorted(predicted.items())
    ]


@router.get("/trends", response_model=list[TrendShift])
async def get_trends(source: SourceType, session: AsyncSession = Depends(get_session)):
    model, _, _ = await _build_model(session, source)
    if model is None:
        return []
    return [TrendShift(**s) for s in detect_trend_shifts(model)]


@router.get("/events/impact", response_model=list[EventImpact])
async def get_event_impacts(source: SourceType, session: AsyncSession = Depends(get_session)):
    _, usage_by_date, _ = await _build_model(session, source)
    events = (await session.execute(select(EventMarker).order_by(EventMarker.event_date))).scalars().all()
    impacts = []
    for event in events:
        impact = evaluate_event_impact(usage_by_date, event.event_date)
        impacts.append(EventImpact(event=EventMarkerOut.model_validate(event), **impact))
    return impacts

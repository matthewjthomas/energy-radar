"""Core data API: usage, weather, forecasts, correlation, and trend detection."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import (
    aggregate_daily_usage,
    aggregate_daily_weather,
    aggregate_monthly_avg_temp,
    aggregate_monthly_usage,
    apply_bias_correction,
    detect_trend_shifts,
    evaluate_event_impact,
)
from app.config import get_settings
from app.db import get_session
from app.forecast_calibration import get_bias_offset
from app.forecasting import (
    build_usage_model,
    enabled_sources,
    forecastable_sources,
    generate_raw_usage_forecast,
    local_tz,
    pricing_map,
    readings_for_source,
    thermostat_config,
    thermostat_readings,
    weather_records,
)
from app.models import EventMarker, ForecastBias, SourceType, UsageForecastSnapshot
from app.schemas import (
    CorrelationResult,
    EventImpact,
    EventMarkerOut,
    ForecastAccuracyPoint,
    ForecastBiasOut,
    ForecastPoint,
    MonthlySummary,
    ThermostatConfigOut,
    ThermostatPoint,
    TrendShift,
    UsagePoint,
    WeatherPoint,
)

router = APIRouter(prefix="/api", tags=["data"])


def _default_range() -> tuple[dt.datetime, dt.datetime]:
    end = dt.datetime.now(dt.timezone.utc)
    start = end - dt.timedelta(days=7)
    return start, end


def _parse_range(start: dt.date | None, end: dt.date | None) -> tuple[dt.datetime, dt.datetime]:
    if start is None or end is None:
        return _default_range()
    tz = local_tz()
    start_dt = dt.datetime.combine(start, dt.time.min, tzinfo=tz)
    end_dt = dt.datetime.combine(end, dt.time.max, tzinfo=tz)
    return start_dt, end_dt


@router.get("/sources", response_model=list[SourceType])
async def get_enabled_sources(session: AsyncSession = Depends(get_session)):
    return await enabled_sources(session)


@router.get("/sources/units", response_model=dict[str, str])
async def get_source_units(session: AsyncSession = Depends(get_session)):
    """Best-effort display unit per enabled source, taken from its mapped HA entity."""
    from app.models import HAEntityConfig

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
    sources = await enabled_sources(session)
    pricing = await pricing_map(session)

    result: dict[str, list[UsagePoint]] = {}
    for source in sources:
        readings = await readings_for_source(session, source, start_dt, end_dt)
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
    records = await weather_records(session, start_dt, end_dt)
    return [WeatherPoint(**r) for r in sorted(records, key=lambda r: r["time"])]


@router.get("/usage/monthly", response_model=list[MonthlySummary])
async def get_monthly_usage(session: AsyncSession = Depends(get_session)):
    """Cumulative usage totals and average temperature for each calendar month."""
    settings = get_settings()
    end_dt = dt.datetime.now(dt.timezone.utc)
    start_dt = end_dt - dt.timedelta(days=settings.weather_lookback_days)
    sources = await enabled_sources(session)
    pricing = await pricing_map(session)

    usage_by_month: dict[tuple[int, int], dict[str, float]] = {}
    cost_by_month: dict[tuple[int, int], dict[str, float | None]] = {}
    for source in sources:
        readings = await readings_for_source(session, source, start_dt, end_dt)
        for (year, month), total in aggregate_monthly_usage(aggregate_daily_usage(readings)).items():
            usage_by_month.setdefault((year, month), {})[source.value] = total
            price = pricing.get(source)
            cost_by_month.setdefault((year, month), {})[source.value] = (
                total * price if price else None
            )

    weather = await weather_records(session, start_dt, end_dt, include_forecast=False)
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


@router.get("/hvac", response_model=ThermostatConfigOut | None)
async def get_hvac_config(session: AsyncSession = Depends(get_session)):
    return await thermostat_config(session)


@router.get("/thermostat", response_model=list[ThermostatPoint])
async def get_thermostat_series(
    start: dt.date | None = None,
    end: dt.date | None = None,
    session: AsyncSession = Depends(get_session),
):
    start_dt, end_dt = _parse_range(start, end)
    readings = await thermostat_readings(session, start_dt, end_dt)
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
    model, _, _, _, _ = await build_usage_model(session, source)
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
    try:
        predicted, model, daily_high = await generate_raw_usage_forecast(session, source, days=days)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    bias_offset = await get_bias_offset(session, source)
    pricing = await pricing_map(session)
    price = pricing.get(source)
    return [
        ForecastPoint(
            date=day,
            predicted_value=apply_bias_correction(raw, bias_offset),
            raw_predicted_value=raw,
            bias_correction=bias_offset if bias_offset else None,
            predicted_cost=(
                apply_bias_correction(raw, bias_offset) * price if price else None
            ),
            high_temp_c=daily_high.get(day),
            is_estimated=model.is_estimated,
        )
        for day, raw in sorted(predicted.items())
    ]


@router.get("/forecast/bias", response_model=list[ForecastBiasOut])
async def get_forecast_bias(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(ForecastBias))).scalars().all()
    return [ForecastBiasOut.model_validate(row) for row in rows]


@router.get("/forecast/accuracy", response_model=list[ForecastAccuracyPoint])
async def get_forecast_accuracy(
    source: SourceType,
    days: int = Query(30, ge=1, le=120),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.execute(
            select(UsageForecastSnapshot)
            .where(
                UsageForecastSnapshot.source_type == source,
                UsageForecastSnapshot.actual_value.is_not(None),
            )
            .order_by(UsageForecastSnapshot.forecast_date.desc())
            .limit(days)
        )
    ).scalars().all()
    points = []
    for row in reversed(rows):
        actual = row.actual_value
        if actual is None:
            continue
        error = row.predicted_value - actual
        pct_error = abs(error / actual) * 100 if actual else None
        points.append(
            ForecastAccuracyPoint(
                forecast_date=row.forecast_date,
                issued_date=row.issued_date,
                predicted_value=row.predicted_value,
                actual_value=actual,
                error=error,
                abs_pct_error=pct_error,
            )
        )
    return points


@router.get("/trends", response_model=list[TrendShift])
async def get_trends(source: SourceType, session: AsyncSession = Depends(get_session)):
    model, _, _, _, _ = await build_usage_model(session, source)
    if model is None or model.is_estimated:
        return []
    return [TrendShift(**s) for s in detect_trend_shifts(model)]


@router.get("/events/impact", response_model=list[EventImpact])
async def get_event_impacts(source: SourceType, session: AsyncSession = Depends(get_session)):
    _, usage_by_date, _, _, _ = await build_usage_model(session, source)
    events = (await session.execute(select(EventMarker).order_by(EventMarker.event_date))).scalars().all()
    impacts = []
    for event in events:
        impact = evaluate_event_impact(usage_by_date, event.event_date)
        impacts.append(EventImpact(event=EventMarkerOut.model_validate(event), **impact))
    return impacts

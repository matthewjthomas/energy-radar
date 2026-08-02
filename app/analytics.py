"""Statistical analytics: weather correlation, forecasting, and trend-change detection.

Deliberately dependency-light (numpy only) so it stays easy to build/maintain:
- Usage is modeled against heating/cooling degree-days (base 18C) via linear
  least squares regression per utility source.
- Thermostat setpoint and runtime hours improve the model when available.
- When meter data is sparse, estimates can be derived from thermostat + weather.
- Forecasted usage projects that regression onto the weather forecast.
- Trend-change detection compares rolling residual means to flag shifts, and
  quantifies the before/after impact of user-added event markers.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np

from app.models import CoolingFuelType, HeatingFuelType, SourceType

DEGREE_DAY_BASE_C = 18.0


def degree_days(avg_temp_c: float, base_c: float = DEGREE_DAY_BASE_C) -> tuple[float, float]:
    """Return (heating_degree_days, cooling_degree_days) for a given average temperature."""
    hdd = max(base_c - avg_temp_c, 0.0)
    cdd = max(avg_temp_c - base_c, 0.0)
    return hdd, cdd


def aggregate_daily_usage(
    readings: list[tuple[dt.datetime, float]],
) -> dict[dt.date, float]:
    """Sum consumption values by calendar date."""
    totals: dict[dt.date, float] = {}
    for timestamp, consumption in readings:
        if consumption is None:
            continue
        day = timestamp.date()
        totals[day] = totals.get(day, 0.0) + consumption
    return totals


def aggregate_monthly_usage(
    usage_by_date: dict[dt.date, float],
) -> dict[tuple[int, int], float]:
    """Sum daily usage totals into calendar months as (year, month) -> total."""
    monthly: dict[tuple[int, int], float] = {}
    for day, value in usage_by_date.items():
        key = (day.year, day.month)
        monthly[key] = monthly.get(key, 0.0) + value
    return monthly


def aggregate_monthly_avg_temp(
    weather_by_date: dict[dt.date, dict[str, float]],
) -> dict[tuple[int, int], float]:
    """Average daily temperatures into calendar months as (year, month) -> avg °C."""
    temps_by_month: dict[tuple[int, int], list[float]] = {}
    for day, weather in weather_by_date.items():
        avg_temp = weather.get("avg_temp_c")
        if avg_temp is None:
            continue
        key = (day.year, day.month)
        temps_by_month.setdefault(key, []).append(avg_temp)
    return {key: float(np.mean(temps)) for key, temps in temps_by_month.items()}


def aggregate_daily_weather(
    records: list[dict],
) -> dict[dt.date, dict[str, float]]:
    """Average hourly weather records into daily temp/degree-day/precipitation summaries."""
    by_day: dict[dt.date, list[dict]] = {}
    for record in records:
        day = record["time"].date()
        by_day.setdefault(day, []).append(record)

    summary: dict[dt.date, dict[str, float]] = {}
    for day, entries in by_day.items():
        temps = [e["temperature_c"] for e in entries if e.get("temperature_c") is not None]
        precip = [e["precipitation_mm"] for e in entries if e.get("precipitation_mm") is not None]
        if not temps:
            continue
        avg_temp = float(np.mean(temps))
        hdd, cdd = degree_days(avg_temp)
        summary[day] = {
            "avg_temp_c": avg_temp,
            "hdd": hdd,
            "cdd": cdd,
            "precipitation_mm": float(np.sum(precip)) if precip else 0.0,
        }
    return summary


def _is_heating_action(mode: str | None, action: str | None) -> bool:
    mode = (mode or "").lower()
    action = (action or "").lower()
    return action == "heating" or mode in ("heat", "emergency_heat")


def _is_cooling_action(mode: str | None, action: str | None) -> bool:
    mode = (mode or "").lower()
    action = (action or "").lower()
    return action == "cooling" or mode == "cool"


def aggregate_daily_thermostat(
    readings: list[tuple[dt.datetime, dict]],
    sample_hours: float = 0.25,
) -> dict[dt.date, dict[str, float]]:
    """Summarize thermostat snapshots into daily averages and runtime hours."""
    by_day: dict[dt.date, list[dict]] = {}
    for timestamp, payload in readings:
        by_day.setdefault(timestamp.date(), []).append(payload)

    summary: dict[dt.date, dict[str, float]] = {}
    for day, entries in by_day.items():
        setpoints = [e["setpoint_c"] for e in entries if e.get("setpoint_c") is not None]
        currents = [e["current_temp_c"] for e in entries if e.get("current_temp_c") is not None]
        heat_hours = sum(
            sample_hours for e in entries if _is_heating_action(e.get("hvac_mode"), e.get("hvac_action"))
        )
        cool_hours = sum(
            sample_hours for e in entries if _is_cooling_action(e.get("hvac_mode"), e.get("hvac_action"))
        )
        if not setpoints and not currents and heat_hours == 0 and cool_hours == 0:
            continue
        summary[day] = {
            "avg_setpoint_c": float(np.mean(setpoints)) if setpoints else DEGREE_DAY_BASE_C,
            "avg_current_temp_c": float(np.mean(currents)) if currents else None,
            "heat_hours": heat_hours,
            "cool_hours": cool_hours,
        }
    return summary


@dataclass
class RegressionResult:
    intercept: float
    hdd_coef: float
    cdd_coef: float
    r_squared: float
    n_samples: int
    setpoint_coef: float = 0.0
    heat_hours_coef: float = 0.0
    cool_hours_coef: float = 0.0
    is_estimated: bool = False
    estimation_method: str | None = None
    dates: list[dt.date] = field(default_factory=list)
    residuals: dict[dt.date, float] = field(default_factory=dict)

    def predict(
        self,
        hdd: float,
        cdd: float,
        avg_setpoint_c: float = DEGREE_DAY_BASE_C,
        heat_hours: float = 0.0,
        cool_hours: float = 0.0,
    ) -> float:
        return (
            self.intercept
            + self.hdd_coef * hdd
            + self.cdd_coef * cdd
            + self.setpoint_coef * avg_setpoint_c
            + self.heat_hours_coef * heat_hours
            + self.cool_hours_coef * cool_hours
        )


def _aligned_days(
    usage_by_date: dict[dt.date, float],
    weather_by_date: dict[dt.date, dict[str, float]],
    thermostat_by_date: dict[dt.date, dict[str, float]] | None = None,
) -> list[dt.date]:
    dates = sorted(d for d in usage_by_date if d in weather_by_date)
    if thermostat_by_date:
        dates = [d for d in dates if d in thermostat_by_date]
    return dates


def fit_usage_model(
    usage_by_date: dict[dt.date, float],
    weather_by_date: dict[dt.date, dict[str, float]],
    thermostat_by_date: dict[dt.date, dict[str, float]] | None = None,
) -> RegressionResult | None:
    """Fit usage against HDD/CDD and optional thermostat features via least squares."""
    dates = _aligned_days(usage_by_date, weather_by_date, thermostat_by_date)
    if len(dates) < 3:
        return None

    y = np.array([usage_by_date[d] for d in dates])
    x_hdd = np.array([weather_by_date[d]["hdd"] for d in dates])
    x_cdd = np.array([weather_by_date[d]["cdd"] for d in dates])
    columns = [np.ones_like(y), x_hdd, x_cdd]

    if thermostat_by_date:
        x_setpoint = np.array([thermostat_by_date[d]["avg_setpoint_c"] for d in dates])
        x_heat = np.array([thermostat_by_date[d]["heat_hours"] for d in dates])
        x_cool = np.array([thermostat_by_date[d]["cool_hours"] for d in dates])
        columns.extend([x_setpoint, x_heat, x_cool])

    design = np.column_stack(columns)
    coeffs, _, _, _ = np.linalg.lstsq(design, y, rcond=None)

    intercept = float(coeffs[0])
    hdd_coef = float(coeffs[1])
    cdd_coef = float(coeffs[2])
    setpoint_coef = float(coeffs[3]) if thermostat_by_date else 0.0
    heat_hours_coef = float(coeffs[4]) if thermostat_by_date else 0.0
    cool_hours_coef = float(coeffs[5]) if thermostat_by_date else 0.0

    predictions = design @ coeffs
    residuals = y - predictions
    ss_res = float(np.sum(residuals**2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    return RegressionResult(
        intercept=intercept,
        hdd_coef=hdd_coef,
        cdd_coef=cdd_coef,
        setpoint_coef=setpoint_coef,
        heat_hours_coef=heat_hours_coef,
        cool_hours_coef=cool_hours_coef,
        r_squared=float(r_squared),
        n_samples=len(dates),
        dates=dates,
        residuals=dict(zip(dates, residuals.tolist())),
    )


def fit_proxy_model_from_thermostat(
    weather_by_date: dict[dt.date, dict[str, float]],
    thermostat_by_date: dict[dt.date, dict[str, float]],
) -> RegressionResult | None:
    """Build a proxy load signal from thermostat + weather when meters are missing."""
    dates = sorted(d for d in weather_by_date if d in thermostat_by_date)
    if len(dates) < 3:
        return None

    proxy_by_date: dict[dt.date, float] = {}
    for day in dates:
        weather = weather_by_date[day]
        thermo = thermostat_by_date[day]
        setpoint = thermo["avg_setpoint_c"]
        outdoor = weather["avg_temp_c"]
        heat_gap = max(setpoint - outdoor, 0.0)
        cool_gap = max(outdoor - setpoint, 0.0)
        proxy_by_date[day] = (
            thermo["heat_hours"] * heat_gap
            + thermo["cool_hours"] * cool_gap
            + weather["hdd"] * 0.25
            + weather["cdd"] * 0.25
        )

    return fit_usage_model(proxy_by_date, weather_by_date, thermostat_by_date)


def heating_load_fraction(
    heating_fuel: HeatingFuelType, source: SourceType, gas_fraction: float = 0.5
) -> float:
    """Return the share of heating-related model terms that apply to this utility."""
    if source == SourceType.water:
        return 0.0
    if heating_fuel == HeatingFuelType.gas:
        return 1.0 if source == SourceType.gas else 0.0
    if heating_fuel in (HeatingFuelType.electric, HeatingFuelType.heat_pump):
        return 1.0 if source == SourceType.electricity else 0.0
    if heating_fuel == HeatingFuelType.dual:
        if source == SourceType.gas:
            return gas_fraction
        if source == SourceType.electricity:
            return 1.0 - gas_fraction
        return 0.0
    # Unknown: split modestly across electric and gas when both exist.
    if source == SourceType.electricity:
        return 0.6
    if source == SourceType.gas:
        return 0.4
    return 0.0


def cooling_load_fraction(cooling_fuel: CoolingFuelType, source: SourceType) -> float:
    if source != SourceType.electricity:
        return 0.0
    if cooling_fuel == CoolingFuelType.electric:
        return 1.0
    return 0.8


def route_model_for_source(
    model: RegressionResult,
    source: SourceType,
    heating_fuel: HeatingFuelType,
    cooling_fuel: CoolingFuelType,
    gas_fraction: float = 0.5,
) -> RegressionResult:
    """Scale HDD/CDD/runtime coefficients to the utility that supplies each load."""
    heat_frac = heating_load_fraction(heating_fuel, source, gas_fraction)
    cool_frac = cooling_load_fraction(cooling_fuel, source)
    heat_pump_scale = 0.7 if heating_fuel == HeatingFuelType.heat_pump else 1.0
    routed = RegressionResult(
        intercept=model.intercept * max(heat_frac, cool_frac, 0.15 if source != SourceType.water else 0.0),
        hdd_coef=model.hdd_coef * heat_frac * heat_pump_scale,
        cdd_coef=model.cdd_coef * cool_frac,
        setpoint_coef=model.setpoint_coef * max(heat_frac, cool_frac),
        heat_hours_coef=model.heat_hours_coef * heat_frac * heat_pump_scale,
        cool_hours_coef=model.cool_hours_coef * cool_frac,
        r_squared=model.r_squared,
        n_samples=model.n_samples,
        is_estimated=model.is_estimated,
        estimation_method=model.estimation_method,
        dates=model.dates,
        residuals=model.residuals,
    )
    return enforce_weather_coefficient_signs(routed, source, heating_fuel, cooling_fuel)


def enforce_weather_coefficient_signs(
    model: RegressionResult,
    source: SourceType,
    heating_fuel: HeatingFuelType,
    cooling_fuel: CoolingFuelType,
) -> RegressionResult:
    """Keep weather terms pointing the right way after collinear regression fits."""
    hdd_coef = model.hdd_coef
    cdd_coef = model.cdd_coef
    heat_hours_coef = model.heat_hours_coef
    cool_hours_coef = model.cool_hours_coef

    heats_with_source = heating_load_fraction(heating_fuel, source) > 0
    cools_with_source = cooling_load_fraction(cooling_fuel, source) > 0

    if heats_with_source:
        hdd_coef = max(hdd_coef, 0.0)
        heat_hours_coef = max(heat_hours_coef, 0.0)
    if cools_with_source:
        cdd_coef = max(cdd_coef, 0.0)
        cool_hours_coef = max(cool_hours_coef, 0.0)

    return RegressionResult(
        intercept=model.intercept,
        hdd_coef=hdd_coef,
        cdd_coef=cdd_coef,
        setpoint_coef=model.setpoint_coef,
        heat_hours_coef=heat_hours_coef,
        cool_hours_coef=cool_hours_coef,
        r_squared=model.r_squared,
        n_samples=model.n_samples,
        is_estimated=model.is_estimated,
        estimation_method=model.estimation_method,
        dates=model.dates,
        residuals=model.residuals,
    )


def forecast_thermostat_profile(
    thermostat_by_date: dict[dt.date, dict[str, float]],
    days: list[dt.date],
    historical_weather: dict[dt.date, dict[str, float]],
    future_weather: dict[dt.date, dict[str, float]],
) -> dict[dt.date, dict[str, float]]:
    """Project thermostat runtime for forecast days from degree-day ratios."""
    if not thermostat_by_date:
        return {}

    avg_setpoint = sum(v["avg_setpoint_c"] for v in thermostat_by_date.values()) / len(thermostat_by_date)

    heat_hours_when_heating = [
        thermostat_by_date[d]["heat_hours"]
        for d in thermostat_by_date
        if thermostat_by_date[d]["heat_hours"] > 0
    ]
    cool_hours_when_cooling = [
        thermostat_by_date[d]["cool_hours"]
        for d in thermostat_by_date
        if thermostat_by_date[d]["cool_hours"] > 0
    ]
    avg_heat = float(np.mean(heat_hours_when_heating)) if heat_hours_when_heating else 0.0
    avg_cool = float(np.mean(cool_hours_when_cooling)) if cool_hours_when_cooling else 0.0

    hist_hdd = [
        historical_weather[d]["hdd"]
        for d in thermostat_by_date
        if d in historical_weather and historical_weather[d]["hdd"] > 0
    ]
    hist_cdd = [
        historical_weather[d]["cdd"]
        for d in thermostat_by_date
        if d in historical_weather and historical_weather[d]["cdd"] > 0
    ]
    mean_hdd = float(np.mean(hist_hdd)) if hist_hdd else 0.0
    mean_cdd = float(np.mean(hist_cdd)) if hist_cdd else 0.0

    profile: dict[dt.date, dict[str, float]] = {}
    for day in days:
        weather = future_weather.get(day, {})
        hdd = weather.get("hdd", 0.0)
        cdd = weather.get("cdd", 0.0)
        heat_hours = avg_heat * (hdd / mean_hdd) if mean_hdd > 0 and hdd > 0 else 0.0
        cool_hours = avg_cool * (cdd / mean_cdd) if mean_cdd > 0 and cdd > 0 else 0.0
        profile[day] = {
            "avg_setpoint_c": avg_setpoint,
            "heat_hours": heat_hours,
            "cool_hours": cool_hours,
        }
    return profile


def forecast_usage(
    model: RegressionResult,
    weather_by_date: dict[dt.date, dict[str, float]],
    thermostat_by_date: dict[dt.date, dict[str, float]] | None = None,
) -> dict[dt.date, float]:
    """Project future usage from forecasted weather using the fitted model."""
    predicted: dict[dt.date, float] = {}
    for day, weather in sorted(weather_by_date.items()):
        thermo = (thermostat_by_date or {}).get(day, {})
        value = model.predict(
            weather["hdd"],
            weather["cdd"],
            thermo.get("avg_setpoint_c", DEGREE_DAY_BASE_C),
            thermo.get("heat_hours", 0.0),
            thermo.get("cool_hours", 0.0),
        )
        predicted[day] = max(value, 0.0)
    return predicted


def detect_trend_shifts(
    model: RegressionResult, window: int = 7, z_thresh: float = 1.5
) -> list[dict]:
    """Flag dates where the rolling mean of weather-adjusted residuals shifts
    significantly relative to the prior window, suggesting a change in
    underlying usage behavior not explained by weather alone."""
    if len(model.dates) < window * 2:
        return []

    residual_series = np.array([model.residuals[d] for d in model.dates])
    overall_std = float(np.std(residual_series)) or 1.0

    shifts = []
    for i in range(window, len(model.dates) - window):
        prior = residual_series[i - window : i]
        after = residual_series[i : i + window]
        shift = float(np.mean(after) - np.mean(prior))
        z = shift / overall_std
        if abs(z) >= z_thresh:
            shifts.append(
                {
                    "date": model.dates[i],
                    "shift": shift,
                    "z_score": z,
                }
            )
    collapsed: list[dict] = []
    for shift in shifts:
        if collapsed and (shift["date"] - collapsed[-1]["date"]).days <= window:
            if abs(shift["z_score"]) > abs(collapsed[-1]["z_score"]):
                collapsed[-1] = shift
        else:
            collapsed.append(shift)
    return collapsed


def evaluate_event_impact(
    usage_by_date: dict[dt.date, float], event_date: dt.date, window_days: int = 14
) -> dict:
    """Compare average daily usage in the window before vs. after an event marker."""
    before = [
        v
        for d, v in usage_by_date.items()
        if 0 < (event_date - d).days <= window_days
    ]
    after = [
        v
        for d, v in usage_by_date.items()
        if 0 <= (d - event_date).days <= window_days
    ]
    before_avg = float(np.mean(before)) if before else None
    after_avg = float(np.mean(after)) if after else None
    pct_change = None
    if before_avg and after_avg is not None and before_avg != 0:
        pct_change = ((after_avg - before_avg) / before_avg) * 100
    return {
        "before_avg": before_avg,
        "after_avg": after_avg,
        "pct_change": pct_change,
        "before_samples": len(before),
        "after_samples": len(after),
    }

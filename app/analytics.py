"""Statistical analytics: weather correlation, forecasting, and trend-change detection.

Deliberately dependency-light (numpy only) so it stays easy to build/maintain:
- Usage is modeled against heating/cooling degree-days (base 18C) via linear
  least squares regression per utility source.
- Forecasted usage projects that regression onto the weather forecast.
- Trend-change detection compares rolling residual means to flag shifts, and
  quantifies the before/after impact of user-added event markers.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np

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


@dataclass
class RegressionResult:
    intercept: float
    hdd_coef: float
    cdd_coef: float
    r_squared: float
    n_samples: int
    dates: list[dt.date] = field(default_factory=list)
    residuals: dict[dt.date, float] = field(default_factory=dict)

    def predict(self, hdd: float, cdd: float) -> float:
        return self.intercept + self.hdd_coef * hdd + self.cdd_coef * cdd


def fit_usage_model(
    usage_by_date: dict[dt.date, float],
    weather_by_date: dict[dt.date, dict[str, float]],
) -> RegressionResult | None:
    """Fit usage = intercept + hdd_coef*HDD + cdd_coef*CDD via least squares."""
    dates = sorted(d for d in usage_by_date if d in weather_by_date)
    if len(dates) < 3:
        return None

    y = np.array([usage_by_date[d] for d in dates])
    x_hdd = np.array([weather_by_date[d]["hdd"] for d in dates])
    x_cdd = np.array([weather_by_date[d]["cdd"] for d in dates])
    design = np.column_stack([np.ones_like(y), x_hdd, x_cdd])

    coeffs, _, _, _ = np.linalg.lstsq(design, y, rcond=None)
    intercept, hdd_coef, cdd_coef = coeffs

    predictions = design @ coeffs
    residuals = y - predictions
    ss_res = float(np.sum(residuals**2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    return RegressionResult(
        intercept=float(intercept),
        hdd_coef=float(hdd_coef),
        cdd_coef=float(cdd_coef),
        r_squared=float(r_squared),
        n_samples=len(dates),
        dates=dates,
        residuals=dict(zip(dates, residuals.tolist())),
    )


def forecast_usage(
    model: RegressionResult,
    weather_by_date: dict[dt.date, dict[str, float]],
) -> dict[dt.date, float]:
    """Project future usage from forecasted weather using the fitted model."""
    return {
        day: max(model.predict(w["hdd"], w["cdd"]), 0.0)
        for day, w in sorted(weather_by_date.items())
    }


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
    # Collapse consecutive detections into single events (keep the strongest).
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

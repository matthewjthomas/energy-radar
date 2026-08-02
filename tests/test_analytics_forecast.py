"""Forecast sanity checks for weather-sensitive usage models."""
from __future__ import annotations

import datetime as dt

from app.analytics import (
    RegressionResult,
    aggregate_daily_weather,
    enforce_weather_coefficient_signs,
    forecast_thermostat_profile,
    forecast_usage,
    route_model_for_source,
)
from app.models import CoolingFuelType, HeatingFuelType, SourceType


def _summer_weather(start: dt.date, days: int) -> dict[dt.date, dict[str, float]]:
    weather: dict[dt.date, dict[str, float]] = {}
    for i in range(days):
        day = start + dt.timedelta(days=i)
        temp_c = 20.0 + i * 1.5
        weather[day] = {
            "avg_temp_c": temp_c,
            "hdd": max(18.0 - temp_c, 0.0),
            "cdd": max(temp_c - 18.0, 0.0),
            "precipitation_mm": 0.0,
        }
    return weather


def test_gas_heat_electric_cool_forecast_rises_with_temperature():
    """Electricity should rise on hotter days when cooling is electric and heat is gas."""
    model = RegressionResult(
        intercept=50.0,
        hdd_coef=2.5,
        cdd_coef=-0.5,
        cool_hours_coef=1.2,
        heat_hours_coef=0.8,
        n_samples=30,
    )
    routed = route_model_for_source(
        model,
        SourceType.electricity,
        HeatingFuelType.gas,
        CoolingFuelType.electric,
    )
    assert routed.hdd_coef == 0.0
    assert routed.cdd_coef >= 0.0
    assert routed.cool_hours_coef >= 0.0

    start = dt.date(2026, 8, 1)
    future = _summer_weather(start, 14)
    historical = _summer_weather(start - dt.timedelta(days=30), 30)
    thermostat_history = {
        day: {
            "avg_setpoint_c": 22.0,
            "heat_hours": 0.0,
            "cool_hours": 6.0,
        }
        for day in historical
    }
    thermostat_history.update(
        {
            day: {
                "avg_setpoint_c": 22.0,
                "heat_hours": 0.0,
                "cool_hours": 0.0,
            }
            for day in historical
            if historical[day]["cdd"] == 0
        }
    )
    future_thermostat = forecast_thermostat_profile(
        thermostat_history, list(future.keys()), historical, future
    )
    predicted = forecast_usage(routed, future, future_thermostat)

    values = [predicted[day] for day in sorted(predicted)]
    assert values[-1] > values[0]


def test_forecast_thermostat_profile_scales_cooling_with_cdd():
    start = dt.date(2026, 8, 1)
    historical = _summer_weather(start - dt.timedelta(days=10), 10)
    future = _summer_weather(start, 5)
    thermostat_history = {
        day: {
            "avg_setpoint_c": 22.0,
            "heat_hours": 0.0,
            "cool_hours": 8.0 if historical[day]["cdd"] > 0 else 0.0,
        }
        for day in historical
    }
    profile = forecast_thermostat_profile(
        thermostat_history, list(future.keys()), historical, future
    )
    cool_hours = [profile[day]["cool_hours"] for day in sorted(profile)]
    assert cool_hours[-1] > cool_hours[0]


def test_enforce_weather_coefficient_signs_clamps_negative_cooling():
    model = RegressionResult(
        intercept=40.0,
        hdd_coef=0.0,
        cdd_coef=-1.5,
        cool_hours_coef=-0.2,
        n_samples=10,
    )
    fixed = enforce_weather_coefficient_signs(
        model,
        SourceType.electricity,
        HeatingFuelType.gas,
        CoolingFuelType.electric,
    )
    assert fixed.cdd_coef == 0.0
    assert fixed.cool_hours_coef == 0.0

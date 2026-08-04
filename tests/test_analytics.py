"""Unit tests for core analytics helpers."""
from __future__ import annotations

import datetime as dt

from app.analytics import (
    aggregate_daily_usage,
    aggregate_daily_weather,
    degree_days,
    detect_trend_shifts,
    filter_usage_outliers,
    fit_usage_model,
)


def test_degree_days_heating_and_cooling():
    hdd, cdd = degree_days(10.0)
    assert hdd == 8.0
    assert cdd == 0.0

    hdd, cdd = degree_days(25.0)
    assert hdd == 0.0
    assert cdd == 7.0


def test_aggregate_daily_usage_sums_by_date():
    readings = [
        (dt.datetime(2026, 8, 1, 12, tzinfo=dt.timezone.utc), 10.0),
        (dt.datetime(2026, 8, 1, 18, tzinfo=dt.timezone.utc), 5.0),
        (dt.datetime(2026, 8, 2, 12, tzinfo=dt.timezone.utc), 7.0),
    ]
    daily = aggregate_daily_usage(readings)
    assert daily[dt.date(2026, 8, 1)] == 15.0
    assert daily[dt.date(2026, 8, 2)] == 7.0


def test_aggregate_daily_usage_uses_daily_high_water_mark_on_reset():
    readings = [
        (dt.datetime(2026, 8, 3, 6, tzinfo=dt.timezone.utc), 10.0, 10.0),
        (dt.datetime(2026, 8, 3, 12, tzinfo=dt.timezone.utc), 20.0, 30.0),
        (dt.datetime(2026, 8, 3, 23, tzinfo=dt.timezone.utc), 25.0, 55.0),
    ]
    daily = aggregate_daily_usage(readings)
    assert daily[dt.date(2026, 8, 3)] == 55.0


def test_aggregate_daily_usage_accepts_database_reading_triples():
    readings = [
        (dt.datetime(2026, 8, 3, 1, tzinfo=dt.timezone.utc), None, 1.0),
        (dt.datetime(2026, 8, 3, 2, tzinfo=dt.timezone.utc), 4.0, 5.0),
        (dt.datetime(2026, 8, 3, 3, tzinfo=dt.timezone.utc), 6.0, 11.0),
    ]
    assert aggregate_daily_usage(readings)[dt.date(2026, 8, 3)] == 10.0


def test_filter_usage_outliers_removes_corrupted_spike():
    start = dt.date(2026, 7, 28)
    usage = {
        start + dt.timedelta(days=index): value
        for index, value in enumerate([72.0, 68.0, 75.0, 5734.0, 64.0, 71.0])
    }
    filtered = filter_usage_outliers(usage)
    assert len(filtered) == 5
    assert 5734.0 not in filtered.values()


def test_aggregate_daily_weather_averages_temperature():
    records = [
        {"time": dt.datetime(2026, 8, 1, 6, tzinfo=dt.timezone.utc), "temperature_c": 20.0, "precipitation_mm": 0.0},
        {"time": dt.datetime(2026, 8, 1, 18, tzinfo=dt.timezone.utc), "temperature_c": 30.0, "precipitation_mm": 1.0},
    ]
    daily = aggregate_daily_weather(records)
    day = daily[dt.date(2026, 8, 1)]
    assert day["avg_temp_c"] == 25.0
    assert day["hdd"] == 0.0
    assert day["cdd"] == 7.0
    assert day["precipitation_mm"] == 1.0


def test_fit_usage_model_positive_cdd_for_summer_electric_load():
    start = dt.date(2026, 7, 1)
    usage_by_date = {}
    weather_by_date = {}
    for i in range(14):
        day = start + dt.timedelta(days=i)
        temp = 22.0 + i
        weather_by_date[day] = {
            "avg_temp_c": temp,
            "hdd": max(18.0 - temp, 0.0),
            "cdd": max(temp - 18.0, 0.0),
            "precipitation_mm": 0.0,
        }
        usage_by_date[day] = 40.0 + weather_by_date[day]["cdd"] * 2.0

    model = fit_usage_model(usage_by_date, weather_by_date)
    assert model is not None
    assert model.cdd_coef > 0
    assert model.n_samples == 14


def test_detect_trend_shifts_flags_sustained_residual_change():
    from app.analytics import RegressionResult

    dates = [dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(30)]
    residuals = {day: 0.0 for day in dates}
    for day in dates[20:]:
        residuals[day] = 5.0
    model = RegressionResult(
        intercept=50.0,
        hdd_coef=1.0,
        cdd_coef=1.0,
        r_squared=0.5,
        n_samples=len(dates),
        dates=dates,
        residuals=residuals,
    )
    shifts = detect_trend_shifts(model, window=5, z_thresh=1.0)
    assert shifts

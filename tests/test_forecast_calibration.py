"""Forecast calibration unit tests."""
from __future__ import annotations

import datetime as dt

from app.analytics import apply_bias_correction, compute_mape, compute_rmse
from app.forecast_calibration import apply_source_bias, scoring_issued_date


def test_scoring_issued_date_is_one_day_ahead():
    target = dt.date(2026, 8, 3)
    assert scoring_issued_date(target) == dt.date(2026, 8, 2)


def test_apply_bias_correction_reduces_over_prediction():
    assert apply_bias_correction(70.0, 5.0) == 65.0
    assert apply_bias_correction(3.0, 5.0) == 1.5
    assert apply_bias_correction(70.0, -5600.0) == 105.0


def test_apply_source_bias_per_day():
    corrected = apply_source_bias(
        {dt.date(2026, 8, 1): 70.0, dt.date(2026, 8, 2): 68.0},
        4.0,
    )
    assert corrected[dt.date(2026, 8, 1)] == 66.0
    assert corrected[dt.date(2026, 8, 2)] == 64.0


def test_compute_mape_and_rmse():
    predicted = [100.0, 80.0, 60.0]
    actual = [90.0, 80.0, 50.0]
    assert compute_mape(predicted, actual) is not None
    assert compute_rmse(predicted, actual) is not None
    assert compute_rmse(predicted, actual) > 0

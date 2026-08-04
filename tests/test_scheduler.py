"""Scheduler helpers for HA reading ingestion."""
from __future__ import annotations

import datetime as dt

import pytest

from app.models import SourceType
from app.scheduler import (
    _merge_latest_state,
    _recent_history_start,
    _rows_from_history_points,
    _rows_from_statistics,
)


class _Cfg:
    source_type = SourceType.electricity
    entity_id = "sensor.power"
    is_cumulative = True


def test_recent_history_starts_at_local_midnight_for_complete_days():
    now = dt.datetime(2026, 8, 4, 1, tzinfo=dt.timezone.utc)
    start = _recent_history_start(now, "America/Chicago")
    # Local time is still Aug 3; seven calendar days are Jul 28 through Aug 3.
    assert start == dt.datetime(2026, 7, 28, 5, tzinfo=dt.timezone.utc)


def test_rows_from_history_points_computes_daily_reset_deltas():
    cfg = _Cfg()
    points = [
        (dt.datetime(2026, 8, 4, 0, tzinfo=dt.timezone.utc), 1.0),
        (dt.datetime(2026, 8, 4, 1, tzinfo=dt.timezone.utc), 4.0),
        (dt.datetime(2026, 8, 4, 2, tzinfo=dt.timezone.utc), 7.0),
    ]
    rows = _rows_from_history_points(cfg, points)
    assert rows[0]["consumption"] is None
    assert rows[1]["consumption"] == 3.0
    assert rows[2]["consumption"] == 3.0


def test_merge_latest_state_appends_newer_live_reading():
    points = [
        (dt.datetime(2026, 8, 4, 12, tzinfo=dt.timezone.utc), 20.0),
    ]
    latest = (dt.datetime(2026, 8, 4, 18, tzinfo=dt.timezone.utc), 51.2)

    merged = _merge_latest_state(points, latest)

    assert merged[-1] == latest
    assert len(merged) == 2


def test_merge_latest_state_replaces_same_timestamp():
    ts = dt.datetime(2026, 8, 4, 18, tzinfo=dt.timezone.utc)
    points = [(ts, 40.0)]
    latest = (ts, 51.2)

    merged = _merge_latest_state(points, latest)

    assert merged == [latest]


@pytest.mark.asyncio
async def test_statistics_rows_use_consumption_without_raw_reset_detection():
    class _Client:
        async def get_statistics(self, entity_id, start, end, period):
            return [
                {"time": start, "sum": 100.0, "state": None, "mean": None},
                {
                    "time": start + dt.timedelta(hours=1),
                    "sum": 105.0,
                    "state": None,
                    "mean": None,
                },
                {
                    "time": start + dt.timedelta(hours=2),
                    "sum": 95.0,
                    "state": None,
                    "mean": None,
                },
            ]

    start = dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)
    rows = await _rows_from_statistics(
        _Client(),
        _Cfg(),
        start,
        start + dt.timedelta(hours=3),
        None,
    )

    assert rows is not None
    assert [row["raw_value"] for row in rows] == [0.0, 0.0, 0.0]
    assert [row["consumption"] for row in rows] == [None, 5.0, None]

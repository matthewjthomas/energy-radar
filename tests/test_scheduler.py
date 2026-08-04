"""Scheduler helpers for HA reading ingestion."""
from __future__ import annotations

import datetime as dt

from app.models import SourceType
from app.scheduler import _recent_history_start, _rows_from_history_points


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

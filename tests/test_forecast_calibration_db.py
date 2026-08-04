"""Database-backed forecast calibration tests."""
from __future__ import annotations

import datetime as dt

import pytest

from app.forecast_calibration import (
    score_forecast_for_date,
    update_bias_for_source,
    upsert_forecast_snapshots,
)
from app.models import SourceType, UsageForecastSnapshot
from sqlalchemy import select


@pytest.mark.usefixtures("requires_database")
@pytest.mark.asyncio
async def test_upsert_and_score_forecast_snapshot(db_session):
    issued = dt.date(2026, 8, 1)
    target = dt.date(2026, 8, 2)
    await upsert_forecast_snapshots(
        db_session,
        issued,
        SourceType.electricity,
        {target: 65.0},
    )
    await db_session.commit()

    row = (
        await db_session.execute(
            select(UsageForecastSnapshot).where(
                UsageForecastSnapshot.issued_date == issued,
                UsageForecastSnapshot.forecast_date == target,
            )
        )
    ).scalar_one()
    assert row.predicted_value == 65.0

    # Without meter readings, scoring should not attach an actual value.
    scored = await score_forecast_for_date(db_session, SourceType.electricity, target)
    assert scored is None


@pytest.mark.usefixtures("requires_database")
@pytest.mark.asyncio
async def test_update_bias_from_scored_rows(db_session):
    issued = dt.date(2026, 8, 1)
    for i, (predicted, actual) in enumerate([(70.0, 60.0), (68.0, 66.0), (72.0, 70.0)]):
        db_session.add(
            UsageForecastSnapshot(
                issued_date=issued,
                forecast_date=dt.date(2026, 8, 2) + dt.timedelta(days=i),
                source_type=SourceType.electricity,
                predicted_value=predicted,
                actual_value=actual,
                scored_at=dt.datetime.now(dt.timezone.utc),
            )
        )
    await db_session.commit()

    updated = await update_bias_for_source(db_session, SourceType.electricity)
    assert updated is True
    await db_session.commit()

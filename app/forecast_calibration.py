"""Store daily forecasts, score against actuals, and maintain bias corrections."""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import apply_bias_correction, compute_mape, compute_rmse
from app.config import get_settings
from app.forecasting import (
    daily_usage_for_date,
    forecastable_sources,
    generate_raw_usage_forecast,
    local_tz,
)
from app.models import ForecastBias, SourceType, UsageForecastSnapshot

logger = logging.getLogger(__name__)


def scoring_issued_date(forecast_date: dt.date) -> dt.date:
    """Prefer the 1-day-ahead snapshot issued the day before the target."""
    return forecast_date - dt.timedelta(days=1)


async def get_bias_offset(session: AsyncSession, source: SourceType) -> float:
    row = (
        await session.execute(select(ForecastBias).where(ForecastBias.source_type == source))
    ).scalar_one_or_none()
    return row.bias_offset if row else 0.0


async def upsert_forecast_snapshots(
    session: AsyncSession,
    issued_date: dt.date,
    source: SourceType,
    predictions: dict[dt.date, float],
) -> int:
    rows = [
        {
            "issued_date": issued_date,
            "forecast_date": day,
            "source_type": source,
            "predicted_value": value,
        }
        for day, value in sorted(predictions.items())
    ]
    if not rows:
        return 0

    for row in rows:
        stmt = pg_insert(UsageForecastSnapshot).values(row)
        stmt = stmt.on_conflict_do_update(
            index_elements=["issued_date", "forecast_date", "source_type"],
            set_={"predicted_value": stmt.excluded.predicted_value},
        )
        await session.execute(stmt)
    return len(rows)


async def score_forecast_for_date(
    session: AsyncSession,
    source: SourceType,
    forecast_date: dt.date,
) -> UsageForecastSnapshot | None:
    """Attach yesterday's actual usage to the best matching stored forecast."""
    actual = await daily_usage_for_date(session, source, forecast_date)
    if actual is None:
        return None

    preferred_issued = scoring_issued_date(forecast_date)
    snapshot = (
        await session.execute(
            select(UsageForecastSnapshot).where(
                UsageForecastSnapshot.source_type == source,
                UsageForecastSnapshot.forecast_date == forecast_date,
                UsageForecastSnapshot.issued_date == preferred_issued,
            )
        )
    ).scalar_one_or_none()

    if snapshot is None:
        snapshot = (
            await session.execute(
                select(UsageForecastSnapshot)
                .where(
                    UsageForecastSnapshot.source_type == source,
                    UsageForecastSnapshot.forecast_date == forecast_date,
                    UsageForecastSnapshot.issued_date < forecast_date,
                )
                .order_by(UsageForecastSnapshot.issued_date.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    if snapshot is None:
        return None

    snapshot.actual_value = actual
    snapshot.scored_at = dt.datetime.now(dt.timezone.utc)
    return snapshot


async def update_bias_for_source(session: AsyncSession, source: SourceType) -> bool:
    settings = get_settings()
    rows = (
        await session.execute(
            select(UsageForecastSnapshot)
            .where(
                UsageForecastSnapshot.source_type == source,
                UsageForecastSnapshot.actual_value.is_not(None),
            )
            .order_by(UsageForecastSnapshot.forecast_date.desc())
            .limit(settings.forecast_bias_window_days)
        )
    ).scalars().all()
    if not rows:
        return False

    predicted = [row.predicted_value for row in rows]
    actual = [row.actual_value for row in rows if row.actual_value is not None]
    if len(predicted) != len(actual):
        return False

    errors = [p - a for p, a in zip(predicted, actual)]
    bias_offset = float(sum(errors) / len(errors))

    short_n = settings.forecast_bias_short_window_days
    short_pred = predicted[:short_n]
    short_actual = actual[:short_n]

    payload = {
        "source_type": source,
        "bias_offset": bias_offset,
        "mape_7d": compute_mape(short_pred, short_actual),
        "rmse_7d": compute_rmse(short_pred, short_actual),
        "mape_30d": compute_mape(predicted, actual),
        "rmse_30d": compute_rmse(predicted, actual),
        "scored_samples": len(rows),
        "updated_at": dt.datetime.now(dt.timezone.utc),
    }
    stmt = pg_insert(ForecastBias).values(payload)
    stmt = stmt.on_conflict_do_update(
        index_elements=["source_type"],
        set_={k: v for k, v in payload.items() if k != "source_type"},
    )
    await session.execute(stmt)
    return True


async def run_daily_forecast_calibration(
    *,
    run_date: dt.date | None = None,
) -> dict[str, int]:
    """Score the previous day, refresh bias, and store a new forecast snapshot."""
    settings = get_settings()
    tz = local_tz()
    today = run_date or dt.datetime.now(tz).date()
    yesterday = today - dt.timedelta(days=1)
    as_of = dt.datetime.combine(today, dt.time.min, tzinfo=tz).astimezone(dt.timezone.utc)

    stats = {"scored": 0, "stored": 0, "bias_updated": 0, "skipped_sources": 0}

    async def _run(session: AsyncSession) -> None:
        sources = await forecastable_sources(session)
        for source in sources:
            scored = await score_forecast_for_date(session, source, yesterday)
            if scored:
                stats["scored"] += 1

            if await update_bias_for_source(session, source):
                stats["bias_updated"] += 1

            try:
                predictions, _, _ = await generate_raw_usage_forecast(
                    session,
                    source,
                    days=settings.forecast_store_days,
                    as_of=as_of,
                )
                stats["stored"] += await upsert_forecast_snapshots(
                    session, today, source, predictions
                )
            except ValueError:
                stats["skipped_sources"] += 1
                logger.info("Skipping forecast snapshot for %s (model not ready)", source.value)
            except Exception:
                stats["skipped_sources"] += 1
                logger.warning("Failed to store forecast snapshot for %s", source.value, exc_info=True)

    from app.db import session_scope

    async with session_scope() as session:
        await _run(session)
        await session.commit()

    logger.info(
        "Daily forecast calibration for %s: scored=%d stored=%d bias_updated=%d skipped=%d",
        today,
        stats["scored"],
        stats["stored"],
        stats["bias_updated"],
        stats["skipped_sources"],
    )
    return stats


def apply_source_bias(predicted: dict[dt.date, float], bias_offset: float) -> dict[dt.date, float]:
    return {day: apply_bias_correction(value, bias_offset) for day, value in predicted.items()}

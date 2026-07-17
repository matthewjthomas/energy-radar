"""SQLAlchemy ORM models."""
from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SourceType(str, enum.Enum):
    electricity = "electricity"
    gas = "gas"
    water = "water"


class Location(Base):
    """The single address used to look up weather data."""

    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    address: Mapped[str] = mapped_column(String(255))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc)
    )


class HAEntityConfig(Base):
    """Mapping of a Home Assistant entity to one of the tracked utility sources."""

    __tablename__ = "ha_entity_configs"
    __table_args__ = (UniqueConstraint("source_type", "entity_id", name="uq_source_entity"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_type: Mapped[SourceType] = mapped_column(Enum(SourceType))
    entity_id: Mapped[str] = mapped_column(String(255))
    friendly_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # True for monotonically increasing "total_increasing" sensors (most HA energy
    # sensors); consumption is derived from the delta between consecutive readings.
    is_cumulative: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc)
    )


class PricingConfig(Base):
    """Optional price per unit so usage can be converted into an estimated cost."""

    __tablename__ = "pricing_configs"

    source_type: Mapped[SourceType] = mapped_column(Enum(SourceType), primary_key=True)
    price_per_unit: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(8), default="USD")


class Reading(Base):
    """A single consumption reading pulled from Home Assistant, stored as a hypertable."""

    __tablename__ = "readings"

    time: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    source_type: Mapped[SourceType] = mapped_column(Enum(SourceType), primary_key=True)
    entity_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    raw_value: Mapped[float] = mapped_column(Float)
    # Consumption during the interval ending at `time` (delta of cumulative sensors).
    consumption: Mapped[float | None] = mapped_column(Float, nullable=True)


class WeatherObservation(Base):
    """Actual (historical) weather observations, stored as a hypertable."""

    __tablename__ = "weather_observations"

    time: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    apparent_temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    humidity_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    precipitation_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_speed_kph: Mapped[float | None] = mapped_column(Float, nullable=True)


class WeatherForecast(Base):
    """Forecasted weather, refreshed periodically and upserted by target time."""

    __tablename__ = "weather_forecasts"

    time: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    generated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    apparent_temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    humidity_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    precipitation_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    precipitation_probability_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_speed_kph: Mapped[float | None] = mapped_column(Float, nullable=True)


class EventMarker(Base):
    """A user-added marker for a change that may have impacted energy usage."""

    __tablename__ = "event_markers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_date: Mapped[dt.date] = mapped_column(Date)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc)
    )

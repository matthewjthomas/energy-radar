"""Pydantic request/response schemas for the API."""
from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict

from app.models import SourceType


class LocationIn(BaseModel):
    address: str


class LocationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    address: str
    latitude: float
    longitude: float
    timezone: str


class HAEntityConfigIn(BaseModel):
    source_type: SourceType
    entity_id: str
    friendly_name: str | None = None
    unit: str | None = None
    is_cumulative: bool = True
    enabled: bool = True


class HAEntityConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_type: SourceType
    entity_id: str
    friendly_name: str | None
    unit: str | None
    is_cumulative: bool
    enabled: bool


class DiscoveredEntity(BaseModel):
    entity_id: str
    friendly_name: str
    unit: str | None = None
    device_class: str | None = None
    state: str | None = None


class PricingConfigIn(BaseModel):
    source_type: SourceType
    price_per_unit: float
    currency: str = "USD"


class PricingConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source_type: SourceType
    price_per_unit: float
    currency: str


class EventMarkerIn(BaseModel):
    event_date: dt.date
    title: str
    description: str | None = None


class EventMarkerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_date: dt.date
    title: str
    description: str | None


class UsagePoint(BaseModel):
    date: dt.date
    value: float
    cost: float | None = None


class WeatherPoint(BaseModel):
    time: dt.datetime
    temperature_c: float | None
    apparent_temperature_c: float | None
    humidity_pct: float | None
    precipitation_mm: float | None
    wind_speed_kph: float | None


class ForecastPoint(BaseModel):
    date: dt.date
    predicted_value: float
    predicted_cost: float | None = None


class TrendShift(BaseModel):
    date: dt.date
    shift: float
    z_score: float


class EventImpact(BaseModel):
    event: EventMarkerOut
    before_avg: float | None
    after_avg: float | None
    pct_change: float | None
    before_samples: int
    after_samples: int


class CorrelationResult(BaseModel):
    source_type: SourceType
    intercept: float
    hdd_coef: float
    cdd_coef: float
    r_squared: float
    n_samples: int

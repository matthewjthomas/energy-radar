"""Pydantic request/response schemas for the API."""
from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict

from app.models import CoolingFuelType, HeatingFuelType, SourceType


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
    entity_kind: str = "sensor"


class ThermostatConfigIn(BaseModel):
    entity_id: str
    friendly_name: str | None = None
    heating_fuel: HeatingFuelType = HeatingFuelType.gas
    cooling_fuel: CoolingFuelType = CoolingFuelType.electric
    heating_gas_fraction: float = 0.5
    enabled: bool = True


class ThermostatConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entity_id: str
    friendly_name: str | None
    heating_fuel: HeatingFuelType
    cooling_fuel: CoolingFuelType
    heating_gas_fraction: float
    enabled: bool


class ThermostatPoint(BaseModel):
    time: dt.datetime
    setpoint_c: float | None
    current_temp_c: float | None
    hvac_mode: str | None
    hvac_action: str | None


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
    raw_predicted_value: float | None = None
    bias_correction: float | None = None
    predicted_cost: float | None = None
    high_temp_c: float | None = None
    is_estimated: bool = False


class ForecastBiasOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source_type: SourceType
    bias_offset: float
    mape_7d: float | None
    rmse_7d: float | None
    mape_30d: float | None
    rmse_30d: float | None
    scored_samples: int
    updated_at: dt.datetime


class ForecastAccuracyPoint(BaseModel):
    forecast_date: dt.date
    issued_date: dt.date
    predicted_value: float
    actual_value: float
    error: float
    abs_pct_error: float | None = None


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
    setpoint_coef: float = 0.0
    heat_hours_coef: float = 0.0
    cool_hours_coef: float = 0.0
    r_squared: float
    n_samples: int
    is_estimated: bool = False
    estimation_method: str | None = None


class MonthlySummary(BaseModel):
    year: int
    month: int
    usage: dict[str, float]
    cost: dict[str, float | None] = {}
    avg_temp_c: float | None = None

"""Open-Meteo client: address geocoding, historical archive, and forecast data.

Open-Meteo requires no API key. Docs: https://open-meteo.com/en/docs
"""
from __future__ import annotations

import datetime as dt
from typing import Any

import httpx

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

HOURLY_VARS = "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,wind_speed_10m"
FORECAST_HOURLY_VARS = HOURLY_VARS + ",precipitation_probability"


class GeocodeResult(dict):
    """dict with keys: latitude, longitude, timezone, display_name"""


async def geocode_address(address: str) -> GeocodeResult | None:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(GEOCODE_URL, params={"name": address, "count": 1})
        resp.raise_for_status()
        data = resp.json()

    results = data.get("results")
    if not results:
        return None
    top = results[0]
    parts = [top.get("name"), top.get("admin1"), top.get("country")]
    display_name = ", ".join(p for p in parts if p)
    return GeocodeResult(
        latitude=top["latitude"],
        longitude=top["longitude"],
        timezone=top.get("timezone", "UTC"),
        display_name=display_name,
    )


def _parse_hourly(payload: dict[str, Any], with_probability: bool = False) -> list[dict[str, Any]]:
    hourly = payload.get("hourly", {})
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    apparent = hourly.get("apparent_temperature", [])
    humidity = hourly.get("relative_humidity_2m", [])
    precip = hourly.get("precipitation", [])
    wind = hourly.get("wind_speed_10m", [])
    prob = hourly.get("precipitation_probability", []) if with_probability else None

    records = []
    for i, t in enumerate(times):
        record = {
            "time": dt.datetime.fromisoformat(t),
            "temperature_c": temps[i] if i < len(temps) else None,
            "apparent_temperature_c": apparent[i] if i < len(apparent) else None,
            "humidity_pct": humidity[i] if i < len(humidity) else None,
            "precipitation_mm": precip[i] if i < len(precip) else None,
            "wind_speed_kph": wind[i] if i < len(wind) else None,
        }
        if with_probability:
            record["precipitation_probability_pct"] = prob[i] if prob and i < len(prob) else None
        records.append(record)
    return records


async def get_historical_weather(
    latitude: float, longitude: float, start_date: dt.date, end_date: dt.date, timezone: str = "UTC"
) -> list[dict[str, Any]]:
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "hourly": HOURLY_VARS,
        "timezone": timezone,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(ARCHIVE_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
    return _parse_hourly(data)


async def get_forecast_weather(
    latitude: float, longitude: float, timezone: str = "UTC", days: int = 16
) -> list[dict[str, Any]]:
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": FORECAST_HOURLY_VARS,
        "forecast_days": min(days, 16),
        "timezone": timezone,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(FORECAST_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
    return _parse_hourly(data, with_probability=True)

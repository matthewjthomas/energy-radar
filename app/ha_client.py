"""Minimal Home Assistant REST API client."""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class HomeAssistantError(RuntimeError):
    pass


class HomeAssistantClient:
    def __init__(self, base_url: str, token: str, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        self._timeout = timeout

    async def test_connection(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(f"{self.base_url}/api/", headers=self._headers)
                return resp.status_code == 200
        except httpx.HTTPError:
            logger.warning("Home Assistant connection test failed", exc_info=True)
            return False

    async def list_sensor_entities(self) -> list[dict[str, Any]]:
        """Return sensor entities that look like energy/gas/water meters."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(f"{self.base_url}/api/states", headers=self._headers)
            resp.raise_for_status()
            states = resp.json()

        candidates = []
        for state in states:
            entity_id = state.get("entity_id", "")
            if not entity_id.startswith("sensor."):
                continue
            attrs = state.get("attributes", {})
            unit = attrs.get("unit_of_measurement", "")
            device_class = attrs.get("device_class", "")
            if device_class in ("energy", "gas", "water") or unit in (
                "kWh",
                "Wh",
                "m³",
                "m3",
                "ft³",
                "gal",
                "L",
                "CCF",
            ):
                candidates.append(
                    {
                        "entity_id": entity_id,
                        "friendly_name": attrs.get("friendly_name", entity_id),
                        "unit": unit,
                        "device_class": device_class,
                        "state": state.get("state"),
                    }
                )
        return candidates

    async def get_latest_state(self, entity_id: str) -> tuple[dt.datetime, float] | None:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(
                f"{self.base_url}/api/states/{entity_id}", headers=self._headers
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
        try:
            value = float(data["state"])
        except (KeyError, ValueError, TypeError):
            return None
        last_changed = dt.datetime.fromisoformat(data["last_updated"].replace("Z", "+00:00"))
        return last_changed, value

    async def get_history(
        self, entity_id: str, start: dt.datetime, end: dt.datetime
    ) -> list[tuple[dt.datetime, float]]:
        """Fetch minimal-response history for a single entity between start and end."""
        params = {
            "filter_entity_id": entity_id,
            "end_time": end.isoformat(),
            "minimal_response": "true",
            "no_attributes": "true",
        }
        url = f"{self.base_url}/api/history/period/{start.isoformat()}"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(url, headers=self._headers, params=params)
            if resp.status_code != 200:
                raise HomeAssistantError(
                    f"HA history request failed ({resp.status_code}): {resp.text[:200]}"
                )
            data = resp.json()

        if not data:
            return []

        points: list[tuple[dt.datetime, float]] = []
        for entry in data[0]:
            try:
                value = float(entry["state"])
            except (KeyError, ValueError, TypeError):
                continue
            ts_raw = entry.get("last_changed") or entry.get("last_updated")
            timestamp = dt.datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            points.append((timestamp, value))
        return points

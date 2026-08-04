"""API smoke tests."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_health_endpoint(async_client):
    response = await async_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_sources_empty_by_default(async_client):
    response = await async_client.get("/api/sources")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_forecast_usage_requires_data(async_client):
    response = await async_client.get("/api/forecast/usage?source=electricity")
    assert response.status_code == 400
    assert "Not enough historical data" in response.json()["detail"]


@pytest.mark.asyncio
async def test_forecast_bias_empty(async_client):
    response = await async_client.get("/api/forecast/bias")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_dashboard_page_renders(async_client):
    response = await async_client.get("/")
    assert response.status_code == 200
    assert "Energy Radar" in response.text or "Your home" in response.text

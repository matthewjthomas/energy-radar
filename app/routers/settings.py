"""Settings API: Home Assistant connection, entity mapping, location, pricing, event markers."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.ha_client import HomeAssistantClient
from app.models import EventMarker, HAEntityConfig, Location, PricingConfig
from app.scheduler import poll_ha_readings, poll_weather_forecast, poll_weather_historical
from app.schemas import (
    DiscoveredEntity,
    EventMarkerIn,
    EventMarkerOut,
    HAEntityConfigIn,
    HAEntityConfigOut,
    LocationIn,
    LocationOut,
    PricingConfigIn,
    PricingConfigOut,
)
from app.weather_client import geocode_address, resolve_timezone

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/ha/status")
async def ha_status() -> dict:
    settings = get_settings()
    if not settings.ha_configured:
        return {"configured": False, "connected": False}
    client = HomeAssistantClient(settings.ha_url, settings.ha_token)
    connected = await client.test_connection()
    return {"configured": True, "connected": connected}


@router.get("/ha/discover", response_model=list[DiscoveredEntity])
async def ha_discover_entities() -> list[DiscoveredEntity]:
    settings = get_settings()
    if not settings.ha_configured:
        raise HTTPException(400, "Home Assistant is not configured (HA_URL/HA_TOKEN env vars).")
    client = HomeAssistantClient(settings.ha_url, settings.ha_token)
    entities = await client.list_sensor_entities()
    return [DiscoveredEntity(**e) for e in entities]


@router.get("/ha/entities", response_model=list[HAEntityConfigOut])
async def list_entity_configs(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(HAEntityConfig))
    return result.scalars().all()


@router.post("/ha/entities", response_model=HAEntityConfigOut)
async def create_entity_config(
    payload: HAEntityConfigIn,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    config = HAEntityConfig(**payload.model_dump())
    session.add(config)
    await session.commit()
    await session.refresh(config)
    background_tasks.add_task(poll_ha_readings)
    return config


@router.put("/ha/entities/{config_id}", response_model=HAEntityConfigOut)
async def update_entity_config(
    config_id: int,
    payload: HAEntityConfigIn,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    config = await session.get(HAEntityConfig, config_id)
    if config is None:
        raise HTTPException(404, "Entity mapping not found")
    for key, value in payload.model_dump().items():
        setattr(config, key, value)
    await session.commit()
    await session.refresh(config)
    if config.enabled:
        background_tasks.add_task(poll_ha_readings)
    return config


@router.delete("/ha/entities/{config_id}")
async def delete_entity_config(config_id: int, session: AsyncSession = Depends(get_session)):
    await session.execute(delete(HAEntityConfig).where(HAEntityConfig.id == config_id))
    await session.commit()
    return {"ok": True}


@router.get("/location", response_model=LocationOut | None)
async def get_location(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Location).limit(1))
    return result.scalar_one_or_none()


@router.post("/location", response_model=LocationOut)
async def set_location(
    payload: LocationIn,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    geocoded = await geocode_address(payload.address)
    if geocoded is None:
        raise HTTPException(400, "Could not find that address. Try including city, state, and ZIP.")

    timezone = await resolve_timezone(geocoded["latitude"], geocoded["longitude"])

    result = await session.execute(select(Location).limit(1))
    location = result.scalar_one_or_none()
    if location is None:
        location = Location()
        session.add(location)

    location.address = geocoded["display_name"] or payload.address
    location.latitude = geocoded["latitude"]
    location.longitude = geocoded["longitude"]
    location.timezone = timezone

    await session.commit()
    await session.refresh(location)
    background_tasks.add_task(poll_weather_historical)
    background_tasks.add_task(poll_weather_forecast)
    return location


@router.get("/pricing", response_model=list[PricingConfigOut])
async def list_pricing(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(PricingConfig))
    return result.scalars().all()


@router.post("/pricing", response_model=PricingConfigOut)
async def upsert_pricing(payload: PricingConfigIn, session: AsyncSession = Depends(get_session)):
    pricing = await session.get(PricingConfig, payload.source_type)
    if pricing is None:
        pricing = PricingConfig(**payload.model_dump())
        session.add(pricing)
    else:
        pricing.price_per_unit = payload.price_per_unit
        pricing.currency = payload.currency
    await session.commit()
    await session.refresh(pricing)
    return pricing


@router.post("/maintenance/refresh")
async def trigger_refresh(background_tasks: BackgroundTasks):
    """Kick off an immediate poll of HA readings and a weather refresh."""
    background_tasks.add_task(poll_ha_readings)
    background_tasks.add_task(poll_weather_historical)
    background_tasks.add_task(poll_weather_forecast)
    return {"ok": True}


@router.get("/events", response_model=list[EventMarkerOut])
async def list_events(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(EventMarker).order_by(EventMarker.event_date.desc()))
    return result.scalars().all()


@router.post("/events", response_model=EventMarkerOut)
async def create_event(payload: EventMarkerIn, session: AsyncSession = Depends(get_session)):
    event = EventMarker(**payload.model_dump())
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return event


@router.delete("/events/{event_id}")
async def delete_event(event_id: int, session: AsyncSession = Depends(get_session)):
    await session.execute(delete(EventMarker).where(EventMarker.id == event_id))
    await session.commit()
    return {"ok": True}

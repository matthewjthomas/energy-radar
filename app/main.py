"""FastAPI application entrypoint."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.db import init_db
from app.routers import api, pages, settings
from app.scheduler import create_scheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    scheduler = create_scheduler()
    scheduler.start()
    logger.info("Energy Radar started; background scheduler running.")
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Energy Radar", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(pages.router)
app.include_router(api.router)
app.include_router(settings.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}

"""FastAPI application entrypoint."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
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


def create_app() -> FastAPI:
    cfg = get_settings()
    base = cfg.base_path

    application = FastAPI(title="Energy Radar", lifespan=lifespan)

    application.mount(f"{base}/static", StaticFiles(directory="app/static"), name="static")

    # Prefix page/API routers so a reverse proxy can expose the app under e.g. /energy.
    application.include_router(pages.router, prefix=base)
    application.include_router(api.router, prefix=base)
    application.include_router(settings.router, prefix=base)

    @application.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    if base:
        # Also expose health under the base path for proxies that only forward that prefix.
        @application.get(f"{base}/health")
        async def health_prefixed() -> dict:
            return {"status": "ok"}

    return application


app = create_app()

"""HTML page routes (server-rendered shell; data is loaded client-side via the API)."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from app.config import get_settings

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/")
async def dashboard(request: Request):
    return templates.TemplateResponse(
        request, "dashboard.html", {"ha_configured": get_settings().ha_configured}
    )


@router.get("/history")
async def history(request: Request):
    return templates.TemplateResponse(request, "history.html", {})


@router.get("/settings")
async def settings_page(request: Request):
    return templates.TemplateResponse(request, "settings.html", {})

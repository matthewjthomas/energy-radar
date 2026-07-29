"""Application configuration loaded from environment variables."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Home Assistant
    ha_url: str = ""
    ha_token: str = ""

    # Database
    database_url: str = "postgresql+asyncpg://energyradar:changeme@db:5432/energyradar"

    # App behavior
    app_port: int = 8000
    ha_poll_interval_minutes: int = 15
    weather_forecast_interval_hours: int = 3
    app_timezone: str = "UTC"
    # Optional URL prefix when serving behind a reverse proxy, e.g. "/energy".
    # All page, API, and static routes are mounted under this path. Leave empty
    # to serve at the domain root. /health always remains at the absolute root.
    app_base_path: str = ""
    # How far back to backfill on first poll of a newly mapped entity, using HA's
    # long-term statistics (which HA retains far longer than raw state history).
    ha_stats_lookback_days: int = 395
    # How far back to fetch historical weather observations. Should be at least
    # as large as ha_stats_lookback_days so the regression model can use all
    # available electricity/gas/water history.
    weather_lookback_days: int = 395

    @property
    def ha_configured(self) -> bool:
        return bool(self.ha_url and self.ha_token)

    @property
    def base_path(self) -> str:
        """Normalized URL prefix with a leading slash and no trailing slash, or ''."""
        path = (self.app_base_path or "").strip()
        if not path or path == "/":
            return ""
        if not path.startswith("/"):
            path = f"/{path}"
        return path.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()

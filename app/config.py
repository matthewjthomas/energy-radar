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

    @property
    def ha_configured(self) -> bool:
        return bool(self.ha_url and self.ha_token)


@lru_cache
def get_settings() -> Settings:
    return Settings()

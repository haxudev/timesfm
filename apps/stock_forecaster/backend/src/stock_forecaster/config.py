from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
  model_config = SettingsConfigDict(
    env_prefix="STOCK_FORECASTER_",
    env_file=".env",
    extra="ignore",
  )

  checkpoint: str = "google/timesfm-3.0-pytorch"
  device: Literal["auto", "cpu", "cuda"] = "auto"
  cors_origins: list[str] = ["http://localhost:5173"]
  cache_ttl_seconds: int = Field(300, ge=0, le=86400)
  min_context_length: int = 32
  default_context_length: int = 512
  max_context_length: int = 16384
  max_horizon: int = 60
  model_enabled: bool = True
  max_concurrent_inferences: int = Field(1, ge=1, le=8)
  log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

  @field_validator("cors_origins", mode="before")
  @classmethod
  def parse_origins(cls, value: object) -> object:
    if isinstance(value, str) and not value.lstrip().startswith("["):
      return [origin.strip() for origin in value.split(",") if origin.strip()]
    return value


@lru_cache
def get_settings() -> Settings:
  return Settings()

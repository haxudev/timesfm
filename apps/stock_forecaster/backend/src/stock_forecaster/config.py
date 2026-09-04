import json
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
  model_config = SettingsConfigDict(
    env_prefix="STOCK_FORECASTER_",
    env_file=".env",
    extra="ignore",
  )

  checkpoint: str = "google/timesfm-3.0-pytorch"
  device: Literal["auto", "cpu", "cuda"] = "auto"
  cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]
  cache_ttl_seconds: int = Field(300, ge=0, le=86400)
  min_context_length: int = Field(32, ge=32, le=16384)
  default_context_length: int = Field(512, ge=32, le=16384)
  max_context_length: int = Field(16384, ge=32, le=16384)
  max_horizon: int = Field(60, ge=1, le=60)
  model_enabled: bool = True
  max_concurrent_inferences: int = Field(1, ge=1, le=8)
  log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

  @field_validator("cors_origins", mode="before")
  @classmethod
  def parse_origins(cls, value: object) -> object:
    if isinstance(value, str):
      if value.lstrip().startswith("["):
        return json.loads(value)
      return [origin.strip() for origin in value.split(",") if origin.strip()]
    return value

  @model_validator(mode="after")
  def validate_context_limits(self) -> "Settings":
    if not (
      self.min_context_length
      <= self.default_context_length
      <= self.max_context_length
    ):
      raise ValueError("context limits must be ordered")
    return self


@lru_cache
def get_settings() -> Settings:
  return Settings()

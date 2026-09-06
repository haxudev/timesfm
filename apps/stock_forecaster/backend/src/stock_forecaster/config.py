import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEFAULT_CHECKPOINT = "google/timesfm-3.0-pytorch"
DEFAULT_CHECKPOINT_REVISION = "43046b85ec22d584a13f8098c2ed39c889e129c2"


def resolve_checkpoint_revision(checkpoint: str, revision: str | None) -> str | None:
  if revision is not None:
    if not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
      raise ValueError("checkpoint_revision must be a full 40-character commit SHA")
    return revision.lower()
  return DEFAULT_CHECKPOINT_REVISION if checkpoint == DEFAULT_CHECKPOINT else None


class Settings(BaseSettings):
  model_config = SettingsConfigDict(
    env_prefix="STOCK_FORECASTER_",
    env_file=".env",
    extra="ignore",
  )

  checkpoint: str = DEFAULT_CHECKPOINT
  checkpoint_revision: str | None = None
  device: Literal["auto", "cpu", "cuda"] = "auto"
  cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]
  cache_ttl_seconds: int = Field(300, ge=0, le=86400)
  cache_max_entries: int = Field(128, ge=1, le=10000)
  min_context_length: int = Field(32, ge=32, le=16384)
  default_context_length: int = Field(512, ge=32, le=16384)
  max_context_length: int = Field(16384, ge=32, le=16384)
  max_horizon: int = Field(60, ge=1, le=60)
  model_enabled: bool = True
  max_concurrent_inferences: int = Field(1, ge=1, le=8)
  log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
  research_data_dir: Path = Path(".local/research")
  internal_token_file: Path | None = None
  research_http_url: str = "http://backend:8000"
  worker_poll_seconds: float = Field(2.0, ge=0.1, le=60, allow_inf_nan=False)
  tushare_api_key: SecretStr | None = Field(
    default=None, validation_alias=AliasChoices("TUSHARE_API_KEY", "STOCK_FORECASTER_TUSHARE_API_KEY"),
    exclude=True, repr=False,
  )

  @field_validator("research_http_url")
  @classmethod
  def validate_research_url(cls, value: str) -> str:
    parsed = urlsplit(value)
    if (
      parsed.scheme not in {"http", "https"} or not parsed.hostname
      or parsed.username is not None or parsed.password is not None
      or parsed.query or parsed.fragment or parsed.path not in {"", "/"}
    ):
      raise ValueError("research_http_url must be an HTTP origin without credentials")
    return value.rstrip("/")

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
    self.checkpoint_revision = resolve_checkpoint_revision(self.checkpoint, self.checkpoint_revision)
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

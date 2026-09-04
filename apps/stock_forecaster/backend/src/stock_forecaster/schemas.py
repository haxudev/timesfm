from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Period = Literal["1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "max"]
Device = Literal["auto", "cpu", "cuda"]
Target = Literal["log_return", "price"]


class DateSelection(BaseModel):
  period: Period | None = None
  start: date | None = None
  end: date | None = None

  @model_validator(mode="after")
  def validate_selection(self) -> "DateSelection":
    has_dates = self.start is not None or self.end is not None
    if has_dates and (self.start is None or self.end is None):
      raise ValueError("start and end must be provided together")
    if has_dates and self.period is not None:
      raise ValueError("use either period or start/end dates")
    if self.start is not None and self.end is not None and self.start >= self.end:
      raise ValueError("start must be before end")
    if not has_dates and self.period is None:
      self.period = "2y"
    return self


class ForecastRequest(DateSelection):
  ticker: str = Field("SPY", min_length=1, max_length=15)
  horizon: int = Field(5, ge=1, le=60)
  context_length: int = Field(512, ge=32, le=16384)
  target: Target = "log_return"
  device: Device = "auto"


class BacktestRequest(DateSelection):
  ticker: str = Field("SPY", min_length=1, max_length=15)
  horizon: int = Field(5, ge=1, le=60)
  context_length: int = Field(512, ge=32, le=16384)
  evaluation_windows: int = Field(10, ge=1, le=100)
  step_size: int = Field(5, ge=1, le=252)
  device: Device = "auto"


class MarketObservation(BaseModel):
  date: date
  price: float
  volume: float | None = None


class MarketDataResponse(BaseModel):
  ticker: str
  price_column: str
  start: date
  end: date
  count: int
  currency: str | None
  observations: list[MarketObservation]


class ForecastPoint(BaseModel):
  date: date
  point_return: float
  quantiles: dict[str, float]
  point_price: float
  lower_price: float
  upper_price: float


class ForecastResponse(BaseModel):
  ticker: str
  target: Target
  history: MarketDataResponse
  context: dict[str, int | str]
  model: dict[str, str]
  forecasts: list[ForecastPoint]
  warnings: list[str]
  summary: dict[str, float | int]


class MetricSet(BaseModel):
  return_mae: float
  return_rmse: float
  directional_accuracy: float
  price_mae: float
  interval_coverage: float | None = None
  average_interval_width: float | None = None


class BacktestWindow(BaseModel):
  origin: date
  context_start: date
  context_end: date
  forecast_end: date
  metrics: dict[str, MetricSet]


class BacktestResponse(BaseModel):
  ticker: str
  window_count: int
  observation_count: int
  aggregate: dict[str, MetricSet]
  windows: list[BacktestWindow]
  warning: str

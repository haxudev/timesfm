from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date
from typing import Annotated, TypeVar

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from . import __version__
from .backtesting import BacktestService
from .config import Settings, get_settings
from .errors import AppError, install_error_handlers
from .forecasting import ForecastService
from .market_data import MarketDataProvider, MarketDataService, YFinanceProvider
from .model import ModelManager, resolve_device
from .schemas import (
  BacktestRequest,
  BacktestResponse,
  DateSelection,
  ForecastRequest,
  ForecastResponse,
  MarketDataResponse,
  Period,
)


def create_app(
  settings: Settings | None = None,
  provider: MarketDataProvider | None = None,
  model: ModelManager | None = None,
) -> FastAPI:
  config = settings or get_settings()
  logging.basicConfig(level=config.log_level)
  app = FastAPI(title="TimesFM Stock Forecaster", version=__version__)
  app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Request-ID"],
  )
  market = MarketDataService(
    provider or YFinanceProvider(),
    config.cache_ttl_seconds,
    config.cache_max_entries,
  )
  manager = model or ModelManager(
    config.checkpoint,
    enabled=config.model_enabled,
    max_concurrent=config.max_concurrent_inferences,
  )
  forecast_service = ForecastService(market, manager)
  backtest_service = BacktestService(market, manager)

  @app.middleware("http")
  async def request_id_middleware(request: Request, call_next):
    supplied = request.headers.get("X-Request-ID", "")
    request.state.request_id = (
      supplied if supplied and len(supplied) <= 128 else str(uuid.uuid4())
    )
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response

  install_error_handlers(app)

  @app.get("/api/v1/health")
  async def health() -> dict[str, str]:
    try:
      selected_device, _ = resolve_device(config.device)
    except AppError:
      selected_device = "unavailable"
    return {
      "status": "ok",
      "device": selected_device,
      "version": __version__,
      "model_state": manager.state,
    }

  @app.get("/api/v1/market-data/{ticker}", response_model=MarketDataResponse)
  async def market_data(
    ticker: str,
    period: Annotated[Period | None, Query()] = None,
    start: date | None = None,
    end: date | None = None,
    include_volume: bool = False,
  ) -> MarketDataResponse:
    try:
      selection = DateSelection(period=period, start=start, end=end)
    except ValidationError as error:
      raise AppError(
        "validation_error",
        "The date selection is invalid.",
        422,
      ) from error
    return await asyncio.to_thread(market.get, ticker, selection, include_volume)

  @app.post("/api/v1/forecasts", response_model=ForecastResponse)
  async def forecast(request: ForecastRequest) -> ForecastResponse:
    request = _apply_request_defaults(request, config)
    _validate_request_policy(request, config)
    return await asyncio.to_thread(forecast_service.run, request)

  @app.post("/api/v1/backtests", response_model=BacktestResponse)
  async def backtest(request: BacktestRequest) -> BacktestResponse:
    request = _apply_request_defaults(request, config)
    _validate_request_policy(request, config)
    return await asyncio.to_thread(backtest_service.run, request)

  return app


RequestModel = TypeVar("RequestModel", ForecastRequest, BacktestRequest)


def _apply_request_defaults(
  request: RequestModel,
  settings: Settings,
) -> RequestModel:
  updates: dict[str, object] = {}
  if "context_length" not in request.model_fields_set:
    updates["context_length"] = settings.default_context_length
  if "device" not in request.model_fields_set:
    updates["device"] = settings.device
  return request.model_copy(update=updates)


def _validate_request_policy(
  request: ForecastRequest | BacktestRequest,
  settings: Settings,
) -> None:
  if request.horizon > settings.max_horizon:
    raise AppError(
      "validation_error",
      f"Horizon exceeds the server maximum of {settings.max_horizon}.",
      422,
    )
  if not (
    settings.min_context_length
    <= request.context_length
    <= settings.max_context_length
  ):
    raise AppError(
      "validation_error",
      (
        "Context length must be between "
        f"{settings.min_context_length} and {settings.max_context_length}."
      ),
      422,
    )


app = create_app()

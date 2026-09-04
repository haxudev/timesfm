from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date
from typing import Annotated

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware

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
    selection = DateSelection(period=period, start=start, end=end)
    return await asyncio.to_thread(market.get, ticker, selection, include_volume)

  @app.post("/api/v1/forecasts", response_model=ForecastResponse)
  async def forecast(request: ForecastRequest) -> ForecastResponse:
    return await asyncio.to_thread(forecast_service.run, request)

  @app.post("/api/v1/backtests", response_model=BacktestResponse)
  async def backtest(request: BacktestRequest) -> BacktestResponse:
    return await asyncio.to_thread(backtest_service.run, request)

  return app


app = create_app()

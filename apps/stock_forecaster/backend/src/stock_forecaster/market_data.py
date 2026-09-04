from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import pandas as pd

from .errors import AppError
from .schemas import DateSelection, MarketDataResponse, MarketObservation

_TICKER_RE = re.compile(r"^[A-Z0-9^][A-Z0-9.\-^=]{0,14}$")
logger = logging.getLogger(__name__)


def normalize_ticker(value: str) -> str:
  ticker = value.strip().upper()
  if not _TICKER_RE.fullmatch(ticker):
    raise AppError(
      "invalid_ticker",
      "Ticker must contain only supported market-symbol characters.",
      422,
    )
  return ticker


@dataclass(frozen=True)
class ProviderResult:
  frame: pd.DataFrame
  currency: str | None = None


class MarketDataProvider(Protocol):
  def fetch(self, ticker: str, selection: DateSelection) -> ProviderResult: ...


class YFinanceProvider:
  def fetch(self, ticker: str, selection: DateSelection) -> ProviderResult:
    try:
      import yfinance as yf

      kwargs: dict[str, object] = {
        "tickers": ticker,
        "progress": False,
        "auto_adjust": False,
        "threads": False,
        "timeout": 15,
      }
      if selection.start is not None:
        kwargs.update(start=selection.start, end=selection.end)
      else:
        kwargs["period"] = selection.period
      frame = yf.download(**kwargs)
      currency = None
      try:
        currency = yf.Ticker(ticker).fast_info.get("currency")
      except Exception as error:  # noqa: BLE001
        logger.debug("Currency metadata unavailable: %s", type(error).__name__)
      return ProviderResult(frame=frame, currency=currency)
    except Exception as error:
      raise AppError(
        "market_data_unavailable",
        "Market data is temporarily unavailable.",
        502,
      ) from error


def _find_column(frame: pd.DataFrame, name: str) -> object | None:
  if not isinstance(frame.columns, pd.MultiIndex) and name in frame.columns:
    return name
  target = name.casefold()
  for column in frame.columns:
    parts = column if isinstance(column, tuple) else (column,)
    if any(str(part).casefold() == target for part in parts):
      return column
  return None


def normalize_frame(
  ticker: str,
  result: ProviderResult,
  include_volume: bool,
) -> MarketDataResponse:
  frame = result.frame.copy()
  if frame.empty:
    raise AppError("market_data_empty", "No market data was found.", 404)

  price_column = _find_column(frame, "Adj Close") or _find_column(frame, "Close")
  if price_column is None:
    raise AppError(
      "market_data_malformed",
      "Market data did not include a usable price column.",
      502,
    )
  selected_name = "Adj Close" if "adj close" in str(price_column).casefold() else "Close"
  volume_column = _find_column(frame, "Volume") if include_volume else None

  data = pd.DataFrame({"price": pd.to_numeric(frame[price_column], errors="coerce")})
  if volume_column is not None:
    data["volume"] = pd.to_numeric(frame[volume_column], errors="coerce")
  index = pd.to_datetime(frame.index, errors="coerce", utc=True)
  data.index = index
  data = data[~data.index.isna()]
  data = data[~data.index.duplicated(keep="last")].sort_index()
  data = data.dropna(subset=["price"])
  if data.empty:
    raise AppError("market_data_empty", "No valid target prices were found.", 404)
  prices = data["price"].to_numpy(dtype=float)
  if not np.isfinite(prices).all():
    raise AppError(
      "non_finite_prices",
      "Target prices must all be finite.",
      422,
    )

  observations = []
  for timestamp, row in data.iterrows():
    volume = row.get("volume")
    observations.append(
      MarketObservation(
        date=timestamp.date(),
        price=float(row["price"]),
        volume=(
          float(volume)
          if volume is not None and pd.notna(volume) and np.isfinite(volume)
          else None
        ),
      )
    )
  return MarketDataResponse(
    ticker=ticker,
    price_column=selected_name,
    start=observations[0].date,
    end=observations[-1].date,
    count=len(observations),
    currency=result.currency,
    observations=observations,
  )


class MarketDataService:
  def __init__(self, provider: MarketDataProvider, ttl_seconds: int) -> None:
    self.provider = provider
    self.ttl_seconds = ttl_seconds
    self._cache: dict[tuple[object, ...], tuple[float, ProviderResult]] = {}
    self._lock = threading.Lock()

  def get(
    self,
    ticker: str,
    selection: DateSelection,
    include_volume: bool = False,
  ) -> MarketDataResponse:
    normalized = normalize_ticker(ticker)
    key = (normalized, selection.period, selection.start, selection.end)
    now = time.monotonic()
    with self._lock:
      cached = self._cache.get(key)
      if cached is not None and now - cached[0] <= self.ttl_seconds:
        result = cached[1]
      else:
        result = self.provider.fetch(normalized, selection)
        self._cache[key] = (now, result)
    return normalize_frame(normalized, result, include_volume)

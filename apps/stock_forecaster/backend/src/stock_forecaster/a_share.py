from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .errors import AppError
from .instruments import index_name
from .market_data import (
  MarketDataProvider,
  ProviderResult,
  YFinanceProvider,
  normalize_ticker,
)
from .schemas import DateSelection

SHANGHAI = ZoneInfo("Asia/Shanghai")


def a_share_symbol(ticker: str) -> str | None:
  match = re.fullmatch(r"(\d{6})\.(SS|SZ|BJ)", normalize_ticker(ticker))
  if match is None:
    return None
  code, exchange = match.groups()
  return f"{'sh' if exchange == 'SS' else exchange.lower()}{code}"


class AKShareProvider:
  def fetch(self, ticker: str, selection: DateSelection) -> ProviderResult:
    name = index_name(ticker)
    symbol = a_share_symbol(ticker)
    if symbol is None:
      raise AppError("invalid_ticker", "An A-share symbol is required.", 422)
    now = datetime.now(SHANGHAI)
    completed_before = now.date() + timedelta(days=int(now.hour >= 16))
    end = min(selection.end or completed_before, completed_before)
    offsets = {
      "1mo": pd.DateOffset(months=1),
      "3mo": pd.DateOffset(months=3),
      "6mo": pd.DateOffset(months=6),
      "1y": pd.DateOffset(years=1),
      "2y": pd.DateOffset(years=2),
      "5y": pd.DateOffset(years=5),
      "10y": pd.DateOffset(years=10),
    }
    start = selection.start or (
      pd.Timestamp("1990-01-01").date()
      if selection.period == "max"
      else (pd.Timestamp(end) - offsets[selection.period or "2y"]).date()
    )
    try:
      process = subprocess.run(
        [
          sys.executable,
          str(Path(__file__).with_name("akshare_worker.py")),
          symbol,
          start.isoformat(),
          end.isoformat(),
        ],
        capture_output=True,
        timeout=45,
        check=True,
      )
      frame = pd.read_json(BytesIO(process.stdout), orient="split")
      if not frame.empty:
        frame = frame.rename(columns={
          "close": "Close" if name else "Adj Close", "volume": "Volume",
        })
        frame.index = pd.to_datetime(frame["date"], errors="coerce")
        frame = frame.loc[
          (frame.index >= pd.Timestamp(start)) & (frame.index < pd.Timestamp(end))
        ]
      return ProviderResult(
        frame, "CNY", source="akshare/tencent",
        instrument_type="index" if name else "stock", name=name,
      )
    except Exception as error:
      raise AppError(
        "market_data_unavailable",
        "AKShare A-share history is temporarily unavailable.",
        502,
      ) from error


class RoutedMarketDataProvider:
  def __init__(
    self,
    a_shares: MarketDataProvider | None = None,
    overseas: MarketDataProvider | None = None,
  ) -> None:
    self.a_shares = a_shares or AKShareProvider()
    self.overseas = overseas or YFinanceProvider()

  def fetch(self, ticker: str, selection: DateSelection) -> ProviderResult:
    ticker = normalize_ticker(ticker)
    provider = self.a_shares if a_share_symbol(ticker) else self.overseas
    return provider.fetch(ticker, selection)
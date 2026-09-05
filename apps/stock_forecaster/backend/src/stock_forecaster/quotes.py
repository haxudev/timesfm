from __future__ import annotations

import math
import re
import threading
import time
from concurrent.futures import Future
from datetime import datetime

import httpx

from .a_share import SHANGHAI, a_share_symbol
from .errors import AppError
from .instruments import index_name
from .market_data import normalize_ticker
from .schemas import QuoteLevel, QuoteResponse, QuoteSource


def _number(value: str, multiplier: float = 1) -> float | None:
  try:
    result = float(value) * multiplier
    return result if math.isfinite(result) else None
  except ValueError:
    return None


def _price(value: str) -> float | None:
  result = _number(value)
  return result if result is not None and result > 0 else None


def _fields(text: str, ticker: str, source: str) -> list[str]:
  symbol = a_share_symbol(ticker)
  if symbol is None:
    raise ValueError("Not an A-share symbol")
  prefix = f"v_{symbol}" if source == "tencent" else f"var hq_str_{symbol}"
  match = re.fullmatch(rf'\s*{re.escape(prefix)}="([^"]*)";\s*', text)
  if match is None:
    raise ValueError("Unexpected quote response")
  fields = match.group(1).split("~" if source == "tencent" else ",")
  minimum = 47 if source == "tencent" else 32
  if len(fields) < minimum:
    raise ValueError("Incomplete quote response")
  if source == "tencent" and fields[2] != symbol[2:]:
    raise ValueError("Quote symbol mismatch")
  return fields


def _change(last: float | None, previous: float | None) -> dict[str, float | None]:
  change = last - previous if last is not None and previous is not None else None
  return {
    "change": round(change, 8) if change is not None else None,
    "change_percent": change / previous * 100 if change is not None and previous else None,
  }


def parse_tencent(text: str, ticker: str) -> QuoteResponse:
  fields = _fields(text, ticker, "tencent")
  is_index = index_name(ticker) is not None
  last, previous = _price(fields[3]), _price(fields[4])

  def levels(start: int) -> list[QuoteLevel]:
    return [
      QuoteLevel(price=_price(fields[index]), volume=_number(fields[index + 1], 100))
      for index in range(start, start + 10, 2)
    ]

  return QuoteResponse(
    ticker=normalize_ticker(ticker),
    instrument_type="index" if is_index else "stock",
    name=fields[1].strip(),
    source="tencent",
    as_of=datetime.strptime(fields[30], "%Y%m%d%H%M%S").replace(tzinfo=SHANGHAI),
    last=last,
    previous_close=previous,
    open=_price(fields[5]),
    high=_price(fields[33]),
    low=_price(fields[34]),
    volume=_number(fields[6], 1 if is_index else 100),
    amount=_number(fields[37], 10000),
    bids=[] if is_index else levels(9),
    asks=[] if is_index else levels(19),
    pe_ratio=None if is_index else _number(fields[39]),
    pb_ratio=None if is_index else _number(fields[46]),
    market_cap=None if is_index else _number(fields[45], 100000000),
    float_market_cap=None if is_index else _number(fields[44], 100000000),
    turnover_rate=None if is_index else _number(fields[38]),
    **_change(last, previous),
  )


def parse_sina(text: str, ticker: str) -> QuoteResponse:
  fields = _fields(text, ticker, "sina")
  is_index = index_name(ticker) is not None
  last, previous = _price(fields[3]), _price(fields[2])

  def levels(start: int) -> list[QuoteLevel]:
    return [
      QuoteLevel(price=_price(fields[index + 1]), volume=_number(fields[index]))
      for index in range(start, start + 10, 2)
    ]

  return QuoteResponse(
    ticker=normalize_ticker(ticker),
    instrument_type="index" if is_index else "stock",
    name=fields[0].strip(),
    source="sina",
    as_of=datetime.strptime(
      f"{fields[30]} {fields[31]}", "%Y-%m-%d %H:%M:%S",
    ).replace(tzinfo=SHANGHAI),
    last=last,
    previous_close=previous,
    open=_price(fields[1]),
    high=_price(fields[4]),
    low=_price(fields[5]),
    volume=_number(fields[8]),
    amount=_number(fields[9]),
    bids=[] if is_index else levels(10),
    asks=[] if is_index else levels(20),
    **_change(last, previous),
  )


class QuoteService:
  def __init__(
    self,
    ttl_seconds: float = 5,
    max_entries: int = 128,
    transport: httpx.BaseTransport | None = None,
  ) -> None:
    self.ttl_seconds = ttl_seconds
    self.max_entries = max_entries
    self.transport = transport
    self._cache: dict[tuple[str, str], tuple[float, QuoteResponse]] = {}
    self._inflight: dict[tuple[str, str], Future[QuoteResponse]] = {}
    self._lock = threading.Lock()

  def get(self, ticker: str, source: QuoteSource = "auto") -> QuoteResponse:
    ticker = normalize_ticker(ticker)
    symbol = a_share_symbol(ticker)
    if symbol is None:
      raise AppError("quote_unsupported", "Quotes require an A-share symbol.", 422)
    key = (ticker, source)
    with self._lock:
      cached = self._cache.get(key)
      if cached and time.monotonic() - cached[0] < self.ttl_seconds:
        return cached[1].model_copy(deep=True)
      pending = self._inflight.get(key)
      owner = pending is None
      if pending is None:
        pending = Future()
        self._inflight[key] = pending
    if not owner:
      return pending.result().model_copy(deep=True)
    try:
      quote = self._fetch(ticker, symbol, source)
      with self._lock:
        if len(self._cache) >= self.max_entries:
          oldest = min(self._cache, key=lambda item: self._cache[item][0])
          self._cache.pop(oldest)
        self._cache[key] = (time.monotonic(), quote)
      pending.set_result(quote)
      return quote.model_copy(deep=True)
    except Exception as error:
      pending.set_exception(error)
      raise
    finally:
      with self._lock:
        self._inflight.pop(key)

  def _fetch(self, ticker: str, symbol: str, source: QuoteSource) -> QuoteResponse:
    sources = ("tencent", "sina") if source == "auto" else (source,)
    with httpx.Client(
      timeout=4,
      transport=self.transport,
      headers={
        "Referer": "https://finance.sina.com.cn/",
        "User-Agent": "TimesFM-Stock-Forecaster/0.1",
      },
    ) as client:
      for selected in sources:
        url = (
          f"https://qt.gtimg.cn/q={symbol}"
          if selected == "tencent"
          else f"https://hq.sinajs.cn/list={symbol}"
        )
        try:
          response = client.get(url)
          response.raise_for_status()
          parser = parse_tencent if selected == "tencent" else parse_sina
          quote = parser(response.content.decode("gbk"), ticker)
        except (httpx.HTTPError, ValueError):
          continue
        if source == "auto" and selected == "sina":
          quote.warnings.append("Tencent unavailable; using Sina without valuations.")
        return quote
    raise AppError(
      "quote_unavailable",
      "A-share quotes are temporarily unavailable from the selected sources.",
      502,
    )
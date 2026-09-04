import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import pytest

from stock_forecaster.errors import AppError
from stock_forecaster.market_data import (
  MarketDataService,
  ProviderResult,
  normalize_frame,
  normalize_ticker,
)
from stock_forecaster.schemas import DateSelection


@pytest.mark.parametrize(
  ("raw", "expected"),
  [(" spy ", "SPY"), ("BRK.B", "BRK.B"), ("^gspc", "^GSPC")],
)
def test_normalize_ticker(raw, expected):
  assert normalize_ticker(raw) == expected


@pytest.mark.parametrize("raw", ["", "^", "../SPY", "SPY X", "A" * 16])
def test_invalid_ticker(raw):
  with pytest.raises(AppError) as caught:
    normalize_ticker(raw)
  assert caught.value.code == "invalid_ticker"


def test_normalizes_multiindex_sorts_deduplicates_and_prefers_adjusted():
  columns = pd.MultiIndex.from_tuples(
    [("Adj Close", "SPY"), ("Close", "SPY"), ("Volume", "SPY")]
  )
  frame = pd.DataFrame(
    [[102, 202, 3], [100, 200, 1], [101, 201, 2]],
    index=pd.to_datetime(["2024-01-03", "2024-01-02", "2024-01-03"]),
    columns=columns,
  )
  result = normalize_frame("SPY", ProviderResult(frame, "USD"), True)
  assert result.price_column == "Adj Close"
  assert [item.price for item in result.observations] == [100, 101]
  assert [item.volume for item in result.observations] == [1, 2]


def test_falls_back_to_close_without_forward_filling():
  frame = pd.DataFrame(
    {"Close": [10.0, np.nan, 12.0]},
    index=pd.date_range("2024-01-01", periods=3),
  )
  result = normalize_frame("SPY", ProviderResult(frame), False)
  assert result.price_column == "Close"
  assert [item.price for item in result.observations] == [10, 12]


def test_falls_back_when_adjusted_close_is_empty():
  frame = pd.DataFrame(
    {"Adj Close": [np.nan, np.nan], "Close": [10.0, 11.0]},
    index=pd.date_range("2024-01-01", periods=2),
  )
  result = normalize_frame("SPY", ProviderResult(frame), False)
  assert result.price_column == "Close"
  assert [item.price for item in result.observations] == [10, 11]


def test_empty_data_has_stable_error():
  with pytest.raises(AppError) as caught:
    normalize_frame("SPY", ProviderResult(pd.DataFrame()), False)
  assert caught.value.code == "market_data_empty"


def test_concurrent_cache_misses_share_one_fetch():
  frame = pd.DataFrame(
    {"Close": [10.0, 11.0]},
    index=pd.date_range("2024-01-01", periods=2),
  )

  class SlowProvider:
    calls = 0

    def fetch(self, ticker, selection):
      self.calls += 1
      time.sleep(0.02)
      return ProviderResult(frame)

  provider = SlowProvider()
  service = MarketDataService(provider, ttl_seconds=60)
  selection = DateSelection(period="1y")
  with ThreadPoolExecutor(max_workers=4) as executor:
    results = list(
      executor.map(lambda _: service.get("SPY", selection), range(4))
    )
  assert provider.calls == 1
  assert all(result.count == 2 for result in results)

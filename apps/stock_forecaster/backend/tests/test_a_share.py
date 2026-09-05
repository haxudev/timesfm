import subprocess
import sys
from datetime import date, datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from stock_forecaster.a_share import AKShareProvider, RoutedMarketDataProvider
from stock_forecaster.errors import AppError
from stock_forecaster.market_data import MarketDataService, ProviderResult
from stock_forecaster.schemas import DateSelection


@pytest.fixture(autouse=True)
def inline_history_worker(monkeypatch):
  def run(arguments, **options):
    from stock_forecaster.akshare_worker import download_history

    frame = download_history(*arguments[-3:])
    return subprocess.CompletedProcess(
      arguments, 0,
      stdout=frame.to_json(orient="split", date_format="iso").encode("utf-8"),
      stderr=b"\xa8\x80",
    )

  monkeypatch.setattr(subprocess, "run", run)


def test_akshare_history_is_adjusted_cny_and_end_exclusive(monkeypatch):
  def history(*, symbol, start_date, end_date, adjust, timeout):
    assert symbol == "sh600519"
    assert start_date == "2024-01-02"
    assert end_date == "2024-01-04"
    assert adjust == "qfq"
    assert timeout == 10
    return pd.DataFrame({
      "date": ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"],
      "close": [99, 100, 102, 103],
      "volume": [1000, 2000, 3000, 4000],
    })

  monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(stock_zh_a_hist_tx=history))
  service = MarketDataService(AKShareProvider(), ttl_seconds=60)
  result = service.get(
    "600519",
    DateSelection(start=date(2024, 1, 2), end=date(2024, 1, 4)),
    include_volume=True,
  )
  assert result.ticker == "600519.SS"
  assert result.currency == "CNY"
  assert result.source == "akshare/tencent"
  assert result.price_column == "Adj Close"
  assert [item.price for item in result.observations] == [100, 102]
  assert [item.volume for item in result.observations] == [2000, 3000]


@pytest.mark.parametrize("ticker", ["600519.SS", "000001.SZ", "920001.BJ", "SPY"])
def test_routing_keeps_a_shares_separate_from_overseas(ticker):
  class Provider:
    def __init__(self, currency):
      self.currency = currency

    def fetch(self, ticker, selection):
      return ProviderResult(pd.DataFrame(), self.currency)

  router = RoutedMarketDataProvider(
    a_shares=Provider("CNY"), overseas=Provider("USD"),
  )
  result = router.fetch(ticker, DateSelection(period="1y"))
  assert result.currency == ("USD" if ticker == "SPY" else "CNY")


def test_akshare_failures_are_stable_application_errors(monkeypatch):
  def unavailable(**kwargs):
    raise ConnectionError("upstream unavailable")

  monkeypatch.setitem(
    sys.modules, "akshare", SimpleNamespace(stock_zh_a_hist_tx=unavailable),
  )
  with pytest.raises(AppError) as caught:
    AKShareProvider().fetch("600519.SS", DateSelection(period="2y"))
  assert caught.value.code == "market_data_unavailable"


def test_hung_akshare_worker_has_a_total_deadline(monkeypatch):
  frame = pd.DataFrame({"date": ["2026-09-03"], "close": [100]})
  monkeypatch.setitem(
    sys.modules, "akshare", SimpleNamespace(stock_zh_a_hist_tx=lambda **_: frame),
  )

  def timeout(arguments, **options):
    assert options["timeout"] == 45
    raise subprocess.TimeoutExpired(arguments, 45)

  monkeypatch.setattr(subprocess, "run", timeout)
  with pytest.raises(AppError) as caught:
    AKShareProvider().fetch("600519.SS", DateSelection(period="2y"))
  assert caught.value.code == "market_data_unavailable"


@pytest.mark.parametrize(("hour", "prices"), [(15, [99]), (16, [99, 100])])
def test_history_excludes_incomplete_shanghai_session(monkeypatch, hour, prices):
  class Clock:
    @classmethod
    def now(cls, timezone):
      return datetime(2026, 9, 4, hour, tzinfo=timezone)

  frame = pd.DataFrame({
    "date": ["2026-09-03", "2026-09-04", "2026-09-05"],
    "close": [99, 100, 101],
  })
  monkeypatch.setattr("stock_forecaster.a_share.datetime", Clock)
  monkeypatch.setitem(
    sys.modules, "akshare", SimpleNamespace(stock_zh_a_hist_tx=lambda **_: frame),
  )
  result = MarketDataService(AKShareProvider(), 60).get("600519", DateSelection())
  assert [item.price for item in result.observations] == prices


@pytest.mark.parametrize(
  ("ticker", "symbol", "name"),
  [
    ("sh000001", "sh000001", "上证指数"),
    ("399001.SZ", "sz399001", "深证成指"),
    ("sz399006", "sz399006", "创业板指"),
    ("000300.SH", "sh000300", "沪深300"),
    ("000905.SS", "sh000905", "中证500"),
    ("000688.SS", "sh000688", "科创50"),
  ],
)
def test_index_history_uses_unadjusted_points(monkeypatch, ticker, symbol, name):
  def history(**kwargs):
    assert kwargs["symbol"] == symbol
    assert kwargs["adjust"] == ""
    return pd.DataFrame({"date": ["2024-01-02"], "close": [3200]})

  monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(stock_zh_a_hist_tx=history))
  result = MarketDataService(AKShareProvider(), 60).get(
    ticker, DateSelection(start=date(2024, 1, 1), end=date(2024, 1, 3)),
  )
  assert result.instrument_type == "index"
  assert result.name == name
  assert result.price_column == "Close"
  assert result.observations[0].price == 3200


def test_bare_000001_remains_a_stock(monkeypatch):
  def history(**kwargs):
    assert kwargs["symbol"] == "sz000001"
    assert kwargs["adjust"] == "qfq"
    return pd.DataFrame({"date": ["2024-01-02"], "close": [10]})

  monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(stock_zh_a_hist_tx=history))
  result = MarketDataService(AKShareProvider(), 60).get(
    "000001", DateSelection(start=date(2024, 1, 1), end=date(2024, 1, 3)),
  )
  assert result.instrument_type == "stock"
  assert result.ticker == "000001.SZ"
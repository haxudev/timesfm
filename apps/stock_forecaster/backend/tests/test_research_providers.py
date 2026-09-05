import importlib
import json
import sys
import time

import pandas as pd
import pytest


def providers():
  return importlib.import_module("stock_forecaster.research.providers")


def test_industry_update_is_not_publication_time():
  result = providers().normalize_industry(pd.DataFrame({
    "symbol": ["600519"], "start_date": ["2014-01-01"],
    "industry_code": ["801120"], "update_time": ["2014-01-02"],
  }), "2026-09-05T10:00:00+00:00")
  row = result.frame.iloc[0]
  assert row["effective_from"] == "2014-01-01"
  assert row["vendor_updated"] == "2014-01-02"
  assert row["known_at"] == "2026-09-05T10:00:00+00:00"
  assert result.metadata["point_in_time"] is False
  assert "historical_industry_publication_unverified" in result.metadata["blockers"]


def test_complete_bars_preserve_adjustment_and_amount():
  result = providers().normalize_bars(pd.DataFrame({
    "date": ["2026-09-04"], "open": [10], "high": [12], "low": [9],
    "close": [11], "volume": [1000], "amount": [11000],
  }), "600519.SS", "qfq", "2026-09-05T10:00:00+00:00")
  assert result.frame.iloc[0]["amount"] == 11000
  assert result.metadata["adjustment"] == "qfq"
  assert result.metadata["point_in_time"] is False
  assert result.metadata["missing_fields"] == []


def test_missing_fields_are_disclosed_not_manufactured():
  result = providers().normalize_bars(pd.DataFrame({
    "date": ["2026-09-04"], "close": [11], "volume": [1000],
  }), "600519.SS", "raw", "2026-09-05T10:00:00+00:00")
  assert set(result.metadata["missing_fields"]) == {"open", "high", "low", "amount"}
  assert pd.isna(result.frame.iloc[0]["amount"])


def test_real_process_timeout_and_bounded_bytes():
  module = providers()
  start = time.monotonic()
  with pytest.raises(module.ProviderError, match="provider_timeout"):
    module.run_process([sys.executable, "-c", "import time; time.sleep(10)"], .2, 1024)
  assert time.monotonic() - start < 3
  with pytest.raises(module.ProviderError, match="response_too_large"):
    module.run_process([sys.executable, "-c", "print('x'*4096)"], 3, 1024)


def test_transient_retries_use_module_and_bytes(monkeypatch):
  module = providers()
  calls = []

  def run(command, timeout, max_bytes):
    calls.append(command)
    assert 0 < timeout <= 45
    assert max_bytes <= 16 * 1024 * 1024
    if len(calls) < 3:
      return json.dumps({"error": "provider_transient"}).encode()
    return json.dumps({"rows": [{"ticker": "600519.SS"}], "metadata": {}}).encode()

  monkeypatch.setattr(module, "run_process", run)
  result = module.ResearchProvider(backoff=0).fetch("universe")
  assert result.frame.iloc[0]["ticker"] == "600519.SS"
  assert len(calls) == 3
  assert calls[0][:4] == [sys.executable, "-m", "stock_forecaster.research.providers", "universe"]


def test_permanent_errors_are_sanitized_and_not_retried(monkeypatch):
  module = providers()
  calls = []

  def run(*args):
    calls.append(args)
    return b'{"error":"password=secret https://vendor/private"}'

  monkeypatch.setattr(module, "run_process", run)
  with pytest.raises(module.ProviderError, match="provider_failed") as error:
    module.ResearchProvider(backoff=0).fetch("industry")
  assert "secret" not in str(error.value)
  assert len(calls) == 1


def test_universe_does_not_claim_historical_membership():
  result = providers().normalize_universe(pd.DataFrame({
    "code": ["600519", "000001", "920001"], "name": ["A", "B", "C"],
  }), "2026-09-05T10:00:00+00:00")
  assert result.frame["ticker"].tolist() == ["600519.SS", "000001.SZ", "920001.BJ"]
  assert result.metadata["point_in_time"] is False
  assert result.metadata["coverage"] == "current_universe_only"


def test_industry_keeps_unsupported_rows_for_audit():
  result = providers().normalize_industry(pd.DataFrame({
    "symbol": ["600519", "200001", "900001"], "start_date": ["2014-01-01"] * 3,
    "industry_code": ["801120"] * 3, "update_time": ["2014-01-02"] * 3,
  }), "2026-09-05T10:00:00+00:00")
  assert len(result.frame) == 3
  assert result.frame.symbol.tolist() == ["600519", "200001", "900001"]
  assert result.frame.ticker.isna().sum() == 2
  assert result.metadata["failed"] == 2


def test_invalid_numeric_fields_are_missing_not_verified_coverage():
  result = providers().normalize_bars(pd.DataFrame({
    "date": ["2026-09-04"], "open": [10], "high": [12], "low": [9],
    "close": [11], "volume": ["unknown"], "amount": [11000],
  }), "600519.SS", "qfq", "2026-09-05T10:00:00+00:00")
  assert "volume" in result.metadata["missing_fields"]
  assert result.metadata["volume_unit"] == "shares"
  assert result.metadata["amount_unit"] == "CNY"


def test_download_calls_actual_contract_with_explicit_adjustments(monkeypatch):
  from types import SimpleNamespace

  calls = []

  def history(*, symbol, start_date, end_date, adjust, timeout):
    calls.append((symbol, start_date, end_date, adjust, timeout))
    return pd.DataFrame({"date": ["2026-09-04"], "open": [10], "close": [11],
                         "high": [12], "low": [9], "volume": [1000], "amount": [11000]})

  monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(stock_zh_a_hist_tx=history))
  for adjustment in ("raw", "qfq"):
    result = providers().download("bars", ticker="600519.SS", start="2026-09-01",
                                  end="2026-09-04", adjustment=adjustment)
    assert result.metadata["missing_fields"] == []
  assert calls == [("sh600519", "20260901", "20260904", "", 10),
                   ("sh600519", "20260901", "20260904", "qfq", 10)]


def test_timeout_does_not_wait_for_descendant_inherited_stdout():
  module = providers()
  script = (
    "import subprocess,sys,time; "
    "subprocess.Popen([sys.executable,'-c','import time; time.sleep(2)']); "
    "time.sleep(5)"
  )
  start = time.monotonic()
  with pytest.raises(module.ProviderError, match="provider_timeout"):
    module.run_process([sys.executable, "-c", script], .3, 1024)
  assert time.monotonic() - start < 1.5
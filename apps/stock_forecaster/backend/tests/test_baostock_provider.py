import importlib

import pandas as pd
import pytest


def provider():
  return importlib.import_module("stock_forecaster.research.baostock_provider")


def test_baostock_discards_suspended_fill_prices_but_keeps_original_status():
  frame = pd.DataFrame({"date": ["2026-09-03", "2026-09-04"], "code": ["sh.600519"] * 2,
    "open": [10., 10.], "high": [11., 10.], "low": [9., 10.], "close": [10., 10.],
    "volume": [1200, 0], "amount": [12000, 0], "tradestatus": ["1", "0"],
    "isST": ["0", "1"], "adjustflag": ["3", "3"]})
  result = provider().normalize_prices(frame, "600519.SS", "raw", "2026-09-06T02:00:00+00:00")
  assert len(result.frame) == 1
  assert len(result.raw_frame) == 2
  assert result.frame.volume.iloc[0] == 1200
  assert result.frame.amount.iloc[0] == 12000
  assert result.metadata["suspended_rows"] == 1
  assert result.metadata["source"] == "baostock"
  assert result.metadata["point_in_time"] is False
  assert result.frame.is_st.iloc[0] == "0"


def test_baostock_industry_date_is_not_publication_or_effective_interval():
  frame = pd.DataFrame({"code": ["sz.002594"], "code_name": ["test"], "updateDate": ["2020-01-06"],
    "industry": ["C36"], "industryClassification": ["CSRC"]})
  result = provider().normalize_industries(frame, "2020-01-10", "2026-09-06T02:00:00+00:00")
  assert result.frame.query_date.iloc[0] == "2020-01-10"
  assert "effective_from" not in result.frame
  assert result.frame.known_at.iloc[0] == "2026-09-06T02:00:00+00:00"
  assert result.metadata["classification"] == "provider_reported_not_sw_substitution"


def test_baostock_explicitly_refuses_unverified_beijing_route():
  with pytest.raises(ValueError, match="provider_symbol_unsupported"):
    provider().to_symbol("920001.BJ")


def test_baostock_stock_requires_adjustment_marker_but_index_raw_does_not():
  frame = pd.DataFrame({"date": ["2026-09-04"], "code": ["sh.600519"], "open": [10], "high": [11],
                        "low": [9], "close": [10], "volume": [100], "amount": [1000]})
  with pytest.raises(ValueError, match="invalid_bars"):
    provider().normalize_prices(frame, "600519.SS", "qfq", "2026-09-06T02:00:00+00:00")
  result = provider().normalize_prices(frame.assign(code="sh.000001"), "000001.SS", "raw", "2026-09-06T02:00:00+00:00")
  assert result.metadata["instrument_type"] == "index"


def test_baostock_missing_ohlc_is_disclosed_by_shared_validator():
  frame = pd.DataFrame({"date": ["2026-09-04"], "code": ["sh.600519"], "open": [10],
                        "low": [9], "close": [10], "volume": [100], "amount": [1000], "adjustflag": ["3"]})
  result = provider().normalize_prices(frame, "600519.SS", "raw", "2026-09-06T02:00:00+00:00")
  assert result.metadata["missing_fields"] == ["high"]
  assert "incomplete_ohlcv_amount" in result.metadata["blockers"]
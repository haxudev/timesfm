import numpy as np
import pandas as pd
import pytest


def test_labels_use_explicit_sessions_and_decimal_units():
  from stock_forecaster.research.labels import make_labels

  sessions = pd.to_datetime(["2026-02-13", "2026-02-24", "2026-02-25"])
  bars = pd.DataFrame(
    {"ticker": ["A"] * 3, "date": sessions, "close": [100.0, 110.0, 99.0]}
  )
  result = make_labels(bars, sessions, horizons=(1, 2))
  first = result[(result.origin == sessions[0]) & (result.horizon == 2)].iloc[0]
  assert first.target_date == sessions[2]
  assert first["return"] == pytest.approx(-0.01)
  assert first.up == 0
  assert first.realized_variance == pytest.approx(0.020184864008725236)
  one = result[(result.origin == sessions[0]) & (result.horizon == 1)].iloc[0]
  assert one["return"] == pytest.approx(0.1)
  assert one.up == 1
  assert result[result.origin == sessions[-1]]["return"].isna().all()


def test_missing_intermediate_session_invalidates_entire_label():
  from stock_forecaster.research.labels import make_labels

  sessions = pd.date_range("2026-01-05", periods=4, freq="B")
  bars = pd.DataFrame(
    {"ticker": ["A"] * 3, "date": sessions[[0, 2, 3]], "close": [100.0, 120.0, 120.0]}
  )
  result = make_labels(bars, sessions, horizons=(1, 2))
  first = result[result.origin == sessions[0]]
  assert first[["return", "up", "realized_variance"]].isna().all().all()
  assert first.loc[first.horizon == 1, "target_date"].iloc[0] == sessions[1]
  zero = result[(result.origin == sessions[2]) & (result.horizon == 1)].iloc[0]
  assert zero["return"] == 0
  assert zero.up == 0
  assert zero.realized_variance == 0


def test_missing_origin_is_retained_for_coverage_counts():
  from stock_forecaster.research.labels import make_labels

  sessions = pd.date_range("2026-01-05", periods=4, freq="B")
  bars = pd.DataFrame(
    {"ticker": ["A"] * 3, "date": sessions[[0, 2, 3]], "close": [100., 110., 120.]}
  )
  result = make_labels(bars, sessions, horizons=(1,))
  assert result.origin.tolist() == sessions.tolist()
  missing = result.loc[result.origin == sessions[1]].iloc[0]
  assert missing.target_date == sessions[2]
  assert pd.isna(missing["return"])
  assert pd.isna(missing.up)


@pytest.mark.parametrize("close", [0, -1, np.inf, np.nan])
def test_invalid_prices_do_not_produce_labels(close):
  from stock_forecaster.research.labels import make_labels

  sessions = pd.date_range("2026-01-05", periods=3, freq="B")
  bars = pd.DataFrame(
    {"ticker": ["A"] * 3, "date": sessions, "close": [100.0, close, 110.0]}
  )
  result = make_labels(bars, sessions, horizons=(2,))
  assert pd.isna(result.iloc[0]["return"])


def test_duplicate_bars_and_invalid_calendars_are_rejected():
  from stock_forecaster.research.labels import make_labels

  sessions = pd.date_range("2026-01-05", periods=3, freq="B")
  bars = pd.DataFrame({"ticker": ["A"] * 3, "date": sessions, "close": [1, 2, 3]})
  with pytest.raises(ValueError, match="duplicate_bars"):
    make_labels(pd.concat([bars, bars.iloc[:1]]), sessions)
  for bad in [sessions[::-1], sessions[[0, 0]], pd.DatetimeIndex([])]:
    with pytest.raises(ValueError, match="invalid_sessions"):
      make_labels(bars, bad)
  with pytest.raises(ValueError, match="invalid_horizon"):
    make_labels(bars, sessions, horizons=(0,))

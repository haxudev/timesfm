import numpy as np
import pandas as pd
import pytest


def market_fixture():
  sessions = pd.bdate_range("2026-01-05", periods=26)
  records = []
  for ticker, growth in [("A", 1.01), ("B", 1.03)]:
    for position, session in enumerate(sessions):
      close = 100 * growth**position
      records.append(
        {
          "ticker": ticker,
          "date": session,
          "close": close,
          "open": close,
          "high": close * 1.02,
          "low": close * 0.98,
          "volume": 100.0,
          "amount": 1000.0,
        }
      )
  bars = pd.DataFrame(records)
  index = pd.DataFrame(
    {"date": sessions, "close": 100 * 1.005 ** np.arange(26)}, index=sessions
  )
  industries = pd.DataFrame(
    {
      "ticker": ["A", "B"],
      "industry": ["X", "X"],
      "effective_from": [sessions[0]] * 2,
      "known_at": [sessions[0]] * 2,
    }
  )
  return bars, index, industries, sessions


def test_feature_units_and_equal_weight_industry_returns():
  from stock_forecaster.research.features import build_features

  bars, index, industries, sessions = market_fixture()
  result = build_features(bars, index, industries, sessions[-1])
  assert result.index.name == "ticker"
  assert list(result.index) == ["A", "B"]
  assert result.loc["A", "industry"] == "X"
  assert result.loc["A", "momentum_1"] == pytest.approx(0.01)
  assert result.loc["A", "momentum_5"] == pytest.approx(1.01**5 - 1)
  assert result.loc["A", "momentum_20"] == pytest.approx(1.01**20 - 1)
  assert result.loc["A", "volatility_5"] == pytest.approx(0, abs=1e-14)
  assert result.loc["A", "volatility_20"] == pytest.approx(0, abs=1e-14)
  assert result.loc["A", "volume_ratio_20"] == 1
  assert result.loc["A", "amount_ratio_20"] == 1
  assert result.loc["A", "amplitude"] == pytest.approx(0.0404)
  assert result.loc["A", "index_momentum_5"] == pytest.approx(1.005**5 - 1)
  assert result.loc["A", "industry_momentum_5"] == pytest.approx(1.02**5 - 1)
  assert result.loc["A", "industry_relative_5"] == pytest.approx(1.01**5 - 1.02**5)
  assert not {"return", "target_date", "up", "realized_variance"} & set(result.columns)


def test_future_price_and_industry_changes_cannot_change_old_features():
  from stock_forecaster.research.features import build_features

  bars, index, industries, sessions = market_fixture()
  origin = sessions[-2]
  expected = build_features(bars, index, industries, origin)
  bars.loc[bars.date > origin, ["close", "volume", "amount"]] = 1e20
  bars["return"] = 999
  index.loc[index.date > origin, "close"] = 1e30
  future = pd.DataFrame(
    {
      "ticker": ["A"],
      "industry": ["FUTURE"],
      "effective_from": [sessions[0]],
      "known_at": [sessions[-1]],
    }
  )
  changed = build_features(bars, index, pd.concat([industries, future]), origin)
  pd.testing.assert_frame_equal(expected, changed)


def test_industry_return_uses_historical_members_not_current_members_backfilled():
  from stock_forecaster.research.features import build_features

  bars, index, industries, sessions = market_fixture()
  industries["effective_to"] = pd.NaT
  industries.loc[industries.ticker == "B", "effective_to"] = sessions[-3]
  new = pd.DataFrame(
    {
      "ticker": ["B"],
      "industry": ["Y"],
      "effective_from": [sessions[-3]],
      "known_at": [sessions[-3]],
    }
  )
  result = build_features(bars, index, pd.concat([industries, new]), sessions[-1])
  assert result.loc["A", "industry_momentum_5"] == pytest.approx(1.02**3 * 1.01**2 - 1)


@pytest.mark.parametrize(
  "problem",
  [
    "missing_columns",
    "missing_ticker",
    "late_known",
    "only_current",
    "expired",
    "null_industry",
  ],
)
def test_inadequate_pit_industry_is_a_hard_block(problem):
  from stock_forecaster.research.features import build_features

  bars, index, industries, sessions = market_fixture()
  if problem == "missing_columns":
    industries = industries.drop(columns="known_at")
  elif problem == "missing_ticker":
    industries = industries.iloc[:1]
  elif problem == "late_known":
    industries["known_at"] = sessions[-1] + pd.Timedelta(days=1)
  elif problem == "only_current":
    industries["effective_from"] = sessions[-1]
  elif problem == "expired":
    industries["effective_to"] = sessions[-2]
  elif problem == "null_industry":
    industries.loc[0, "industry"] = None
  with pytest.raises(ValueError, match="missing_pit_industry"):
    build_features(bars, index, industries, sessions[-1])


def test_missing_stock_session_not_treated_as_next_trading_day():
  from stock_forecaster.research.features import build_features

  bars, index, industries, sessions = market_fixture()
  bars = bars[~((bars.ticker == "A") & (bars.date == sessions[-3]))]
  with pytest.raises(ValueError, match="incomplete_bar_history"):
    build_features(bars, index, industries, sessions[-1])


def test_calendar_is_explicit_and_missing_benchmark_cannot_be_filled():
  from stock_forecaster.research.features import build_features

  bars, index, industries, sessions = market_fixture()
  with pytest.raises(ValueError, match="missing_sessions"):
    build_features(bars, index.reset_index(drop=True), industries, sessions[-1])
  result = build_features(
    bars, index.reset_index(drop=True), industries, sessions[-1], sessions=sessions
  )
  assert len(result) == 2
  with pytest.raises(ValueError, match="incomplete_index_history"):
    build_features(
      bars, index.drop(sessions[-3]), industries, sessions[-1], sessions=sessions
    )


def test_overlapping_industries_and_duplicate_bars_are_rejected():
  from stock_forecaster.research.features import build_features

  bars, index, industries, sessions = market_fixture()
  overlapping = industries.iloc[:1].copy()
  overlapping["effective_from"] = sessions[1]
  overlapping["industry"] = "Y"
  with pytest.raises(ValueError, match="ambiguous_pit_industry"):
    build_features(bars, index, pd.concat([industries, overlapping]), sessions[-1])
  with pytest.raises(ValueError, match="duplicate_bars"):
    build_features(pd.concat([bars, bars.iloc[-1:]]), index, industries, sessions[-1])

"""Session-aligned targets. Missing prices invalidate labels; nothing is filled."""

import numpy as np
import pandas as pd


def validate_sessions(sessions: pd.DatetimeIndex) -> pd.DatetimeIndex:
  """Require a unique ascending, timezone-naive calendar of midnight sessions."""
  if (
    not isinstance(sessions, pd.DatetimeIndex)
    or sessions.empty
    or sessions.tz is not None
    or sessions.hasnans
    or not sessions.is_unique
    or not sessions.is_monotonic_increasing
    or not sessions.equals(sessions.normalize())
  ):
    raise ValueError("invalid_sessions: provide an ascending explicit session calendar")
  return sessions


def make_labels(
  bars: pd.DataFrame,
  sessions: pd.DatetimeIndex,
  horizons: tuple[int, ...] = (1, 5, 20),
) -> pd.DataFrame:
  """Return ticker/origin/target_date/horizon/return/up/realized_variance.

  Return is cumulative decimal simple return; up is strictly return > 0.
  Realized variance is the sum of daily squared log returns (decimal squared).
  Every calendar origin within each supplied history span is retained, with
  NaN targets for missing quotes or incomplete intervals and NaT target_date
  when the explicit calendar does not extend far enough.
  """
  validate_sessions(sessions)
  if (
    not horizons
    or any(
      isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 1
      for horizon in horizons
    )
    or len(set(horizons)) != len(horizons)
  ):
    raise ValueError("invalid_horizon: horizons must be distinct positive integers")
  if not {"ticker", "date", "close"}.issubset(bars.columns):
    raise ValueError("missing_bar_columns: ticker/date/close required")
  data = bars[["ticker", "date", "close"]].copy()
  data["date"] = pd.to_datetime(data["date"])
  if data[["ticker", "date"]].isna().any().any():
    raise ValueError("invalid_bars: ticker/date cannot be null")
  if data.duplicated(["ticker", "date"]).any():
    raise ValueError("duplicate_bars: ticker/date must be unique")
  if not data["date"].isin(sessions).all():
    raise ValueError("off_calendar_bars: every bar date must be in sessions")
  data["close"] = pd.to_numeric(data["close"], errors="coerce")
  rows = []
  for ticker, group in data.groupby("ticker", sort=True):
    prices = group.set_index("date")["close"].reindex(sessions).to_numpy(float)
    origins = sessions[(sessions >= group["date"].min()) & (sessions <= group["date"].max())]
    for origin in origins:
      position = sessions.get_loc(origin)
      for horizon in horizons:
        end = position + horizon
        target = sessions[end] if end < len(sessions) else pd.NaT
        simple_return = up = variance = np.nan
        interval = prices[position : end + 1]
        if end < len(sessions) and np.isfinite(interval).all() and (interval > 0).all():
          simple_return = float(interval[-1] / interval[0] - 1)
          up = float(simple_return > 0)
          variance = float(np.square(np.diff(np.log(interval))).sum())
        rows.append((ticker, origin, target, horizon, simple_return, up, variance))
  return pd.DataFrame(
    rows,
    columns=[
      "ticker",
      "origin",
      "target_date",
      "horizon",
      "return",
      "up",
      "realized_variance",
    ],
  )

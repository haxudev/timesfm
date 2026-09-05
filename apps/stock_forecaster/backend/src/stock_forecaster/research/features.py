"""Origin-bounded, complete-session price and point-in-time industry features."""

from datetime import date

import numpy as np
import pandas as pd

from .labels import validate_sessions


def _memberships(
  industries: pd.DataFrame,
  tickers: pd.Index,
  window: pd.DatetimeIndex,
  origin: pd.Timestamp,
) -> pd.DataFrame:
  required = {"ticker", "industry", "effective_from", "known_at"}
  if not required.issubset(industries.columns):
    raise ValueError(
      "missing_pit_industry: ticker/industry/effective_from/known_at required"
    )
  records = industries.copy()
  records["known_at"] = pd.to_datetime(records["known_at"])
  records = records.loc[records.known_at <= origin].copy()
  records["effective_from"] = pd.to_datetime(records["effective_from"])
  if "effective_to" not in records:
    records["effective_to"] = pd.NaT
  records["effective_to"] = pd.to_datetime(records["effective_to"])
  records = records.loc[records.ticker.isin(tickers)]
  if (
    records["effective_from"].isna().any()
    or (
      records.effective_to.notna() & (records.effective_to <= records.effective_from)
    ).any()
  ):
    raise ValueError("missing_pit_industry: invalid effective interval")
  membership = pd.DataFrame(index=window, columns=tickers, dtype=object)
  for session in window:
    active = records.loc[
      (records.effective_from <= session)
      & (records.effective_to.isna() | (session < records.effective_to))
    ]
    if active.ticker.duplicated().any():
      raise ValueError(
        f"ambiguous_pit_industry: overlapping records at {session.date()}"
      )
    selected = active.set_index("ticker")["industry"].reindex(tickers)
    invalid = selected.isna() | selected.astype(str).str.strip().eq("")
    if invalid.any():
      missing = ",".join(map(str, selected.index[invalid]))
      raise ValueError(f"missing_pit_industry: {missing} at {session.date()}")
    membership.loc[session] = selected
  return membership


def build_features(
  bars: pd.DataFrame,
  index_bars: pd.DataFrame,
  industries: pd.DataFrame,
  origin: date | str,
  *,
  sessions: pd.DatetimeIndex | None = None,
) -> pd.DataFrame:
  """Return an ordered feature matrix indexed by ticker (no labels).

  bars: ticker,date,close,volume,open,high,low,amount. index_bars: date,close
  for ONE benchmark. Pass sessions explicitly, or supply the full authoritative
  calendar as index_bars' DatetimeIndex (including rows with missing quotes).
  Never infer sessions from observed bar rows. origin is an exact as-of cutoff;
  callers must provide completed daily bars and consistently naive timestamps.

  Require 21 complete sessions for all supplied tickers and the benchmark.
  Historical industry intervals are [effective_from,effective_to), known_at
  <= origin, and must cover every window session. Ambiguous records are rejected.
  Industry daily returns equally weight the supplied universe's prior-session
  members, including the stock itself. Each stock follows its historical
  membership path; this is NOT a full-market industry index unless the caller
  supplied the full PIT universe. Caller owns universe/snapshot provenance.

  momentum/index_momentum/industry_momentum/industry_relative_{1,5,20} are
  decimal simple returns (relative = stock minus industry). volatility_{5,20}
  is daily log-return sample std, ddof=1, not annualized. volume/amount_ratio_20
  divide current values by the inclusive trailing 20-session mean. amplitude
  = (high-low)/previous_close. Incomplete histories raise, never forward-fill.
  """
  cutoff = pd.Timestamp(origin)
  if pd.isna(cutoff) or cutoff.tz is not None or cutoff != cutoff.normalize():
    raise ValueError("invalid_origin: a timezone-naive session date is required")
  if sessions is None:
    if not isinstance(index_bars.index, pd.DatetimeIndex):
      raise ValueError("missing_sessions: pass sessions or a full calendar index")
    sessions = index_bars.index
  validate_sessions(sessions)
  if cutoff not in sessions:
    raise ValueError("invalid_origin: origin must belong to the session calendar")
  window = sessions[sessions <= cutoff][-21:]
  if len(window) < 21:
    raise ValueError("insufficient_history: 21 calendar sessions required")
  fields = ["close", "volume", "open", "high", "low", "amount"]
  if not {"ticker", "date", *fields}.issubset(bars.columns):
    raise ValueError("missing_bar_columns: complete OHLCV and amount required")
  data = bars[["ticker", "date", *fields]].copy()
  data["date"] = pd.to_datetime(data["date"])
  data = data.loc[data.date <= cutoff]
  if data.empty or data.ticker.isna().any():
    raise ValueError("incomplete_bar_history: nonempty ticker histories required")
  if data.duplicated(["ticker", "date"]).any():
    raise ValueError("duplicate_bars: ticker/date must be unique")
  if not data.date.isin(sessions).all():
    raise ValueError("off_calendar_bars: stock dates outside supplied calendar")
  tickers = pd.Index(sorted(data.ticker.unique()), name="ticker")
  membership = _memberships(industries, tickers, window, cutoff)
  matrices = {}
  for field in fields:
    matrix = (
      data.pivot(index="date", columns="ticker", values=field)
      .reindex(index=window, columns=tickers)
      .astype(float)
    )
    if not np.isfinite(matrix.to_numpy()).all():
      raise ValueError(f"incomplete_bar_history: missing/nonfinite {field} in window")
    if (matrix < 0).any().any() or (
      field in {"close", "open", "high", "low"} and (matrix <= 0).any().any()
    ):
      raise ValueError(f"invalid_bar_values: invalid {field}")
    matrices[field] = matrix
  if (matrices["high"] < matrices["low"]).any().any():
    raise ValueError("invalid_bar_values: high below low")
  if not {"date", "close"}.issubset(index_bars.columns):
    raise ValueError("missing_index_columns: date/close required")
  benchmark = index_bars[["date", "close"]].reset_index(drop=True).copy()
  benchmark["date"] = pd.to_datetime(benchmark["date"])
  benchmark = benchmark.loc[benchmark.date <= cutoff]
  if benchmark.date.duplicated().any():
    raise ValueError("duplicate_index_bars: supply exactly one benchmark")
  benchmark_close = benchmark.set_index("date")["close"].reindex(window).astype(float)
  if not np.isfinite(benchmark_close).all() or (benchmark_close <= 0).any():
    raise ValueError("incomplete_index_history: complete positive benchmark required")
  close = matrices["close"]
  daily = close.pct_change(fill_method=None).iloc[1:]
  industry_daily = pd.DataFrame(index=daily.index, columns=tickers, dtype=float)
  for position, session in enumerate(daily.index):
    groups = membership.iloc[position]
    means = daily.loc[session].groupby(groups).mean()
    industry_daily.loc[session] = groups.map(means)
  result = pd.DataFrame(index=tickers)
  result["industry"] = membership.iloc[-1].astype("category")
  for horizon in (1, 5, 20):
    result[f"momentum_{horizon}"] = close.iloc[-1] / close.iloc[-horizon - 1] - 1
    result[f"index_momentum_{horizon}"] = (
      benchmark_close.iloc[-1] / benchmark_close.iloc[-horizon - 1] - 1
    )
    result[f"industry_momentum_{horizon}"] = (
      1 + industry_daily.iloc[-horizon:]
    ).prod() - 1
    result[f"industry_relative_{horizon}"] = (
      result[f"momentum_{horizon}"] - result[f"industry_momentum_{horizon}"]
    )
  log_returns = np.log(close).diff().iloc[1:]
  for horizon in (5, 20):
    result[f"volatility_{horizon}"] = log_returns.iloc[-horizon:].std(ddof=1)
  for field in ("volume", "amount"):
    mean = matrices[field].iloc[-20:].mean()
    if (mean <= 0).any():
      raise ValueError(f"invalid_bar_values: zero trailing {field}")
    result[f"{field}_ratio_20"] = matrices[field].iloc[-1] / mean
  result["amplitude"] = (
    matrices["high"].iloc[-1] - matrices["low"].iloc[-1]
  ) / close.iloc[-2]
  return result

from datetime import datetime, timezone

import pandas as pd

from .providers import (
  INDUSTRY_BLOCKER,
  ProviderError,
  ProviderResult,
  _ticker,
  normalize_bars,
  normalize_universe,
)


def _metadata(kind, api_name, observed):
  return {"kind": kind, "provider": "baostock", "provider_version": "0.9.3",
          "source": "baostock", "api_name": api_name, "fetched_at": observed, "known_at": observed,
          "observed_at": observed, "published_at": None, "known_at_semantics": "first_observed_timestamp",
          "trusted": False, "point_in_time": False, "schema_version": 1}


def to_symbol(ticker):
  from ..market_data import normalize_ticker

  normalized = normalize_ticker(ticker)
  if normalized.endswith(".BJ"):
    raise ProviderError("provider_symbol_unsupported")
  return ("sh." if normalized.endswith(".SS") else "sz.") + normalized[:6]


def _rows(result):
  rows = []
  while result.error_code == "0" and result.next():
    rows.append(result.get_row_data())
    if len(rows) >= 20000:
      raise ProviderError("response_too_large")
  if result.error_code != "0":
    raise ProviderError("provider_failed")
  return pd.DataFrame(rows, columns=result.fields)


def normalize_prices(frame, ticker, adjustment, observed):
  from ..instruments import INDEX_NAMES
  from ..market_data import normalize_ticker

  ticker = normalize_ticker(ticker)
  if not {"code", "date", "volume", "amount"} <= set(frame) or not frame.code.eq(to_symbol(ticker)).all():
    raise ProviderError("invalid_bars")
  if ticker not in INDEX_NAMES and "adjustflag" not in frame:
    raise ProviderError("invalid_bars")
  if "adjustflag" in frame and not frame.adjustflag.astype(str).eq("3" if adjustment == "raw" else "2").all():
    raise ProviderError("invalid_bars")
  suspended = frame.tradestatus.astype(str).eq("0") if "tradestatus" in frame else pd.Series(False, index=frame.index)
  result = normalize_bars(frame.loc[~suspended], ticker, adjustment, observed)
  result.frame["source"] = "baostock"
  if "isST" in frame:
    result.frame["is_st"] = result.frame.date.map(frame.set_index("date").isST)
  result.metadata.update(_metadata("bars", "query_history_k_data_plus", observed),
    suspended_rows=int(suspended.sum()), unit_evidence="baostock_official_shares_cny",
    adjustment_method="baostock_return_based_vendor_qfq" if adjustment == "qfq" else "raw")
  result.raw_frame = frame.copy(deep=True)
  return result


def normalize_industries(frame, query_date, observed):
  if not {"code", "industry", "industryClassification", "updateDate"} <= set(frame):
    raise ProviderError("invalid_response")
  normalized = frame.copy()
  normalized["ticker"] = normalized.code.str.replace("sh.", "", regex=False).str.replace("sz.", "", regex=False).map(_ticker)
  normalized["query_date"] = query_date
  normalized["known_at"] = observed
  return ProviderResult(normalized, {**_metadata("industry", "query_stock_industry", observed),
    "classification": "provider_reported_not_sw_substitution", "blockers": [INDUSTRY_BLOCKER],
    "query_date": query_date, "failed": 0}, frame.copy(deep=True))


def download(operation, *, ticker=None, start=None, end=None, adjustment="raw", status="L"):
  try:
    import baostock as bs
  except ImportError:
    raise ProviderError("provider_dependency_missing") from None

  observed = datetime.now(timezone.utc).isoformat()
  if bs.login().error_code != "0":
    raise ProviderError("provider_transient")
  try:
    if operation == "universe":
      frame = _rows(bs.query_all_stock(day=end or ""))
      stocks = frame.loc[frame.code.str.match(r"^(sh\.(60|68)|sz\.(00|30))\d{4}$")].copy()
      stocks["code"] = stocks.code.str[3:]
      result = normalize_universe(stocks.rename(columns={"code_name": "name"}), observed)
      result.metadata.update(_metadata("universe", "query_all_stock", observed),
                             coverage="dated_vendor_universe_publication_unverified", query_date=end,
                             market_coverage="SH_SZ_only_BJ_unverified")
      result.raw_frame = frame
      return result
    if operation == "industry":
      return normalize_industries(_rows(bs.query_stock_industry(code=to_symbol(ticker) if ticker else "", date=end or "")), end, observed)
    if operation == "securities":
      frame = _rows(bs.query_stock_basic(code=to_symbol(ticker) if ticker else ""))
      return ProviderResult(frame.assign(known_at=observed), {**_metadata("securities", "query_stock_basic", observed),
        "blockers": ["historical_universe_unverified"]}, frame)
    if operation == "calendar":
      frame = _rows(bs.query_trade_dates(start_date=start, end_date=end))
      return ProviderResult(frame.assign(known_at=observed), _metadata("calendar", "query_trade_dates", observed), frame)
    if operation == "factors":
      frame = _rows(bs.query_adjust_factor(code=to_symbol(ticker), start_date=start, end_date=end))
      return ProviderResult(frame.assign(known_at=observed), {**_metadata("factors", "query_adjust_factor", observed),
        "blockers": ["historical_price_vintage_unverified"]}, frame)
    if operation != "bars" or adjustment not in {"raw", "qfq"}:
      raise ProviderError("invalid_operation")
    from ..instruments import INDEX_NAMES
    from ..market_data import normalize_ticker

    fields = "date,code,open,high,low,close,volume,amount"
    if normalize_ticker(ticker) not in INDEX_NAMES:
      fields += ",adjustflag,tradestatus,isST"
    elif adjustment != "raw":
      raise ProviderError("invalid_operation")
    frame = _rows(bs.query_history_k_data_plus(to_symbol(ticker), fields, start_date=start, end_date=end,
                                                frequency="d", adjustflag="3" if adjustment == "raw" else "2"))
    return normalize_prices(frame, ticker, adjustment, observed)
  finally:
    bs.logout()
import argparse
import json
import time
from contextlib import nullcontext
from datetime import datetime, timezone

import httpx
import numpy as np
import pandas as pd
from pydantic import SecretStr

from .providers import (
  INDUSTRY_BLOCKER,
  MAX_BYTES,
  ProviderError,
  ProviderResult,
  normalize_bars,
  normalize_universe,
)

API_LIMITS = {"daily": 6000, "adj_factor": 6000, "index_daily": 6000,
              "stock_basic": 6000, "index_member_all": 2000, "trade_cal": 6000}


def _metadata(kind, api_name, observed):
  return {"kind": kind, "provider": "tushare", "provider_version": "https-api-v1",
          "source": "tushare", "api_name": api_name, "fetched_at": observed,
          "known_at": observed, "observed_at": observed, "published_at": None,
          "known_at_semantics": "first_observed_timestamp", "trusted": False,
          "point_in_time": False, "schema_version": 1}


def to_symbol(ticker):
  from ..market_data import normalize_ticker

  return normalize_ticker(ticker).replace(".SS", ".SH")


def normalize_prices(frame, ticker, adjustment, observed, *, factors=None):
  from ..instruments import INDEX_NAMES
  from ..market_data import normalize_ticker

  ticker = normalize_ticker(ticker)
  api_name = "index_daily" if ticker in INDEX_NAMES else "daily"
  if not {"ts_code", "trade_date", "vol", "amount"} <= set(frame) or not frame.ts_code.eq(to_symbol(ticker)).all():
    raise ProviderError("invalid_bars")
  original = frame.copy(deep=True).assign(record_type=api_name)
  prices = frame.rename(columns={"trade_date": "date", "vol": "volume"}).copy()
  prices["date"] = pd.to_datetime(prices.date, format="%Y%m%d", errors="raise").dt.strftime("%Y-%m-%d")
  prices["volume"] = pd.to_numeric(prices.volume, errors="coerce") * 100
  prices["amount"] = pd.to_numeric(prices.amount, errors="coerce") * 1000
  anchor = None
  if adjustment == "qfq":
    if (factors is None or not {"ts_code", "trade_date", "adj_factor"} <= set(factors)
        or factors.empty or not factors.ts_code.eq(to_symbol(ticker)).all()
        or factors.trade_date.duplicated().any() or frame.trade_date.duplicated().any()):
      raise ProviderError("invalid_adjustment_factors")
    aligned = pd.to_numeric(factors.set_index("trade_date").adj_factor, errors="coerce").reindex(frame.trade_date)
    if not np.isfinite(aligned.to_numpy(float)).all() or not aligned.gt(0).all() or frame.empty:
      raise ProviderError("invalid_adjustment_factors")
    latest = frame.trade_date.max()
    ratio = aligned.to_numpy(float) / float(aligned.loc[latest])
    for field in ("open", "high", "low", "close"):
      prices[field] = pd.to_numeric(prices[field], errors="coerce") * ratio
    original = pd.concat([original, factors.assign(record_type="adj_factor")], ignore_index=True)
    anchor = pd.Timestamp(latest).date().isoformat()
  result = normalize_bars(prices, ticker, adjustment, observed)
  result.frame["source"] = "tushare"
  result.metadata.update(_metadata("bars", api_name, observed),
    unit_evidence="tushare_official_daily_index_daily_vol_hands_amount_thousand_cny",
    adjustment_anchor=anchor, adjustment_method="close_times_factor_over_window_end_factor" if anchor else "raw")
  result.raw_frame = original
  return result


def download(operation, *, ticker=None, start=None, end=None, adjustment="raw", status="L", client=None):
  from ..config import Settings

  client = client or TushareClient(Settings().tushare_api_key)
  observed = datetime.now(timezone.utc).isoformat()
  if operation in {"universe", "securities"}:
    fields = "ts_code,symbol,name,exchange,market,list_status,list_date,delist_date"
    params = {"list_status": "L" if operation == "universe" else status}
    if ticker:
      params["ts_code"] = to_symbol(ticker)
    frame = client.query("stock_basic", params, fields)
    if operation == "universe":
      result = normalize_universe(frame.rename(columns={"symbol": "code"}), observed)
      result.metadata.update(_metadata("universe", "stock_basic", observed))
      result.raw_frame = frame
      return result
    normalized = frame.copy()
    normalized["ticker"] = frame.ts_code.str.replace(".SH", ".SS", regex=False)
    normalized["known_at"] = observed
    return ProviderResult(normalized, {**_metadata("securities", "stock_basic", observed),
      "list_status": status, "blockers": ["historical_universe_unverified"]}, frame)
  if operation == "industry":
    fields = "ts_code,l1_code,l1_name,l2_code,l2_name,l3_code,l3_name,in_date,out_date,is_new"
    params = {"ts_code": to_symbol(ticker)} if ticker else {}
    rows = [client.query("index_member_all", {**params, "is_new": "N"}, fields),
            client.query("index_member_all", {**params, "is_new": "Y"}, fields)]
    frame = pd.concat(rows, ignore_index=True).drop_duplicates()
    normalized = frame.copy()
    normalized["ticker"] = frame.ts_code.str.replace(".SH", ".SS", regex=False)
    normalized["industry"] = frame.l1_code
    normalized["effective_from"] = pd.to_datetime(frame.in_date, format="%Y%m%d", errors="coerce").dt.strftime("%Y-%m-%d")
    normalized["vendor_out_date"] = frame.out_date
    normalized["known_at"] = observed
    return ProviderResult(normalized, {**_metadata("industry", "index_member_all", observed),
      "classification": "SW_level_1_vendor_version_unverified", "blockers": [INDUSTRY_BLOCKER],
      "failed": int(normalized.effective_from.isna().sum())}, frame)
  first, last = pd.Timestamp(start), pd.Timestamp(end)
  if pd.isna(first) or pd.isna(last) or first > last or (last - first).days > 7305:
    raise ProviderError("invalid_operation")
  params = {"start_date": first.strftime("%Y%m%d"), "end_date": last.strftime("%Y%m%d")}
  if operation == "calendar":
    frame = client.query("trade_cal", {**params, "exchange": "SSE"}, "exchange,cal_date,is_open,pretrade_date")
    return ProviderResult(frame.assign(known_at=observed), _metadata("calendar", "trade_cal", observed), frame)
  params["ts_code"] = to_symbol(ticker)
  if operation == "factors":
    frame = client.query("adj_factor", params, "ts_code,trade_date,adj_factor")
    return ProviderResult(frame.assign(known_at=observed), {**_metadata("factors", "adj_factor", observed),
      "blockers": ["historical_price_vintage_unverified"]}, frame)
  if operation != "bars":
    raise ProviderError("invalid_operation")
  from ..instruments import INDEX_NAMES
  from ..market_data import normalize_ticker

  api_name = "index_daily" if normalize_ticker(ticker) in INDEX_NAMES else "daily"
  if api_name == "index_daily" and adjustment != "raw":
    raise ProviderError("invalid_operation")
  frame = client.query(api_name, params, "ts_code,trade_date,open,high,low,close,vol,amount")
  if not frame.empty and not frame.trade_date.between(params["start_date"], params["end_date"]).all():
    raise ProviderError("invalid_response")
  factors = client.query("adj_factor", params, "ts_code,trade_date,adj_factor") if adjustment == "qfq" else None
  return normalize_prices(frame, ticker, adjustment, observed, factors=factors)


class TushareClient:
  def __init__(self, token, *, client=None, timeout=12, max_bytes=MAX_BYTES):
    secret = token.get_secret_value() if isinstance(token, SecretStr) else token
    if not isinstance(secret, str) or not secret.strip():
      raise ProviderError("tushare_not_configured")
    if not 0 < timeout <= 30 or not 0 < max_bytes <= MAX_BYTES:
      raise ProviderError("invalid_operation")
    self._token = SecretStr(secret.strip())
    self.client, self.timeout, self.max_bytes = client, timeout, max_bytes

  def query(self, api_name, params, fields, *, row_limit=None):
    if api_name not in API_LIMITS or not isinstance(params, dict) or not isinstance(fields, str):
      raise ProviderError("invalid_operation")
    limit = row_limit if row_limit is not None else API_LIMITS[api_name]
    connection = nullcontext(self.client) if self.client is not None else httpx.Client(
      timeout=httpx.Timeout(self.timeout, connect=min(5, self.timeout)),
      trust_env=False, follow_redirects=False,
    )
    payload = {"api_name": api_name, "token": self._token.get_secret_value(), "params": params, "fields": fields}
    deadline = time.monotonic() + self.timeout
    try:
      with connection as client, client.stream("POST", "https://api.tushare.pro", json=payload,
                                                timeout=self.timeout, follow_redirects=False) as response:
        if response.status_code == 429:
          raise ProviderError("tushare_rate_limited")
        if response.status_code in {401, 403}:
          raise ProviderError("tushare_permission_denied")
        if response.status_code >= 500:
          raise ProviderError("provider_transient")
        if response.status_code != 200:
          raise ProviderError("provider_failed")
        output = bytearray()
        for chunk in response.iter_bytes():
          if len(output) + len(chunk) > self.max_bytes:
            raise ProviderError("response_too_large")
          if time.monotonic() > deadline:
            raise ProviderError("provider_timeout")
          output.extend(chunk)
      result = json.loads(output)
      if not isinstance(result, dict) or type(result.get("code")) is not int:
        raise ProviderError("invalid_response")
      if result["code"] != 0:
        message = str(result.get("msg", "")).lower()
        if "token" in message and any(value in message for value in ("不正确", "无效", "invalid", "错误", "过期")):
          raise ProviderError("tushare_auth_failed")
        if any(value in message for value in ("每分钟", "每小时", "每天", "频率", "rate limit")):
          raise ProviderError("tushare_rate_limited")
        if result["code"] == 2002 or any(value in message for value in ("权限", "积分", "permission")):
          raise ProviderError("tushare_permission_denied")
        raise ProviderError("provider_failed")
      data = result.get("data")
      if not isinstance(data, dict):
        raise ProviderError("invalid_response")
      columns, rows = data.get("fields"), data.get("items")
      if (not isinstance(columns, list) or not columns or not all(isinstance(name, str) for name in columns)
          or len(set(columns)) != len(columns) or not isinstance(rows, list)
          or any(not isinstance(row, list) or len(row) != len(columns) for row in rows)):
        raise ProviderError("invalid_response")
      if fields and set(columns) != set(fields.split(",")):
        raise ProviderError("invalid_response")
      if len(rows) >= limit:
        raise ProviderError("tushare_response_truncated")
      return pd.DataFrame(rows, columns=columns)
    except httpx.TimeoutException:
      raise ProviderError("provider_timeout") from None
    except httpx.RequestError:
      raise ProviderError("provider_transient") from None
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
      raise ProviderError("invalid_response") from None


def probe_access(store, *, client=None, rate_seconds=1.3, end=None):
  from ..config import Settings
  from .pipeline import ResearchPipeline
  from .runtime import digest

  if not 0 <= rate_seconds <= 10:
    raise ValueError("invalid_rate_budget")
  session = ResearchPipeline(store).completed_session()
  cutoff = min(pd.Timestamp(end).normalize(), session) if end else session
  start = (cutoff - pd.Timedelta(days=7)).strftime("%Y%m%d")
  end_date = cutoff.strftime("%Y%m%d")
  requests = [
    ("daily", "daily", {"ts_code": "000001.SZ", "start_date": start, "end_date": end_date},
     "ts_code,trade_date,open,high,low,close,pre_close,vol,amount"),
    ("adj_factor", "adj_factor", {"ts_code": "000001.SZ", "start_date": start, "end_date": end_date},
     "ts_code,trade_date,adj_factor"),
    ("index_daily", "index_daily", {"ts_code": "000300.SH", "start_date": start, "end_date": end_date},
     "ts_code,trade_date,open,high,low,close,vol,amount"),
    ("trade_cal", "trade_cal", {"exchange": "SSE", "start_date": start, "end_date": end_date},
     "exchange,cal_date,is_open,pretrade_date"),
    ("stock_basic_listed", "stock_basic", {"ts_code": "000001.SZ", "list_status": "L"},
     "ts_code,symbol,name,exchange,market,list_status,list_date,delist_date"),
    ("stock_basic_delisted", "stock_basic", {"ts_code": "000005.SZ", "list_status": "D"},
     "ts_code,symbol,name,exchange,market,list_status,list_date,delist_date"),
    ("industry_current", "index_member_all", {"ts_code": "000001.SZ", "is_new": "Y"},
     "ts_code,l1_code,l1_name,l2_code,l2_name,l3_code,l3_name,in_date,out_date,is_new"),
    ("industry_history", "index_member_all", {"ts_code": "000001.SZ", "is_new": "N"},
     "ts_code,l1_code,l1_name,l2_code,l2_name,l3_code,l3_name,in_date,out_date,is_new"),
  ]
  report = {"provider": "tushare", "as_of": datetime.now(timezone.utc).isoformat(),
            "data_as_of": cutoff.date().isoformat(), "subscription_purchased": False,
            "historical_pit_verified": False, "checks": []}
  if client is None:
    token = Settings().tushare_api_key
    client = TushareClient(token) if token is not None else None
  for check, api_name, params, fields in requests:
    entry = {"check": check, "api": api_name, "status": "unavailable", "rows": 0}
    try:
      if client is None:
        raise ProviderError("tushare_not_configured")
      frame = client.query(api_name, params, fields)
      if set(frame.columns) != set(fields.split(",")):
        raise ProviderError("invalid_response")
      observed = datetime.now(timezone.utc).isoformat()
      identifier = store.save_snapshot(frame, {
        "kind": "provider_response", "provider": "tushare", "source": "tushare:" + api_name,
        "operation": api_name, "request": params, "observed_at": observed, "first_seen_at": observed,
        "published_at": None, "known_at": observed, "known_at_semantics": "first_observed_timestamp",
        "trusted": False, "point_in_time": False, "payload_format": "api_data_fields_items",
        "content_digest": digest({"api": api_name, "params": params,
                                  "rows": json.loads(frame.to_json(orient="records"))}),
      })
      entry.update(status="available" if len(frame) else "empty", rows=len(frame), snapshot_id=identifier)
    except ProviderError as error:
      code = str(error)
      entry.update(code=code, status={"tushare_permission_denied": "permission_denied",
                    "tushare_auth_failed": "authentication_failed", "tushare_rate_limited": "rate_limited",
                    "tushare_not_configured": "not_configured"}.get(code, "unavailable"))
    report["checks"].append(entry)
    if rate_seconds:
      time.sleep(rate_seconds)
  available = sum(row["status"] == "available" for row in report["checks"])
  report["status"] = "available" if available == len(requests) else "partial" if available else "unavailable"
  report["report_snapshot"] = store.save_snapshot(pd.DataFrame({"report": [json.dumps(report, sort_keys=True)]}),
    {"kind": "provider_capabilities", "provider": "tushare", "as_of": report["as_of"]})
  return report


def main(argv=None):
  from .store import ResearchStore

  parser = argparse.ArgumentParser()
  parser.add_argument("--root", required=True)
  parser.add_argument("--end")
  args = parser.parse_args(argv)
  print(json.dumps(probe_access(ResearchStore(args.root), end=args.end), allow_nan=False))


if __name__ == "__main__":
  main()
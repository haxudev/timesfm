import argparse
import contextlib
import io
import json
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

INDUSTRY_BLOCKER = "historical_industry_publication_unverified"
MAX_BYTES = 16 * 1024 * 1024
ERROR_CODES = {
  "provider_timeout", "provider_transient", "provider_failed",
  "response_too_large", "invalid_response", "invalid_operation", "invalid_bars",
}


class ProviderError(ValueError):
  pass


@dataclass
class ProviderResult:
  frame: pd.DataFrame
  metadata: dict


def run_process(command: list[str], timeout: float, max_bytes: int) -> bytes:
  deadline = time.monotonic() + timeout
  cleanup_reserve = min(.25, timeout / 4)
  process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
  output = bytearray()
  finished = threading.Event()
  overflow = threading.Event()

  def collect():
    try:
      while chunk := process.stdout.read1(65536):
        if len(output) + len(chunk) > max_bytes:
          overflow.set()
          break
        output.extend(chunk)
    finally:
      process.stdout.close()
      finished.set()

  reader = threading.Thread(target=collect, daemon=True)
  reader.start()
  try:
    if not finished.wait(max(0, deadline - time.monotonic() - cleanup_reserve)):
      raise ProviderError("provider_timeout")
    if overflow.is_set():
      raise ProviderError("response_too_large")
    try:
      process.wait(timeout=max(.001, deadline - time.monotonic() - cleanup_reserve))
    except subprocess.TimeoutExpired as error:
      raise ProviderError("provider_timeout") from error
    if process.returncode and not output:
      raise ProviderError("provider_failed")
    return bytes(output)
  finally:
    if process.poll() is None:
      process.kill()
    try:
      process.wait(timeout=max(.001, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
      pass
    reader.join(timeout=max(0, deadline - time.monotonic()))


class ResearchProvider:
  def __init__(self, *, timeout: float = 45, attempts: int = 3, backoff: float = .5):
    if not 0 < timeout <= 45 or not 1 <= attempts <= 3 or not 0 <= backoff <= 5:
      raise ValueError("invalid_provider_budget")
    self.timeout, self.attempts, self.backoff = timeout, attempts, backoff

  def fetch(self, operation: str, **kwargs) -> ProviderResult:
    if operation not in {"universe", "bars", "industry"}:
      raise ProviderError("invalid_operation")
    command = [sys.executable, "-m", __name__, operation]
    for key, value in kwargs.items():
      if key not in {"ticker", "start", "end", "adjustment"}:
        raise ProviderError("invalid_operation")
      command.extend(["--" + key, str(value)])
    deadline = time.monotonic() + self.timeout
    for attempt in range(self.attempts):
      remaining = deadline - time.monotonic()
      if remaining <= 0:
        raise ProviderError("provider_timeout")
      try:
        payload = json.loads(run_process(command, remaining, MAX_BYTES))
        if not isinstance(payload, dict):
          raise ProviderError("invalid_response")
        if "error" in payload:
          code = payload["error"]
          raise ProviderError(code if code in ERROR_CODES else "provider_failed")
        if not isinstance(payload.get("rows"), list) or not isinstance(
          payload.get("metadata"), dict
        ):
          raise ProviderError("invalid_response")
        return ProviderResult(pd.DataFrame(payload["rows"]), payload["metadata"])
      except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as error:
        raise ProviderError("invalid_response") from error
      except ProviderError as error:
        if str(error) not in {"provider_transient", "provider_timeout"}:
          raise
        if attempt + 1 == self.attempts:
          raise
        time.sleep(min(self.backoff * 2 ** attempt, max(0, deadline - time.monotonic())))
    raise ProviderError("provider_failed")


def _ticker(code) -> str:
  text = str(code).strip()
  if re.fullmatch(r"\d{6}\.(SS|SZ|BJ)", text):
    return text
  text = text.zfill(6)
  if not re.fullmatch(r"(?:(?:60|68|00|30|92)\d{4}|[48]\d{5})", text):
    raise ValueError("unsupported_symbol")
  return text + (".SS" if text.startswith("6") else
                 ".SZ" if text.startswith(("0", "3")) else ".BJ")


def _metadata(kind: str, fetched_at: str) -> dict:
  return {"kind": kind, "provider": "akshare", "provider_version": "1.18.94",
          "fetched_at": fetched_at, "known_at": fetched_at,
          "point_in_time": False, "trusted": False, "schema_version": 1}


def normalize_universe(frame: pd.DataFrame, fetched_at: str) -> ProviderResult:
  if not {"code", "name"} <= set(frame):
    raise ProviderError("invalid_response")
  rows, errors = [], 0
  for row in frame.to_dict("records"):
    try:
      ticker = _ticker(row["code"])
      rows.append({"ticker": ticker, "name": str(row["name"]),
                   "instrument_type": "stock", "exchange": ticker[-2:],
                   "fetched_at": fetched_at})
    except ValueError:
      errors += 1
  normalized = pd.DataFrame(rows, columns=[
    "ticker", "name", "instrument_type", "exchange", "fetched_at",
  ])
  duplicates = int(normalized["ticker"].duplicated().sum())
  return ProviderResult(normalized.drop_duplicates("ticker"), {
    **_metadata("universe", fetched_at), "source": "stock_info_a_code_name",
    "coverage": "current_universe_only", "requested": len(frame),
    "failed": errors + duplicates, "blockers": ["historical_universe_unverified"],
  })


def normalize_bars(frame, ticker: str, adjustment: str, fetched_at: str) -> ProviderResult:
  from stock_forecaster.instruments import index_name
  from stock_forecaster.market_data import normalize_ticker

  ticker = normalize_ticker(ticker)
  if adjustment not in {"raw", "qfq"} or (index_name(ticker) and adjustment != "raw"):
    raise ProviderError("invalid_operation")
  frame = frame.copy()
  required = ["open", "high", "low", "close", "volume", "amount"]
  if not {"date", "close"} <= set(frame) or frame.empty:
    raise ProviderError("invalid_bars")
  for field in required:
    frame[field] = pd.to_numeric(frame[field], errors="coerce") if field in frame else float("nan")
    frame[field] = frame[field].replace([float("inf"), float("-inf")], float("nan"))
  missing = [field for field in required if frame[field].isna().any()]
  dates = pd.to_datetime(frame["date"], errors="coerce")
  invalid = dates.isna() | ~frame["close"].between(1e-12, 1e12)
  invalid |= frame["volume"].lt(0) | frame["amount"].lt(0)
  for field in ("open", "high", "low"):
    invalid |= frame[field].notna() & ~frame[field].between(1e-12, 1e12)
  invalid |= frame["high"].lt(frame[["open", "close", "low"]].max(axis=1))
  invalid |= frame["low"].gt(frame[["open", "close", "high"]].min(axis=1))
  frame["date"] = dates.dt.strftime("%Y-%m-%d")
  invalid |= frame["date"].duplicated(keep=False)
  failed = int(invalid.sum())
  frame = frame.loc[~invalid, ["date", *required]].sort_values("date")
  if frame.empty:
    raise ProviderError("invalid_bars")
  frame["ticker"] = ticker
  frame["adjustment"] = adjustment
  frame["source"] = "akshare:stock_zh_a_hist_tx"
  frame["fetched_at"] = fetched_at
  frame["known_at"] = fetched_at
  return ProviderResult(frame.reset_index(drop=True), {
    **_metadata("bars", fetched_at), "source": "akshare:stock_zh_a_hist_tx",
    "ticker": ticker, "instrument_type": "index" if index_name(ticker) else "stock",
    "adjustment": adjustment, "missing_fields": missing, "failed": failed,
    "currency": "CNY", "volume_unit": "shares", "amount_unit": "CNY",
    "unit_evidence": "installed_stock_zh_a_hist_tx_normalization",
    "blockers": ["historical_price_vintage_unverified"]
      + (["incomplete_ohlcv_amount"] if missing else []),
  })


def normalize_industry(frame: pd.DataFrame, fetched_at: str) -> ProviderResult:
  if not {"symbol", "start_date", "industry_code", "update_time"} <= set(frame):
    raise ProviderError("invalid_response")
  normalized = frame.copy()
  tickers = []
  for symbol in normalized["symbol"]:
    try:
      tickers.append(_ticker(symbol))
    except ValueError:
      tickers.append(None)
  normalized["ticker"] = tickers
  normalized["effective_from"] = pd.to_datetime(normalized["start_date"], errors="coerce").dt.strftime("%Y-%m-%d")
  normalized["vendor_updated"] = normalized["update_time"].astype(str)
  normalized["known_at"] = fetched_at
  normalized["fetched_at"] = fetched_at
  return ProviderResult(normalized, {
    **_metadata("industry", fetched_at), "source": "stock_industry_clf_hist_sw",
    "failed": int((normalized.ticker.isna() | normalized.effective_from.isna()).sum()),
    "blockers": [INDUSTRY_BLOCKER],
  })


def download(operation: str, **kwargs) -> ProviderResult:
  import akshare as ak

  fetched_at = datetime.now(timezone.utc).isoformat()
  if operation == "universe":
    return normalize_universe(ak.stock_info_a_code_name(), fetched_at)
  if operation == "industry":
    return normalize_industry(ak.stock_industry_clf_hist_sw(), fetched_at)
  from stock_forecaster.market_data import normalize_ticker

  ticker = normalize_ticker(kwargs["ticker"])
  symbol = {"SS": "sh", "SZ": "sz", "BJ": "bj"}[ticker[-2:]] + ticker[:6]
  frame = ak.stock_zh_a_hist_tx(
    symbol=symbol, start_date=kwargs["start"].replace("-", ""),
    end_date=kwargs["end"].replace("-", ""),
    adjust="" if kwargs["adjustment"] == "raw" else "qfq", timeout=10,
  )
  return normalize_bars(frame, ticker, kwargs["adjustment"], fetched_at)


def main(argv=None) -> int:
  parser = argparse.ArgumentParser()
  parser.add_argument("operation", choices=["universe", "bars", "industry"])
  parser.add_argument("--ticker")
  parser.add_argument("--start")
  parser.add_argument("--end")
  parser.add_argument("--adjustment", choices=["raw", "qfq"], default="raw")
  args = vars(parser.parse_args(argv))
  operation = args.pop("operation")
  try:
    with contextlib.redirect_stdout(io.StringIO()):
      result = download(operation, **args)
    output = json.dumps({"rows": json.loads(result.frame.to_json(orient="records", date_format="iso")),
                         "metadata": result.metadata}, allow_nan=False)
    if len(output.encode()) > MAX_BYTES:
      raise ProviderError("response_too_large")
    print(output)
    return 0
  except (ValueError, TypeError, KeyError, OSError, RuntimeError, ImportError) as error:
    import requests

    transient = isinstance(error, (requests.Timeout, requests.ConnectionError))
    if isinstance(error, requests.HTTPError) and error.response is not None:
      transient = error.response.status_code in {408, 429, 500, 502, 503, 504}
    code = str(error) if isinstance(error, ProviderError) else (
      "provider_transient" if transient else "provider_failed")
    print(json.dumps({"error": code if code in ERROR_CODES else "provider_failed"}))
    return 1


if __name__ == "__main__":
  raise SystemExit(main())
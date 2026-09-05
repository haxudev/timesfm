import json
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import duckdb
import numpy as np
import pandas as pd

from ..errors import AppError
from ..forecasting import _china_calendar, future_business_days
from ..instruments import INDEX_NAMES
from ..market_data import ProviderResult as MarketResult
from ..market_data import normalize_ticker
from .providers import ERROR_CODES, INDUSTRY_BLOCKER, ProviderError, ResearchProvider
from .runtime import digest

OPERATION_ERRORS = (ValueError, TypeError, LookupError, OSError, RuntimeError,
                    ArithmeticError, AppError, duckdb.Error, sqlite3.Error)


def snapshots(store, kind):
  with closing(sqlite3.connect(store.db_path.as_uri() + "?mode=ro", uri=True)) as db:
    for identifier, document in db.execute(
      "SELECT id, metadata FROM snapshots ORDER BY created_at DESC, rowid DESC"
    ):
      metadata = json.loads(document)
      if metadata.get("kind") == kind:
        yield identifier, metadata


def latest_bars(store, ticker, adjustment=None):
  for identifier, metadata in snapshots(store, "bars"):
    if ticker not in metadata.get("tickers", [metadata.get("ticker")]):
      continue
    if adjustment is not None and metadata.get("adjustment") != adjustment:
      continue
    frame = store.read_snapshot(identifier)
    frame = frame.loc[frame.ticker == ticker].copy()
    if not frame.empty:
      return identifier, frame, metadata
  return None


def error_code(error):
  if isinstance(error, ProviderError) and str(error) in ERROR_CODES:
    return str(error)
  if isinstance(error, AppError) and error.code in {
    "invalid_ticker", "insufficient_history", "artifact_changed", "calendar_unavailable",
  }:
    return error.code
  return "data_operation_failed"


class PersistedMarketProvider:
  def __init__(self, store, fallback=None):
    self.store, self.fallback = store, fallback

  def fetch(self, ticker, selection):
    return self.fetch_with_provenance(ticker, selection)[0]

  def fetch_with_provenance(self, ticker, selection):
    ticker = normalize_ticker(ticker)
    found = latest_bars(self.store, ticker, "raw" if ticker in INDEX_NAMES else "qfq")
    if found is None:
      if self.fallback is not None:
        return self.fallback.fetch(ticker, selection), None
      raise AppError("market_data_empty", "No persisted history for this ticker.", 404)
    identifier, frame, metadata = found
    frame.index = pd.to_datetime(frame["date"])
    if selection.start is not None:
      frame = frame.loc[(frame.index >= pd.Timestamp(selection.start)) &
                        (frame.index < pd.Timestamp(selection.end))]
    frame = frame.rename(columns={"close": "Close", "volume": "Volume"})
    return MarketResult(frame, "CNY", metadata["source"],
                        "index" if ticker in INDEX_NAMES else "stock"), identifier


class ResearchPipeline:
  def __init__(self, store, provider=None, *, rate_seconds=.25):
    if not 0 <= rate_seconds <= 60:
      raise ValueError("invalid_rate_budget")
    self.store = store
    self.provider = provider if provider is not None else ResearchProvider()
    self.rate_seconds = rate_seconds
    self.clock = lambda: datetime.now(timezone.utc)

  def completed_session(self):
    local = self.clock().astimezone(ZoneInfo("Asia/Shanghai"))
    day = pd.Timestamp(local.date())
    sessions = _china_calendar().sessions
    position = sessions.searchsorted(day, side="right" if local.hour >= 15 else "left") - 1
    if position < 0 or day > sessions[-1]:
      raise ValueError("calendar_unavailable")
    return sessions[position]

  def _fetch(self, operation, **kwargs):
    try:
      return self.provider.fetch(operation, **kwargs)
    finally:
      if self.rate_seconds:
        time.sleep(self.rate_seconds)

  def probe(self, *, limit=2, index_limit=2, start=None, end=None):
    end = min(pd.Timestamp(end) if end else self.completed_session(),
          self.completed_session()).date().isoformat()
    start = start or (pd.Timestamp(end) - pd.Timedelta(days=45)).date().isoformat()
    return self.bootstrap(start=start, end=end, limit=limit, index_limit=index_limit,
                          batch_size=min(100, max(1, limit + index_limit)), mode="probe")

  def bootstrap(
    self, *, start=None, end=None, limit=None, index_limit=6, batch_size=100,
    mode="bootstrap", on_batch=None,
  ):
    if (limit is not None and (isinstance(limit, bool) or not 1 <= limit <= 10000)) or not 0 <= index_limit <= 6:
      raise ValueError("invalid_universe_limit")
    if not 1 <= batch_size <= 100:
      raise ValueError("invalid_batch_size")
    end = min(pd.Timestamp(end) if end else self.completed_session(),
          self.completed_session()).date().isoformat()
    start = start or (pd.Timestamp(end) - pd.DateOffset(years=5)).date().isoformat()
    if pd.Timestamp(start) > pd.Timestamp(end):
      raise ValueError("invalid_date_range")
    report = {"mode": mode, "as_of": end, "start": start, "requested": 0,
              "succeeded": 0, "failed": 0, "eligible": 0,
              "eligibility_basis": "complete_downloaded_fields_not_PIT_training_approval",
              "errors": [], "bars_snapshots": [],
              "industry_snapshot": None, "blockers": [INDUSTRY_BLOCKER],
              "partial_universe": limit is not None, "field_coverage": [],
              "invalid_rows": 0, "source_coverage": {}, "successful_tickers": [],
              "selection": {"limit": limit, "index_limit": index_limit}, "phase": "collection"}
    try:
      universe = self._fetch("universe")
      stocks = universe.frame.ticker.drop_duplicates().tolist()
      report["universe_snapshot"] = self.store.save_snapshot(universe.frame, {
        **universe.metadata, "kind": "universe", "as_of": end,
      })
    except OPERATION_ERRORS as error:
      report["errors"].append({"operation": "universe", "code": error_code(error)})
      return self._finish_collection(report)
    report["universe_total"] = len(stocks)
    report["invalid_rows"] += int(universe.metadata.get("failed", 0))
    report["source_coverage"]["universe"] = "current_only"
    tickers = stocks[:limit] + list(INDEX_NAMES)[:index_limit]
    report["requested"] = len(tickers)
    report["stock_requested"] = len(stocks[:limit])
    report["index_requested"] = index_limit
    try:
      industry = self._fetch("industry")
      report["invalid_rows"] += int(industry.metadata.get("failed", 0))
      report["industry_snapshot"] = self.store.save_snapshot(industry.frame, {
        **industry.metadata, "kind": "industry", "as_of": end,
        "trusted": False, "point_in_time": False,
      })
      report["source_coverage"]["industry"] = "downloaded_publication_unverified"
    except (ValueError, TypeError, KeyError, OSError, RuntimeError, AppError, duckdb.Error, sqlite3.Error) as error:
      report["errors"].append({"operation": "industry", "code": error_code(error)})
      report["source_coverage"]["industry"] = "failed"
    for offset in range(0, len(tickers), batch_size):
      groups, successful = {}, []
      for ticker in tickers[offset:offset + batch_size]:
        ok = True
        eligible = True
        for adjustment in (("raw",) if ticker in INDEX_NAMES else ("raw", "qfq")):
          try:
            result = self._fetch("bars", ticker=ticker, start=start, end=end, adjustment=adjustment)
            if result.frame.empty:
              raise ProviderError("invalid_bars")
            groups.setdefault(adjustment, []).append(result)
            missing = result.metadata.get("missing_fields", [])
            report["field_coverage"].append({"ticker": ticker, "adjustment": adjustment,
                                             "missing": missing})
            report["invalid_rows"] += int(result.metadata.get("failed", 0))
            eligible = eligible and not missing and not result.metadata.get("failed", 0)
            report["blockers"].extend(result.metadata.get("blockers", []))
            if missing:
              report["blockers"].append("incomplete_ohlcv_amount")
          except (ValueError, TypeError, KeyError, OSError, RuntimeError, AppError) as error:
            ok = False
            report["errors"].append({"operation": "bars", "ticker": ticker,
                                      "adjustment": adjustment, "code": error_code(error)})
        report["succeeded" if ok else "failed"] += 1
        report["eligible"] += int(ok and eligible)
        if ok:
          successful.append(ticker)
      identifiers = {}
      for adjustment, results in groups.items():
        frame = pd.concat([result.frame for result in results], ignore_index=True)
        metadata = {key: value for key, value in results[0].metadata.items()
                    if key not in {"ticker", "instrument_type", "failed", "missing_fields"}}
        identifier = self.store.save_snapshot(frame, {
          **metadata, "kind": "bars", "adjustment": adjustment, "as_of": end,
          "tickers": frame.ticker.unique().tolist(),
          "universe_snapshot": report["universe_snapshot"],
          "members": [result.metadata for result in results],
          "trusted": False, "point_in_time": False,
        })
        report["bars_snapshots"].append(identifier)
        for ticker in frame.ticker.unique():
          if adjustment == ("raw" if ticker in INDEX_NAMES else "qfq"):
            identifiers[ticker] = identifier
      report["successful_tickers"].extend(successful)
      if on_batch is not None:
        try:
          on_batch(successful, identifiers)
        except OPERATION_ERRORS as error:
          report["errors"].append({"operation": "on_batch", "code": error_code(error)})
    return self._finish_collection(report)

  def _finish_collection(self, report):
    report["blockers"] = sorted(set(report["blockers"]))
    report["error_count"] = len(report["errors"]) + report["invalid_rows"]
    report["coverage_complete"] = False
    processed = report["succeeded"] + report["failed"]
    complete = bool(report.get("universe_snapshot")) and processed == report["requested"]
    success = complete and not report["failed"] and not report["error_count"]
    report["collection"] = {key: report[key] for key in ("requested", "succeeded", "failed", "eligible")}
    report["collection"].update(processed=processed, complete=complete, success=success,
      status="succeeded" if success else "partial" if processed else "failed")
    report["report_snapshot"] = self.store.save_snapshot(
      pd.DataFrame([{"report": json.dumps(report, sort_keys=True)}]),
      {"kind": "collection_report", "as_of": report["as_of"], "mode": report["mode"],
       "collection_success": success, "collection_complete": complete,
       "selection": report["selection"]},
    )
    return report

  def daily(self, service, *, limit=None, index_limit=6, batch_size=100,
            fit_missing=True, on_batch=None, submit_predictions=True):
    submitted, fitted, errors = [], [], []
    cutoff = service.session_date().isoformat()

    def enqueue(tickers, identifiers):
      if submit_predictions:
        for ticker in tickers:
          try:
            exists = (service.runtime.directory("garch", ticker) / "manifest.json").exists()
            if fit_missing and not exists:
              payload = {"ticker": ticker, "snapshot_id": identifiers[ticker],
                         "origin": cutoff, "submit_prediction": True}
              fitted.append(self.store.submit_job("fit_garch", payload, digest(payload)))
            else:
              submitted.append(service.submit(ticker, priority=0)["job_id"])
          except (ValueError, TypeError, KeyError, OSError, RuntimeError, AppError, sqlite3.Error) as error:
            errors.append({"ticker": ticker, "code": error_code(error)})
      if on_batch is not None:
        on_batch()

    try:
      report = self.bootstrap(end=cutoff, limit=limit, index_limit=index_limit,
                              batch_size=batch_size, mode="daily", on_batch=enqueue)
    except OPERATION_ERRORS as error:
      report = self._finish_collection({
        "mode": "daily", "as_of": cutoff, "requested": 0, "succeeded": 0,
        "failed": 0, "eligible": 0, "invalid_rows": 0, "blockers": [],
        "selection": {"limit": limit, "index_limit": index_limit},
        "errors": [{"operation": "collection", "code": error_code(error)}],
      })
    try:
      evaluation = self.score(as_of=cutoff)
    except OPERATION_ERRORS as error:
      evaluation = {"status": "failed", "errors": [{"code": error_code(error)}]}
    for identifier in fitted:
      job = self.store.get_job(identifier)
      child = (job.get("result") or {}).get("prediction_job_id")
      if child and child not in submitted:
        submitted.append(child)
    execution = {}
    for kind, identifiers in (("prediction", submitted), ("fit", fitted)):
      counts = dict.fromkeys(("pending", "running", "succeeded", "partial", "failed", "cancelled"), 0)
      for identifier in identifiers:
        counts[self.store.get_job(identifier)["status"]] += 1
      execution[kind] = {"requested": len(identifiers), **counts}
    execution["predicted"] = sum(
      any(any(row.get(field) is not None for field in (
        "timesfm_return", "lightgbm_return", "up_probability", "volatility",
      )) for row in (self.store.get_job(identifier).get("result") or {}).get("horizons", []))
      for identifier in submitted
    )
    execution["queued"] = sum(execution[kind]["pending"] + execution[kind]["running"] for kind in ("prediction", "fit"))
    report.update(submitted=len(submitted), prediction_jobs=submitted, fit_jobs=fitted,
                  submission_errors=errors, evaluation=evaluation, execution=execution,
                  priority_policy="scheduled_0_interactive_10_same_identity", phase="daily_final")
    failed = (not report["collection"]["success"] or errors or evaluation.get("errors")
        or any(execution[kind]["failed"] + execution[kind]["cancelled"] + execution[kind]["partial"]
          for kind in ("prediction", "fit")))
    report["status"] = "partial" if failed else "queued" if execution["queued"] else "succeeded"
    report["collection_report_snapshot"] = report.pop("report_snapshot")
    report["report_snapshot"] = self.store.save_snapshot(
      pd.DataFrame([{"report": json.dumps(report, sort_keys=True)}]),
      {"kind": "daily_report", "as_of": cutoff, "status": report["status"],
       "collection_success": report["collection"]["success"], "selection": report["selection"]},
    )
    return report

  def score(self, *, as_of=None):
    from .validation import probability_metrics, return_metrics, volatility_metrics

    as_of = min(pd.Timestamp(as_of).normalize() if as_of else self.completed_session(),
          self.completed_session())
    report = {"scored": 0, "pending": 0, "invalid": 0, "errors": [], "evaluation_snapshots": []}
    existing = {(metadata.get("forecast_id"), metadata.get("horizon"), metadata.get("realized_snapshot"))
                for _, metadata in snapshots(self.store, "evaluation") if metadata.get("scoring_version") == 2}
    with closing(sqlite3.connect(self.store.db_path.as_uri() + "?mode=ro", uri=True)) as db:
      jobs = db.execute("SELECT id, result FROM jobs WHERE kind='prediction' AND status IN ('succeeded','partial') ORDER BY rowid")
      for job_id, document in jobs:
        try:
          bundle = json.loads(document)
          frozen = self.store.read_snapshot(bundle["snapshot_id"])
          basis = self.store.snapshot_metadata(bundle["snapshot_id"])
          origin = pd.Timestamp(bundle["origin"])
          expected = [pd.Timestamp(day) for day in future_business_days(origin.date(),
                       max(row["horizon"] for row in bundle["horizons"]), bundle["ticker"])]
          expected_adjustment = "raw" if basis.get("adjustment") == "unadjusted" else basis.get("adjustment")
          found = latest_bars(self.store, bundle["ticker"], expected_adjustment)
          if found is None:
            found = latest_bars(self.store, bundle["ticker"])
          if found is None:
            report["pending"] += len(bundle["horizons"])
            continue
          realized_id, later, metadata = found
          prices = later.assign(date=pd.to_datetime(later.date)).set_index("date")["close"]
          before = frozen.assign(date=pd.to_datetime(frozen.date)).set_index("date")["price"].sort_index().loc[:origin]
          shared = before.index.intersection(prices.index)
          adjustment = lambda value: "raw" if value == "unadjusted" else value
          unresolved = (
            metadata.get("source") != basis.get("source")
            or adjustment(metadata.get("adjustment")) != adjustment(basis.get("adjustment"))
            or adjustment(basis.get("adjustment")) not in {"raw", "qfq"}
            or (basis.get("adjustment_revision") is not None and
                basis.get("adjustment_revision") != metadata.get("adjustment_revision"))
            or origin not in shared or not prices.index.is_unique
          )
          if not unresolved:
            unresolved = not np.allclose(before.loc[shared].to_numpy(float), prices.loc[shared].to_numpy(float), rtol=1e-9, atol=1e-10)
          issued = pd.Timestamp(bundle["issued_at"])
          open_at = (expected[0] + pd.Timedelta(hours=9, minutes=30)).tz_localize("Asia/Shanghai")
          close_at = (origin + pd.Timedelta(hours=15)).tz_localize("Asia/Shanghai")
          classification = "prospective" if issued.tz is not None and close_at <= issued < open_at else "replay"
          for signal in bundle["horizons"]:
            horizon = signal["horizon"]
            target = pd.Timestamp(signal["target_date"])
            if target > as_of:
              report["pending"] += 1
              continue
            key = (job_id, horizon, realized_id)
            if key in existing:
              continue
            dates = expected[:horizon]
            reason = "adjustment_unresolved" if unresolved else None
            if not reason and (target != dates[-1] or not all(day in prices.index for day in dates)):
              reason = "missing_target_sessions"
            interval = prices.reindex([origin, *dates]).to_numpy(float) if prices.index.is_unique else np.array([np.nan])
            if not reason and (not np.isfinite(interval).all() or (interval <= 0).any()):
              reason = "invalid_realized_prices"
            rows = []
            if reason is None:
              actual_return = float(interval[-1] / interval[0] - 1)
              variance = float(np.square(np.diff(np.log(interval))).sum())
              for model, field in (("timesfm", "timesfm_return"), ("lightgbm", "lightgbm_return")):
                if signal.get(field) is not None:
                  prediction = float(signal[field])
                  rows.append({"group": "return", "model": model, "actual": actual_return,
                               "predicted": prediction, "metrics": json.dumps(return_metrics([actual_return], [prediction]))})
              if signal.get("up_probability") is not None:
                prediction = float(signal["up_probability"])
                rows.append({"group": "probability", "model": "lightgbm", "actual": float(actual_return > 0),
                             "predicted": prediction, "metrics": json.dumps(probability_metrics([actual_return > 0], [prediction]))})
              if signal.get("volatility") is not None:
                sigma = float(signal["volatility"])
                if sigma < 0:
                  raise ValueError("invalid_sigma")
                rows.append({"group": "variance", "model": "garch", "actual": variance,
                             "predicted": sigma ** 2, "metrics": json.dumps(volatility_metrics([variance], [sigma ** 2]))})
              if rows:
                history_returns = np.diff(np.log(before.to_numpy(float)))
                if not len(history_returns) or not np.isfinite(history_returns).all():
                  raise ValueError("invalid_frozen_returns")
                mean_prediction = float(np.expm1(history_returns.mean() * horizon))
                for name, prediction in (("zero_return", 0.), ("historical_mean", mean_prediction)):
                  rows.append({"group": "return", "model": name, "actual": actual_return,
                               "predicted": prediction, "metrics": json.dumps(return_metrics([actual_return], [prediction]))})
                ewma = float(history_returns[0] ** 2)
                for value in history_returns[1:]:
                  ewma = .94 * ewma + .06 * float(value ** 2)
                for name, prediction in (("historical_variance", float(np.var(history_returns)) * horizon),
                                         ("ewma", ewma * horizon)):
                  rows.append({"group": "variance", "model": name, "actual": variance,
                               "predicted": prediction, "metrics": json.dumps(volatility_metrics([variance], [prediction]))})
                trained = basis.get("model_provenance", {}).get("lightgbm", {}).get(str(horizon), {})
                prior = trained.get("training_up_prior")
                if (type(prior) in (int, float) and 0 <= prior <= 1 and trained.get("trained_through")
                    and pd.Timestamp(trained["trained_through"]) <= origin):
                  rows.append({"group": "probability", "model": "training_prior",
                    "actual": float(actual_return > 0), "predicted": prior,
                    "metrics": json.dumps(probability_metrics([actual_return > 0], [prior]))})
            if not rows:
              reason = reason or "no_available_signals"
              rows = [{"group": "invalid", "model": "none",
                       "actual": None, "predicted": None, "metrics": "{}"}]
            identifier = self.store.save_snapshot(pd.DataFrame(rows), {
              "kind": "evaluation", "forecast_id": job_id, "horizon": horizon,
              "ticker": bundle["ticker"], "origin": origin.date().isoformat(),
              "target_date": target.date().isoformat(), "issued_at": bundle["issued_at"],
              "label_dates": [day.date().isoformat() for day in dates],
              "forecast_snapshot": bundle["snapshot_id"], "realized_snapshot": realized_id,
              "valid": reason is None, "reason": reason, "classification": classification,
              "components": bundle.get("components", {}),
              "adjustment_check": "common_anchor_reconciled_not_total_return",
              "realized_proxy": "sum_squared_daily_log_returns", "schema_version": 1, "scoring_version": 2,
              "baseline_config": {"return_mean": "expm1_mean_frozen_log_return_times_horizon",
                "historical_variance_ddof": 0, "ewma_decay": .94,
                "ewma_initialization": "first_frozen_squared_log_return",
                "variance_aggregation": "daily_variance_times_horizon",
                "probability_prior": "frozen_training_metadata_only"},
            })
            report["evaluation_snapshots"].append(identifier)
            existing.add(key)
            report["invalid" if reason else "scored"] += 1
        except (ValueError, TypeError, KeyError, OSError, RuntimeError, AppError, duckdb.Error, sqlite3.Error) as error:
          report["errors"].append({"forecast_id": job_id, "code": error_code(error)})
    return report
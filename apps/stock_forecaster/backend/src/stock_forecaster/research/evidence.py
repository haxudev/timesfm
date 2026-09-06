import json
import math
import re
import sqlite3
from datetime import datetime
from pathlib import Path

import duckdb

from ..errors import AppError
from .providers import ERROR_CODES
from .store import ResearchStore

LIMITATIONS = {
  "historical_industry_publication_unverified", "historical_price_vintage_unverified",
  "historical_universe_unverified", "industry_features_not_in_specification",
  "not_point_in_time_backtest", "single_chronological_fold_not_validated",
  "daily_variance_proxy_only", "verified_dataset_required", "missing_target_sessions",
  "insufficient_training_features", "one_class_training", "one_class_calibration",
  "empty_purged_partition", "training_row_budget_exceeded", "training_memory_budget_exceeded",
}


def _number(value, *, probability=False, integer=False):
  if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
    return None
  if probability and value > 1:
    return None
  if integer and (int(value) != value or value > 10**9):
    return None
  return int(value) if integer else value


def _timestamp(value):
  try:
    return datetime.fromisoformat(value).isoformat() if isinstance(value, str) else None
  except ValueError:
    return None


def _date(value):
  timestamp = _timestamp(value)
  return timestamp[:10] if timestamp else None


def _ids(value):
  if not isinstance(value, dict) or not {"bars", "index"} <= set(value):
    return None
  result = {key: value[key] for key in ("bars", "index", "industries") if key in value}
  return result if all(isinstance(item, str) and re.fullmatch("[0-9a-f]{64}", item)
                       for item in result.values()) else None


def _codes(values):
  return sorted({value if isinstance(value, str) and value in LIMITATIONS else "additional_limitations"
                 for value in values[:30]}) if isinstance(values, list) else []


def _report(store, row):
  if (store.snapshots_dir / (row["id"] + ".parquet")).stat().st_size > 2 * 1024 * 1024:
    raise ValueError("Report budget exceeded")
  frame = store.read_snapshot(row["id"])
  if len(frame) != 1 or "report" not in frame or len(frame.iloc[0]["report"]) > 1024 * 1024:
    raise ValueError("Invalid report")
  value = json.loads(frame.iloc[0]["report"])
  if not isinstance(value, dict):
    raise TypeError("Invalid report")
  return value


def _horizon(report, horizon):
  metrics = report.get("metrics", {}).get(str(horizon), {})
  test = report.get("splits", {}).get(str(horizon), {}).get("test", {})
  mae = _number(metrics.get("return", {}).get("mae"))
  baseline = _number(metrics.get("zero_return", {}).get("mae"))
  brier = _number(metrics.get("probability", {}).get("brier"), probability=True)
  prior = _number(metrics.get("training_prior", {}).get("brier"), probability=True)
  return {
    "horizon": horizon, "test_rows": _number(test.get("n"), integer=True),
    "test_origins": _number(test.get("origins"), integer=True),
    "mae": mae, "baseline_mae": baseline, "brier": brier, "baseline_brier": prior,
    "auc": _number(metrics.get("probability", {}).get("auc"), probability=True),
    "mae_beats_baseline": mae < baseline if mae is not None and baseline is not None else None,
    "brier_beats_baseline": brier < prior if brier is not None and prior is not None else None,
  }


def _source_summaries(store, records):
  sources = {}
  checks = {"daily", "adj_factor", "index_daily", "trade_cal", "stock_basic_listed",
            "stock_basic_delisted", "industry_current", "industry_history"}
  statuses = {"available", "empty", "permission_denied", "authentication_failed", "rate_limited",
              "not_configured", "unavailable"}
  for row, metadata in records:
    kind = metadata.get("kind")
    if kind not in {"collection_report", "provider_capabilities"}:
      continue
    report = _report(store, row)
    provider = report.get("provider", metadata.get("provider", "akshare" if kind == "collection_report" else None))
    if provider not in {"akshare", "tushare", "baostock"}:
      continue
    source = sources.setdefault(provider, {"provider": provider, "collection": None, "access": None})
    if kind == "collection_report" and source["collection"] is None:
      collection = report.get("collection", {})
      source["collection"] = {
        "snapshot_id": row["id"], "checked_at": _timestamp(row["created_at"]),
        "data_as_of": _date(report.get("as_of")),
        "status": collection.get("status") if collection.get("status") in {"succeeded", "partial", "failed"} else "unknown",
        "requested": _number(collection.get("requested"), integer=True),
        "succeeded": _number(collection.get("succeeded"), integer=True),
        "industry_status": report.get("source_coverage", {}).get("industry") if report.get("source_coverage", {}).get("industry") in {
          "failed", "downloaded_publication_unverified"} else "unknown",
        "error_codes": sorted({error["code"] for error in report.get("errors", [])[:30]
                               if isinstance(error, dict) and error.get("code") in ERROR_CODES}),
      }
    elif kind == "provider_capabilities" and source["access"] is None:
      source["access"] = {"snapshot_id": row["id"], "checked_at": _timestamp(row["created_at"]),
        "checks": [{"check": check["check"], "status": check.get("status") if check.get("status") in statuses else "unavailable",
                    "rows": _number(check.get("rows"), integer=True)}
                   for check in report.get("checks", [])[:20] if isinstance(check, dict) and check.get("check") in checks]}
  return [sources[name] for name in ("akshare", "tushare", "baostock") if name in sources]


def evidence_overview(root):
  result = {"status": "empty", "usage": "local_research_only", "production_ready": False,
            "source_check": None, "experiment": None}
  if not (Path(root) / "research.sqlite3").exists():
    return result
  try:
    store = ResearchStore(root, read_only=True)
    with store._connect() as connection:
      rows = connection.execute(
        "SELECT id, metadata, created_at FROM snapshots "
        "WHERE json_extract(metadata, '$.kind') IN ('collection_report','training_report','readiness_report','provider_capabilities') "
        "ORDER BY created_at DESC, rowid DESC LIMIT 100"
      ).fetchall()
    records = [(row, json.loads(row["metadata"])) for row in rows]
    sources = _source_summaries(store, records)
    if sources:
      result["sources"] = sources
    source = next((row for row, meta in records if meta.get("kind") == "collection_report"), None)
    if source is not None:
      report = _report(store, source)
      collection = report.get("collection", {})
      result["source_check"] = {
        "snapshot_id": source["id"], "checked_at": _timestamp(source["created_at"]),
        "data_as_of": _date(report.get("as_of")),
        "scope": "sample" if report.get("partial_universe") is True else "unspecified",
        "requested": _number(collection.get("requested"), integer=True),
        "succeeded": _number(collection.get("succeeded"), integer=True),
        "failed": _number(collection.get("failed"), integer=True),
        "universe_total": _number(report.get("universe_total"), integer=True),
        "status": collection.get("status") if collection.get("status") in {"succeeded", "partial", "failed"} else "unknown",
        "industry_status": (report.get("source_coverage", {}).get("industry")
                            if report.get("source_coverage", {}).get("industry") in {
                              "failed", "downloaded_publication_unverified"} else "unknown"),
      }
    selected = next((row for row, meta in records if meta.get("kind") == "training_report"), None)
    if selected is not None:
      report = _report(store, selected)
      identifiers = _ids(report.get("dataset_ids"))
      if identifiers is None:
        raise ValueError("Invalid dataset identifiers")
      readiness_row = next((row for row, meta in records if meta.get("kind") == "readiness_report"
                            and _ids(meta.get("dataset_ids")) == identifiers), None)
      readiness = _report(store, readiness_row) if readiness_row is not None else {}
      if readiness_row is not None and _ids(readiness.get("dataset_ids")) != identifiers:
        raise ValueError("Dataset report mismatch")
      history = readiness.get("history", {})
      quality = readiness.get("quality", {})
      models = readiness.get("models", {})
      result["experiment"] = {
        "training_snapshot_id": selected["id"],
        "readiness_snapshot_id": readiness_row["id"] if readiness_row is not None else None,
        "dataset_ids": identifiers, "evaluated_at": _timestamp(report.get("as_of")),
        "mode": report.get("mode") if report.get("mode") in {"strict_pit", "historical_research"} else "unknown",
        "feature_set": report.get("feature_set") if report.get("feature_set") in {
          "price_index_v1", "research_features_v1"} else "unknown",
        "approval": "not_approved",
        "historical_ready": readiness.get("historical_research", {}).get("ready") is True,
        "strict_pit_ready": readiness.get("strict_pit", {}).get("ready") is True,
        "history": {"start": _date(history.get("start")), "end": _date(history.get("end")),
                    "sessions": _number(history.get("sessions"), integer=True),
                    "tickers": _number(history.get("tickers"), integer=True)},
        "quality": {key: _number(quality.get(key), integer=True) for key in (
          "stock_rows", "feature_rows", "missing_stock_sessions", "excluded_feature_rows")},
        "timesfm_context_eligible": _number(models.get("timesfm", {}).get("context_eligible_tickers"), integer=True),
        "garch_history_eligible": _number(models.get("garch", {}).get("history_eligible_tickers"), integer=True),
        "limitations": _codes(report.get("limitations", [])),
        "horizons": [_horizon(report, horizon) for horizon in (1, 5, 20)],
      }
    result["status"] = ("ready" if result["source_check"] and result["experiment"] and result["experiment"]["readiness_snapshot_id"]
                        else "partial" if result["source_check"] or result["experiment"] or sources else "empty")
    return result
  except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, sqlite3.Error, duckdb.Error) as error:
    raise AppError("evidence_unavailable", "Research evidence is unavailable.", 503) from error
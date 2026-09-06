import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from ..instruments import INDEX_NAMES
from .providers import INDUSTRY_BLOCKER
from .splits import purged_split
from .training import load_dataset, prepare_training_data, training_error


def _report(store, identifier, kind):
  if store.snapshot_metadata(identifier).get("kind") != kind:
    raise ValueError("dataset_kind_mismatch")
  frame = store.read_snapshot(identifier)
  if len(frame) != 1 or "report" not in frame:
    raise ValueError("invalid_dataset_manifest")
  return json.loads(frame.iloc[0]["report"])


def dataset_options(store, identifier):
  report = _report(store, identifier, "training_dataset")
  identifiers = report.get("snapshot_ids", {})
  if set(identifiers) != {"bars", "index"} or not all(identifiers.values()):
    raise ValueError("invalid_dataset_manifest")
  return identifiers


def assemble_dataset(store, *, collection_report, benchmark="000300.SS", max_rows=250000,
                     max_bytes=512 * 1024 * 1024):
  if benchmark not in INDEX_NAMES:
    raise ValueError("missing_benchmark")
  report = _report(store, collection_report, "collection_report")
  parents = report.get("bars_snapshots", [])
  if not parents or len(set(parents)) != len(parents):
    raise ValueError("invalid_collection_report")
  groups = {"bars": [], "index": []}
  sources, rows, memory = set(), 0, 0
  for identifier in parents:
    metadata = store.snapshot_metadata(identifier)
    if metadata.get("kind") != "bars" or metadata.get("universe_snapshot") != report.get("universe_snapshot"):
      raise ValueError("invalid_collection_report")
    if (store.snapshots_dir / (identifier + ".parquet")).stat().st_size > max_bytes:
      raise ValueError("training_memory_budget_exceeded")
    frame = store.read_snapshot(identifier)
    if not {"ticker", "date", "close", "known_at"} <= set(frame):
      raise ValueError("missing_bar_columns")
    for name, mask in (
      ("bars", ~frame.ticker.isin(INDEX_NAMES) & frame.ticker.notna() & (metadata.get("adjustment") == "qfq")),
      ("index", frame.ticker.eq(benchmark) & (metadata.get("adjustment") == "raw")),
    ):
      selected = frame.loc[mask].copy()
      if selected.empty:
        continue
      if not metadata.get("source"):
        raise ValueError("dataset_price_basis_required")
      sources.add(metadata["source"])
      for member in metadata.get("members", []):
        if member.get("source") != metadata["source"] or member.get("adjustment") != metadata["adjustment"]:
          raise ValueError("mixed_dataset_basis")
      if "source" in selected and not selected.source.eq(metadata["source"]).all():
        raise ValueError("mixed_dataset_basis")
      rows += len(selected)
      memory += int(selected.memory_usage(deep=True).sum())
      if rows > max_rows:
        raise ValueError("training_row_budget_exceeded")
      if memory > max_bytes:
        raise ValueError("training_memory_budget_exceeded")
      groups[name].append(selected)
  if len(sources) != 1:
    raise ValueError("mixed_dataset_basis")
  if not groups["index"]:
    raise ValueError("missing_benchmark")
  if not groups["bars"]:
    raise ValueError("insufficient_training_features")
  frames = {name: pd.concat(parts, ignore_index=True) for name, parts in groups.items()}
  for frame in frames.values():
    frame["date"] = pd.to_datetime(frame.date)
    if frame.duplicated(["ticker", "date"]).any():
      raise ValueError("duplicate_bars")
    if not frame.date.between(pd.Timestamp(report["start"]), pd.Timestamp(report["as_of"])).all():
      raise ValueError("invalid_collection_report")
  metadata = {
    "source": next(iter(sources)), "collection_report": collection_report,
    "parent_snapshots": parents, "universe_snapshot": report.get("universe_snapshot"),
    "industry_snapshot": report.get("industry_snapshot"),
    "trusted": False, "point_in_time": False, "known_at_semantics": "first_observed_timestamp",
    "blockers": sorted(set(report.get("blockers", []) + [
      INDUSTRY_BLOCKER, "historical_price_vintage_unverified", "historical_universe_unverified"])),
    "partial_universe": report.get("partial_universe", True),
  }
  identifiers = {name: store.save_snapshot(frame.sort_values(["ticker", "date"]).reset_index(drop=True), {
    **metadata, "kind": name, "adjustment": "qfq" if name == "bars" else "raw",
    "tickers": sorted(frame.ticker.unique().tolist()),
  }) for name, frame in frames.items()}
  result = {"snapshot_ids": identifiers, "collection_report": collection_report,
            "benchmark": benchmark, "rows": {name: len(frame) for name, frame in frames.items()},
            "tickers": sorted(frames["bars"].ticker.unique().tolist()),
            "mode": "historical_research", "feature_set": "price_index_v1",
            "limitations": metadata["blockers"]}
  result["dataset_snapshot"] = store.save_snapshot(pd.DataFrame({"report": [json.dumps(result, sort_keys=True)]}),
                                                    {**metadata, "kind": "training_dataset"})
  return result


def default_bounds(sessions):
  if len(sessions) < 160:
    raise ValueError("insufficient_training_features")
  return {name: sessions[position].date().isoformat() for name, position in zip(
    ("train_end", "validation_end", "calibration_end", "test_end"),
    (int(len(sessions) * .55) - 1, int(len(sessions) * .70) - 1,
     int(len(sessions) * .85) - 1, len(sessions) - 1), strict=True)}


def assess_dataset(store, *, bars, index, train_end=None, validation_end=None,
                   calibration_end=None, test_end=None, max_rows=250000,
                   max_bytes=512 * 1024 * 1024):
  identifiers = {"bars": bars, "index": index}
  result = {"dataset_ids": identifiers, "as_of": datetime.now(timezone.utc).isoformat(),
            "mode": "historical_research", "feature_set": "price_index_v1",
            "strict_pit": {"ready": False, "blockers": [INDUSTRY_BLOCKER]},
            "historical_research": {"ready": False, "blockers": []},
            "statistical_validity": "not_established", "splits": {}, "models": {}}
  try:
    load_dataset(store, **identifiers, mode="strict_pit", feature_set="price_index_v1",
                 max_rows=max_rows, max_bytes=max_bytes)
  except (ValueError, TypeError, KeyError) as error:
    result["strict_pit"]["blockers"].append(training_error(error))
  try:
    dataset = load_dataset(store, **identifiers, mode="historical_research", feature_set="price_index_v1",
                           max_rows=max_rows, max_bytes=max_bytes)
    result["limitations"] = dataset["limitations"] + ["single_chronological_fold_not_validated"]
    sessions = dataset["sessions"]
    result["history"] = {"start": sessions[0].date().isoformat(), "end": sessions[-1].date().isoformat(),
                         "sessions": len(sessions), "tickers": int(dataset["bars"].ticker.nunique())}
    contexts = []
    for ticker, group in dataset["bars"].groupby("ticker", sort=True):
      values = pd.to_numeric(group.set_index("date").close, errors="coerce").reindex(sessions).to_numpy(float)
      valid = np.isfinite(values) & (values > 0)
      for field in ("volume", "amount"):
        if field in group:
          activity = pd.to_numeric(group.set_index("date")[field], errors="coerce").reindex(sessions).to_numpy(float)
          valid &= np.isfinite(activity) & (activity > 0)
      count = int(np.cumprod(valid[::-1]).sum())
      contexts.append({"ticker": ticker, "consecutive_prices": count})
    result["models"] = {
      "timesfm": {"context_eligible_tickers": sum(row["consecutive_prices"] >= 33 for row in contexts),
                  "eligibility_basis": "positive_close_and_available_activity_fields_not_OHLC_features",
                  "mode": "pretrained_inference_not_finetuning"},
      "garch": {"history_eligible_tickers": sum(row["consecutive_prices"] >= 253 for row in contexts),
                "eligibility_basis": "positive_close_and_available_activity_fields_not_OHLC_features",
                "mode": "fit_requires_convergence_check"},
      "contexts": contexts,
    }
    ends = dict(zip(("train_end", "validation_end", "calibration_end", "test_end"),
                    (train_end, validation_end, calibration_end, test_end), strict=True))
    if all(value is None for value in ends.values()):
      ends = default_bounds(sessions)
    elif any(value is None for value in ends.values()):
      raise ValueError("chronological_split_bounds_required")
    if pd.Timestamp(ends["test_end"]) > sessions[-1]:
      raise ValueError("unmatured_training_labels")
    result["split_ends"] = ends
    features, labels, quality = prepare_training_data(dataset, max_rows=max_rows, max_bytes=max_bytes)
    result["quality"] = quality
    result["feature_columns"] = [name for name in features if name not in {"ticker", "origin"}]
    blockers = result["historical_research"]["blockers"]
    for horizon in (1, 5, 20):
      candidates = features.merge(labels.loc[labels.horizon == horizon], on=["ticker", "origin"],
                                   validate="one_to_one").dropna(subset=["return"])
      parts = purged_split(candidates, **ends)
      result["splits"][str(horizon)] = {
        name: {"n": len(part), "origins": int(part.origin.nunique()), "tickers": int(part.ticker.nunique()),
               "up": int(part.up.eq(1).sum()), "non_up": int(part.up.eq(0).sum()),
               "max_target": part.target_date.max().date().isoformat() if len(part) else None}
        for name, part in parts.items()}
      if any(part.empty for part in parts.values()):
        blockers.append("empty_purged_partition")
      for name in ("train", "calibration"):
        if parts[name].up.nunique() < 2:
          blockers.append("one_class_training" if name == "train" else "one_class_calibration")
    result["historical_research"]["ready"] = not blockers
  except (ValueError, TypeError, KeyError) as error:
    result["historical_research"]["blockers"].append(training_error(error))
  for name in ("strict_pit", "historical_research"):
    result[name]["blockers"] = sorted(set(result[name]["blockers"]))
  result["report_snapshot"] = store.save_snapshot(pd.DataFrame({"report": [json.dumps(result, sort_keys=True)]}),
    {"kind": "readiness_report", "dataset_ids": identifiers, "as_of": result["as_of"]})
  return result
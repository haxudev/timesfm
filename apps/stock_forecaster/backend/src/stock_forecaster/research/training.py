import argparse
import contextlib
import gc
import hashlib
import json
import os
import shutil
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from ..forecasting import _china_calendar
from .features import build_features
from .labels import make_labels
from .providers import INDUSTRY_BLOCKER
from .runtime import digest
from .splits import purged_split

TRAINING_CODES = {
  INDUSTRY_BLOCKER, "verified_dataset_required", "verified_snapshot_ids_required",
  "dataset_kind_mismatch", "dataset_snapshot_mismatch", "known_at_required",
  "known_at_after_session", "dataset_price_basis_required", "uncompleted_training_dates",
  "training_row_budget_exceeded", "training_memory_budget_exceeded",
  "chronological_split_bounds_required", "invalid_training_budget",
  "unmatured_training_labels", "empty_purged_partition", "active_reference_exists",
  "training_already_running", "non_continuous_sessions", "insufficient_garch_history",
  "insufficient_training_features", "missing_pit_industry", "ambiguous_pit_industry",
  "one_class_training", "one_class_calibration", "garch_nonconvergence",
  "invalid_garch_parameters", "invalid_split_bounds", "training_failed",
  "invalid_training_mode", "invalid_feature_set", "research_feature_set_required",
  "historical_research_candidate_only",
  "mixed_dataset_basis", "missing_benchmark", "invalid_collection_report",
  "invalid_dataset_manifest", "duplicate_bars", "off_calendar_bars",
  "missing_bar_columns", "invalid_bars", "duplicate_index_bars",
}


def training_error(error):
  code = str(error).split(":", 1)[0]
  return code if code in TRAINING_CODES else "training_failed"


def _hash_file(path):
  with Path(path).open("rb") as stream:
    return hashlib.file_digest(stream, "sha256").hexdigest()


def load_dataset(store, *, bars=None, index=None, industries=None, dataset_dir=None,
                 max_rows=250000, max_bytes=512 * 1024 * 1024,
                 mode="strict_pit", feature_set="research_features_v1"):
  if mode not in {"strict_pit", "historical_research"}:
    raise ValueError("invalid_training_mode")
  if feature_set not in {"research_features_v1", "price_index_v1"}:
    raise ValueError("invalid_feature_set")
  if mode == "historical_research" and feature_set != "price_index_v1":
    raise ValueError("research_feature_set_required")
  identifiers = {"bars": bars, "index": index, "industries": industries}
  required = {"bars", "index"} if feature_set == "price_index_v1" else set(identifiers)
  if "industries" not in required and industries is not None:
    raise ValueError("research_feature_set_required")
  identifiers = {name: value for name, value in identifiers.items() if name in required}
  if dataset_dir is not None:
    if any(identifiers.values()):
      raise ValueError("choose_snapshot_ids_or_dataset_directory")
    directory = Path(dataset_dir).resolve()
    manifest_path = directory / "manifest.json"
    if manifest_path.stat().st_size > 1024 * 1024:
      raise ValueError("dataset_manifest_too_large")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    identifiers = manifest.get("snapshot_ids", {})
    if set(identifiers) != required:
      raise ValueError("verified_snapshot_ids_required")
    for name, identifier in identifiers.items():
      store.snapshot_metadata(identifier)
      source = directory / (name + ".parquet")
      if source.is_symlink() or _hash_file(source) != _hash_file(store.snapshots_dir / (identifier + ".parquet")):
        raise ValueError("dataset_snapshot_mismatch")
  if not all(identifiers.values()):
    raise ValueError(INDUSTRY_BLOCKER)
  frames = {"industries": None}
  limitations = [] if mode == "strict_pit" else [
    "historical_price_vintage_unverified", "historical_universe_unverified",
    "industry_features_not_in_specification", "not_point_in_time_backtest",
  ]
  for name in (name for name in ("industries", "bars", "index") if name in required):
    identifier = identifiers[name]
    metadata = store.snapshot_metadata(identifier)
    provenance = metadata.get("provenance", {})
    valid = (
      metadata.get("trusted") is True and metadata.get("point_in_time") is True
      and isinstance(provenance, dict)
      and all(isinstance(provenance.get(field), str) and provenance[field].strip()
              for field in ("source", "approved_by", "evidence"))
      and provenance.get("known_at_semantics") == "publication_timestamp"
    )
    if mode == "strict_pit" and not valid:
      raise ValueError(INDUSTRY_BLOCKER if name == "industries" else "verified_dataset_required")
    limitations.extend(metadata.get("blockers", []))
    expected_kind = "industry" if name == "industries" else name
    if metadata.get("kind") != expected_kind:
      raise ValueError("dataset_kind_mismatch")
    if (store.snapshots_dir / (identifier + ".parquet")).stat().st_size > max_bytes:
      raise ValueError("training_memory_budget_exceeded")
    frame = store.read_snapshot(identifier)
    if len(frame) > max_rows:
      raise ValueError("training_row_budget_exceeded")
    if frame.memory_usage(deep=True).sum() > max_bytes:
      raise ValueError("training_memory_budget_exceeded")
    if "known_at" not in frame or frame.known_at.isna().any():
      raise ValueError("known_at_required")
    known = pd.to_datetime(frame["known_at"])
    if known.isna().any():
      raise ValueError("known_at_required")
    if known.dt.tz is not None:
      known = known.dt.tz_convert("Asia/Shanghai").dt.tz_localize(None)
    frame["known_at"] = known
    if name != "industries":
      if metadata.get("adjustment") not in {"raw", "qfq"} or not metadata.get("source"):
        raise ValueError("dataset_price_basis_required")
      frame["date"] = pd.to_datetime(frame["date"])
      if frame.date.dt.tz is not None or frame.date.max() > pd.Timestamp.now().normalize():
        raise ValueError("uncompleted_training_dates")
      if mode == "strict_pit" and (known > frame.date + pd.Timedelta(hours=15)).any():
        raise ValueError("known_at_after_session")
    frames[name] = frame
  first, last = frames["bars"].date.min(), frames["bars"].date.max()
  if mode == "historical_research":
    basis = {name: store.snapshot_metadata(identifiers[name]) for name in ("bars", "index")}
    if (basis["bars"].get("source") != basis["index"].get("source")
        or basis["bars"].get("adjustment") != "qfq"
        or basis["index"].get("adjustment") != "raw"):
      raise ValueError("mixed_dataset_basis")
    for name in ("bars", "index"):
      if "source" in frames[name] and not frames[name].source.eq(basis[name]["source"]).all():
        raise ValueError("mixed_dataset_basis")
      if "adjustment" in frames[name] and not frames[name].adjustment.eq(basis[name]["adjustment"]).all():
        raise ValueError("mixed_dataset_basis")
  frames["sessions"] = _china_calendar().sessions_in_range(first, last)
  frames["ids"] = identifiers
  frames.update(mode=mode, feature_set=feature_set, limitations=sorted(set(limitations)))
  return frames


def prepare_training_data(dataset, *, max_rows=250000, max_bytes=512 * 1024 * 1024):
  bars = dataset["bars"].copy()
  sessions = dataset["sessions"]
  fields = ["open", "high", "low", "close", "volume", "amount"]
  if not {"ticker", "date", *fields} <= set(bars):
    raise ValueError("missing_bar_columns")
  numeric = bars[fields].apply(pd.to_numeric, errors="coerce")
  valid = pd.Series(np.isfinite(numeric).all(axis=1), index=bars.index)
  valid &= numeric.gt(0).all(axis=1)
  valid &= numeric.high.ge(numeric[["open", "close", "low"]].max(axis=1))
  valid &= numeric.low.le(numeric[["open", "close", "high"]].min(axis=1))
  bars[fields] = numeric
  quality = {"invalid_stock_rows": int((~valid).sum()), "excluded_feature_rows": 0,
             "missing_stock_sessions": 0, "stock_rows": len(bars),
             "sessions": len(sessions), "tickers": int(bars.ticker.nunique())}
  if dataset["mode"] == "strict_pit" and not valid.all():
    raise ValueError("invalid_bars")
  if dataset["mode"] == "historical_research":
    bars.loc[~valid, "close"] = np.nan
  labels = make_labels(bars, sessions)
  usable = bars.loc[valid] if dataset["mode"] == "historical_research" else bars
  spans = bars.groupby("ticker").date.agg(["min", "max"])
  for ticker, span in spans.iterrows():
    quality["missing_stock_sessions"] += int(
      ((sessions >= span["min"]) & (sessions <= span["max"])).sum()
      - bars.loc[bars.ticker == ticker, "date"].nunique())
  benchmark = dataset["index"]
  if benchmark.date.duplicated().any():
    raise ValueError("duplicate_index_bars")
  benchmark_close = pd.to_numeric(benchmark.set_index("date").close, errors="coerce").reindex(sessions)
  features, count = [], 0
  for position, origin in enumerate(sessions[20:], start=20):
    window = sessions[position - 20:position + 1]
    window_bars = usable.loc[usable.date.between(window[0], origin)]
    if dataset["mode"] == "historical_research":
      expected = int(((spans["min"] <= origin) & (spans["max"] >= origin)).sum())
      counts = window_bars.groupby("ticker").date.nunique()
      eligible = counts.index[counts == 21]
      values = benchmark_close.reindex(window).to_numpy(float)
      if not (np.isfinite(values).all() and (values > 0).all()):
        eligible = eligible[:0]
      quality["excluded_feature_rows"] += expected - len(eligible)
      if eligible.empty:
        continue
      selected = window_bars.loc[window_bars.ticker.isin(eligible)]
    else:
      selected = bars
    matrix = build_features(selected, benchmark, dataset["industries"], origin,
                            sessions=sessions, feature_set=dataset["feature_set"])
    matrix = matrix.reset_index()
    matrix["origin"] = origin
    count += len(matrix)
    if count > max_rows:
      raise ValueError("training_row_budget_exceeded")
    features.append(matrix)
  if not features:
    raise ValueError("insufficient_training_features")
  feature_frame = pd.concat(features, ignore_index=True)
  if feature_frame.memory_usage(deep=True).sum() + labels.memory_usage(deep=True).sum() > max_bytes:
    raise ValueError("training_memory_budget_exceeded")
  quality["feature_rows"] = len(feature_frame)
  quality["mature_label_rows"] = int(labels["return"].notna().sum())
  return feature_frame, labels, quality


@contextmanager
def training_lock(store):
  models = store.root / "models"
  models.mkdir(parents=True, exist_ok=True)
  if models.is_symlink():
    raise ValueError("unsafe_artifact_path")
  lock = models / ".training.lock"
  try:
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
  except FileExistsError as error:
    raise ValueError("training_already_running") from error
  try:
    os.close(descriptor)
    yield models
  finally:
    lock.unlink(missing_ok=True)


def _publish_artifact(models, staging, identifier):
  root = models / "artifacts"
  root.mkdir(exist_ok=True)
  if root.is_symlink():
    raise ValueError("unsafe_artifact_path")
  destination = root / identifier
  if destination.exists():
    for source in staging.iterdir():
      if not source.is_file() or _hash_file(source) != _hash_file(destination / source.name):
        raise ValueError("artifact_identity_conflict")
    return destination
  os.rename(staging, destination)
  return destination


def train(store, *, bars=None, index=None, industries=None, dataset_dir=None,
          train_end=None, validation_end=None, calibration_end=None, test_end=None,
          n_estimators=100, num_threads=2, max_rows=250000,
    max_bytes=512 * 1024 * 1024, activate=True,
    mode="strict_pit", feature_set="research_features_v1"):
  if mode == "historical_research" and activate:
    raise ValueError("historical_research_candidate_only")
  dataset = load_dataset(store, bars=bars, index=index, industries=industries,
       dataset_dir=dataset_dir, max_rows=max_rows, max_bytes=max_bytes,
       mode=mode, feature_set=feature_set)
  ends = [train_end, validation_end, calibration_end, test_end]
  if any(value is None for value in ends):
    raise ValueError("chronological_split_bounds_required")
  if not 1 <= num_threads <= 4 or not 1 <= n_estimators <= 100:
    raise ValueError("invalid_training_budget")
  if pd.Timestamp(test_end) > dataset["sessions"][-1]:
    raise ValueError("unmatured_training_labels")
  with training_lock(store) as models:
    active = models / "lightgbm"
    if activate and (active.exists() or active.is_symlink()):
      raise FileExistsError("active_reference_exists")
    from threadpoolctl import threadpool_limits

    from .gradient_boosting import GradientBoostingModel
    from .validation import probability_metrics, return_metrics

    with tempfile.TemporaryDirectory(prefix=".candidate-", dir=models) as temporary:
      stage = Path(temporary)
      feature_frame, labels, quality = prepare_training_data(dataset, max_rows=max_rows, max_bytes=max_bytes)
      feature_columns = [name for name in feature_frame if name not in {"ticker", "origin"}]
      result = {"status": "research", "artifacts": {}, "metrics": {}, "splits": {},
                "dataset_ids": dataset["ids"], "as_of": datetime.now(timezone.utc).isoformat(),
                "mode": mode, "feature_set": feature_set, "quality": quality,
                "limitations": dataset["limitations"] + [
                  "single_chronological_fold_not_validated", "daily_variance_proxy_only"]}
      trained_models = []
      for horizon in (1, 5, 20):
        frame = feature_frame.merge(labels.loc[labels.horizon == horizon], on=["ticker", "origin"],
                                    validate="one_to_one").dropna(subset=["return"])
        parts = purged_split(frame, *ends)
        if any(part.empty for part in parts.values()):
          raise ValueError("empty_purged_partition")
        trained_through = parts["calibration"].target_date.max().date().isoformat()
        vocabulary = ({"industry": sorted(parts["train"].industry.astype(str).unique())}
                if "industry" in feature_columns else {})
        schema = digest({"version": feature_set, "columns": feature_columns,
             "categorical_vocabulary": vocabulary,
                         "missing_policy": "native_nan_unknown_category"})
        metadata = {"feature_schema": schema, "trained_through": trained_through,
                    "as_of": result["as_of"], "dataset_ids": dataset["ids"],
                    "status": "research", "split_ends": dict(zip(parts, ends, strict=True)),
                    "feature_version": feature_set, "mode": mode,
                    "point_in_time": mode == "strict_pit",
                    "limitations": result["limitations"],
                    "training_up_prior": float(parts["train"].up.mean())}
        with threadpool_limits(limits=num_threads):
          model = GradientBoostingModel.train(
            parts["train"][feature_columns], parts["train"]["return"],
            parts["validation"][feature_columns], parts["validation"]["return"],
            parts["calibration"][feature_columns], parts["calibration"]["return"],
            horizon, metadata, n_estimators=n_estimators, num_threads=num_threads,
          )
          predicted, probability = model.predict(parts["test"][feature_columns])
        result["metrics"][str(horizon)] = {
          "return": return_metrics(parts["test"]["return"], predicted),
          "probability": probability_metrics(parts["test"].up, probability),
          "zero_return": return_metrics(parts["test"]["return"], [0.] * len(predicted)),
          "training_prior": probability_metrics(parts["test"].up,
            [float(parts["train"].up.mean())] * len(predicted)),
        }
        result["splits"][str(horizon)] = {
          name: {"n": len(part), "max_target": part.target_date.max().date().isoformat(),
                 "min_origin": part.origin.min().date().isoformat(),
                 "origins": int(part.origin.nunique()), "tickers": int(part.ticker.nunique())}
          for name, part in parts.items()
        }
        candidate = stage / str(horizon)
        identifier = model.save(candidate)
        loaded = GradientBoostingModel.load(candidate)
        loaded.predict(parts["test"][feature_columns].iloc[:1])
        result["artifacts"][str(horizon)] = identifier
        trained_models.append((horizon, candidate, identifier, schema))
        del model, loaded, parts, frame
        gc.collect()
      compatibility = stage / "lightgbm"
      compatibility.mkdir()
      for horizon, candidate, identifier, _ in trained_models:
        immutable = _publish_artifact(models, candidate, identifier)
        shutil.copytree(immutable, compatibility / str(horizon))
      result["report_snapshot"] = store.save_snapshot(
        pd.DataFrame([{"report": json.dumps(result, sort_keys=True)}]),
        {"kind": "training_report", "dataset_ids": dataset["ids"], "status": "research"},
      )
      current = feature_frame.loc[feature_frame.origin == feature_frame.origin.max()]
      for schema in ({row[3] for row in trained_models} if mode == "strict_pit" else set()):
        for _, row in current.iterrows():
          store.save_snapshot(pd.DataFrame([row[feature_columns].to_dict()]), {
            "kind": "features", "ticker": row.ticker, "origin": row.origin.date().isoformat(),
            "feature_schema": schema, "trusted": True, "point_in_time": True,
            "dataset_ids": dataset["ids"], "known_at": row.origin.date().isoformat() + "T15:00:00+08:00",
          })
      if activate:
        os.rename(compatibility, active)
      result["activated"] = activate
      return result


def fit_garch_snapshot(store, ticker, snapshot_id, *, activate=True, origin=None):
  import numpy as np

  from .service import research_ticker

  ticker = research_ticker(ticker)
  metadata = store.snapshot_metadata(snapshot_id)
  frame = store.read_snapshot(snapshot_id)
  if metadata.get("kind") not in {"prices", "bars"}:
    raise ValueError("dataset_kind_mismatch")
  if metadata.get("kind") == "bars":
    frame = frame.loc[frame.ticker == ticker].rename(columns={"close": "price"})
  elif metadata.get("ticker") != ticker:
    raise ValueError("dataset_snapshot_mismatch")
  frame = frame.assign(date=pd.to_datetime(frame.date)).sort_values("date")
  cutoff = pd.Timestamp(origin or datetime.now(timezone.utc).date()).normalize()
  frame = frame.loc[frame.date <= cutoff].tail(513)
  if len(frame) < 253:
    raise ValueError("insufficient_garch_history")
  sessions = _china_calendar().sessions_in_range(frame.date.iloc[0], frame.date.iloc[-1])
  if list(frame.date) != list(sessions):
    raise ValueError("non_continuous_sessions")
  prices = frame.price.to_numpy(float)
  if not np.isfinite(prices).all() or (prices <= 0).any():
    raise ValueError("invalid_garch_returns")
  with training_lock(store) as models:
    parent = models / "garch"
    parent.mkdir(exist_ok=True)
    if parent.is_symlink():
      raise ValueError("unsafe_artifact_path")
    active = parent / ticker
    if activate and (active.exists() or active.is_symlink()):
      raise FileExistsError("active_reference_exists")
    from threadpoolctl import threadpool_limits

    from .volatility import GarchModel

    with tempfile.TemporaryDirectory(prefix=".garch-candidate-", dir=models) as temporary:
      stage = Path(temporary) / "artifact"
      with threadpool_limits(limits=1):
        model = GarchModel.fit(np.diff(np.log(prices)), {
          **metadata, "ticker": ticker, "origin": frame.date.iloc[-1].date().isoformat(),
          "snapshot_id": snapshot_id, "context_length": len(frame) - 1,
          "trained_through": frame.date.iloc[-1].date().isoformat(),
          "as_of": datetime.now(timezone.utc).isoformat(), "status": "research",
        })
      identifier = model.save(stage)
      GarchModel.load(stage)
      immutable = _publish_artifact(models, stage, identifier)
      if activate:
        compatibility = Path(temporary) / "active"
        shutil.copytree(immutable, compatibility)
        os.rename(compatibility, active)
      return {"artifact_id": identifier, "snapshot_id": snapshot_id,
              "origin": model.metadata["origin"], "activated": activate}


def run_training(store, operation, *, timeout=3600, **kwargs):
  from .providers import MAX_BYTES, run_process

  if operation not in {"train", "fit-garch"} or not 1 <= timeout <= 7200:
    raise ValueError("invalid_training_budget")
  command = [sys.executable, "-m", __name__, operation, "--root", str(store.root)]
  for key, value in kwargs.items():
    if key == "activate":
      if not value:
        command.append("--candidate-only")
    elif value is not None:
      command.extend(["--" + key.replace("_", "-"), str(value)])
  payload = json.loads(run_process(command, timeout, MAX_BYTES))
  if "error" in payload:
    code = payload["error"]
    raise ValueError(code if code in TRAINING_CODES else "training_failed")
  return payload


def main(argv=None):
  from .store import ResearchStore

  parser = argparse.ArgumentParser()
  parser.add_argument("operation", choices=["train", "fit-garch"])
  parser.add_argument("--root", required=True)
  parser.add_argument("--candidate-only", action="store_true")
  parser.add_argument("--mode", choices=["strict_pit", "historical_research"], default="strict_pit")
  parser.add_argument("--feature-set", choices=["research_features_v1", "price_index_v1"],
                      default="research_features_v1")
  for name in ("bars", "index", "industries", "dataset-dir", "train-end", "validation-end",
               "calibration-end", "test-end", "ticker", "snapshot-id", "origin"):
    parser.add_argument("--" + name)
  parser.add_argument("--num-threads", type=int, default=2)
  parser.add_argument("--n-estimators", type=int, default=100)
  parser.add_argument("--max-rows", type=int, default=250000)
  parser.add_argument("--max-bytes", type=int, default=512 * 1024 * 1024)
  args = vars(parser.parse_args(argv))
  operation, root = args.pop("operation"), args.pop("root")
  args["activate"] = not args.pop("candidate_only")
  try:
    with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink):
      store = ResearchStore(root)
      if operation == "fit-garch":
        result = fit_garch_snapshot(store, args["ticker"], args["snapshot_id"],
                                    activate=args["activate"], origin=args["origin"])
      else:
        for key in ("ticker", "snapshot_id", "origin"):
          args.pop(key)
        result = train(store, **args)
    print(json.dumps(result, allow_nan=False))
    return 0
  except (ValueError, TypeError, KeyError, OSError, RuntimeError, ImportError) as error:
    print(json.dumps({"error": training_error(error)}))
    return 1


if __name__ == "__main__":
  raise SystemExit(main())
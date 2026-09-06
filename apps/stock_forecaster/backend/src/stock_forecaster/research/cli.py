import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

import numpy as np

from ..a_share import RoutedMarketDataProvider
from ..config import Settings
from ..errors import AppError
from ..market_data import MarketDataService
from .contracts import PredictionBundle
from .runtime import HttpTimesFMAdapter, safe_code
from .service import PredictionService, research_ticker
from .store import ResearchStore
from .worker import ResearchWorker


def replay(store, job_id):
  job = store.get_job(job_id)
  if job is None or job["kind"] != "prediction" or job["result"] is None:
    raise AppError("prediction_not_found", "No completed prediction to replay.", 404)
  bundle = PredictionBundle.model_validate(job["result"])
  frame = store.read_snapshot(bundle.snapshot_id)
  metadata = store.snapshot_metadata(bundle.snapshot_id)
  if metadata["ticker"] != bundle.ticker or metadata["origin"] != bundle.origin.isoformat():
    raise AppError("snapshot_mismatch", "Stored input and prediction differ.", 409)
  prices = frame["price"].to_numpy(dtype=float)
  if not np.array_equal(prices, [row.price for row in bundle.history]):
    raise AppError("snapshot_mismatch", "Stored history differs from its snapshot.", 409)
  cumulative = np.array([row.cumulative_return for row in bundle.path])
  steps = np.diff(np.log1p(np.r_[0.0, cumulative]))
  return {
    "job_id": job_id, "ticker": bundle.ticker, "origin": bundle.origin.isoformat(),
    "snapshot_id": bundle.snapshot_id, "replay_mode": "stored_outputs_no_inference",
    "history_count": len(prices),
    "observed_return": float(prices[-1] / prices[0] - 1),
    "timesfm_returns": {
      str(horizon): float(np.expm1(steps[:horizon].sum())) if len(steps) >= horizon else None
      for horizon in (1, 5, 20)
    },
    "warnings": bundle.warnings,
  }


def main(argv=None):
  parser = argparse.ArgumentParser(prog="stock-forecaster-research")
  parser.add_argument("--root", type=Path, help="Research store (otherwise configured data directory)")
  commands = parser.add_subparsers(dest="command", required=True)
  worker_parser = commands.add_parser("worker", help="Poll prediction and background fit jobs")
  worker_parser.add_argument("--schedule", action="store_true", help="Enable singleton daily scheduler and startup catch-up")
  worker_parser.add_argument("--training-config", type=Path, help="Approved dataset IDs and chronological split options in JSON")
  for name in ("probe", "bootstrap", "daily"):
    command = commands.add_parser(name)
    command.add_argument("--limit", type=int, default=2 if name == "probe" else None)
    command.add_argument("--index-limit", type=int, default=2 if name == "probe" else 6)
    command.add_argument("--rate-seconds", type=float, default=.25)
    if name != "daily":
      command.add_argument("--provider", choices=["akshare", "tushare", "baostock"], default="akshare")
    if name != "probe":
      command.add_argument("--batch-size", type=int, default=100)
    if name != "daily":
      command.add_argument("--start")
      command.add_argument("--end")
    else:
      command.add_argument("--no-fit", action="store_true", help="Do not enqueue missing GARCH fits")
      command.add_argument("--enqueue-only", action="store_true", help="Leave jobs to a separately running worker")
  worker_parser.add_argument("--limit", type=int)
  worker_parser.add_argument("--index-limit", type=int, default=6)
  worker_parser.add_argument("--batch-size", type=int, default=100)
  worker_parser.add_argument("--rate-seconds", type=float, default=.25)
  worker_parser.add_argument("--retry-seconds", type=int, default=300,
                             help="Scheduler failure backoff (1..3600 seconds, at most 3 attempts per session/scope)")
  worker_parser.add_argument("--catch-up-limit", type=int, default=100,
                             help="Maximum startup stock collections when a saved universe exists; empty stores need --limit")
  score_parser = commands.add_parser("score", help="Evaluate matured frozen predictions using persisted bars only")
  score_parser.add_argument("--as-of")
  access = commands.add_parser("tushare-access", help="Check existing Tushare API permissions without buying access")
  access.add_argument("--end")
  source = commands.add_parser("source-fetch", help="Persist one explicitly chosen data-source response")
  source.add_argument("operation", choices=["universe", "bars", "industry", "securities", "calendar", "factors"])
  source.add_argument("--provider", choices=["akshare", "tushare", "baostock"], required=True)
  for name in ("ticker", "start", "end"):
    source.add_argument("--" + name)
  source.add_argument("--adjustment", choices=["raw", "qfq"], default="raw")
  source.add_argument("--status", choices=["L", "D", "P"], default="L")
  train_parser = commands.add_parser("train", help="Offline verified-dataset training; never trust free history implicitly")
  for name in ("bars", "index", "industries", "dataset-dir", "train-end", "validation-end", "calibration-end", "test-end"):
    train_parser.add_argument("--" + name)
  train_parser.add_argument("--num-threads", type=int, default=2)
  train_parser.add_argument("--n-estimators", type=int, default=100)
  train_parser.add_argument("--max-rows", type=int, default=250000)
  train_parser.add_argument("--max-bytes", type=int, default=512 * 1024 * 1024)
  train_parser.add_argument("--timeout", type=int, default=3600)
  train_parser.add_argument("--candidate-only", action="store_true")
  train_parser.add_argument("--dataset", help="Immutable prepared dataset snapshot ID")
  train_parser.add_argument("--mode", choices=["strict_pit", "historical_research"], default="strict_pit")
  train_parser.add_argument("--feature-set", choices=["research_features_v1", "price_index_v1"],
                            default="research_features_v1")
  prepare = commands.add_parser("prepare-dataset", help="Assemble one collection into a candidate research dataset")
  prepare.add_argument("--collection-report", required=True)
  prepare.add_argument("--benchmark", default="000300.SS")
  prepare.add_argument("--max-rows", type=int, default=250000)
  prepare.add_argument("--max-bytes", type=int, default=512 * 1024 * 1024)
  readiness = commands.add_parser("readiness", help="Assess coverage and purged label counts without training")
  readiness.add_argument("--dataset", required=True)
  for name in ("train-end", "validation-end", "calibration-end", "test-end"):
    readiness.add_argument("--" + name)
  readiness.add_argument("--max-rows", type=int, default=250000)
  readiness.add_argument("--max-bytes", type=int, default=512 * 1024 * 1024)
  commands.add_parser("run-once", help="Recover stale leases and execute at most one job")
  fit = commands.add_parser("fit-garch", help="Explicitly fit a new GARCH artifact; existing artifacts are refused")
  fit.add_argument("ticker")
  backup = commands.add_parser("backup", help="Back up jobs, frozen snapshots and verified native models; excludes secrets")
  backup.add_argument("destination")
  replay_parser = commands.add_parser("replay", help="Recalculate summary from stored inputs and outputs, offline")
  replay_parser.add_argument("job_id")
  retry = commands.add_parser("retry", help="Retry a failed/cancelled job with a new explicit identity")
  retry.add_argument("job_id")
  arguments = parser.parse_args(argv)
  try:
    settings = Settings()
    if arguments.command == "fit-garch":
      arguments.ticker = research_ticker(arguments.ticker)
    store = ResearchStore(arguments.root or settings.research_data_dir)
    if arguments.command in {"probe", "bootstrap", "score"}:
      from .pipeline import ResearchPipeline
      from .providers import ResearchProvider

      runner = ResearchPipeline(store, ResearchProvider(provider=getattr(arguments, "provider", "akshare")),
                                rate_seconds=getattr(arguments, "rate_seconds", .25))
      options = {key: value for key, value in vars(arguments).items()
                 if key not in {"command", "root", "rate_seconds", "provider"}}
      result = getattr(runner, arguments.command)(**options)
    elif arguments.command == "tushare-access":
      from .tushare import probe_access

      result = probe_access(store, end=arguments.end)
    elif arguments.command == "source-fetch":
      from .pipeline import ResearchPipeline
      from .providers import ResearchProvider

      runner = ResearchPipeline(store, ResearchProvider(provider=arguments.provider), rate_seconds=1.3)
      options = {key: value for key, value in vars(arguments).items()
                 if key not in {"command", "root", "provider", "operation"} and value is not None}
      fetched = runner._fetch(arguments.operation, **options)
      identifier = store.save_snapshot(fetched.frame, fetched.metadata)
      result = {"snapshot_id": identifier, "provider": arguments.provider, "operation": arguments.operation,
                "rows": len(fetched.frame), "raw_snapshot_id": fetched.metadata.get("raw_snapshot_id"),
                "point_in_time": False, "blockers": fetched.metadata.get("blockers", [])}
    elif arguments.command in {"prepare-dataset", "readiness"}:
      from .datasets import assemble_dataset, assess_dataset, dataset_options

      options = {key: value for key, value in vars(arguments).items()
                 if key not in {"command", "root", "dataset"}}
      if arguments.command == "prepare-dataset":
        result = assemble_dataset(store, **options)
      else:
        result = assess_dataset(store, **dataset_options(store, arguments.dataset), **options)
    elif arguments.command == "train":
      from .training import run_training

      options = {key: value for key, value in vars(arguments).items()
                 if key not in {"command", "root", "candidate_only", "dataset"}}
      if arguments.dataset:
        from .datasets import dataset_options

        if any(options.get(name) is not None for name in ("bars", "index", "industries", "dataset_dir")):
          raise ValueError("invalid_dataset_manifest")
        options.update(dataset_options(store, arguments.dataset))
      result = run_training(store, "train", **options, activate=not arguments.candidate_only)
    elif arguments.command == "backup":
      result = {"path": str(store.backup(arguments.destination)), "scope": "jobs_snapshots_and_native_models"}
    elif arguments.command == "replay":
      result = replay(store, arguments.job_id)
    else:
      from .pipeline import PersistedMarketProvider

      market = MarketDataService(
        PersistedMarketProvider(store, fallback=None if arguments.command == "daily" else RoutedMarketDataProvider()),
        0, settings.cache_max_entries,
      )
      service = PredictionService(store, market, HttpTimesFMAdapter(settings), settings)
      if arguments.command == "retry":
        job = service.get_job(arguments.job_id)
        if job["status"] not in {"failed", "cancelled"}:
          raise AppError("job_not_retryable", "Only failed or cancelled jobs can be retried.", 409)
        payload = store.get_job(arguments.job_id)["payload"]
        result = service.submit(payload["ticker"], payload.get("horizon", 5), "retry:" + uuid4().hex)
      elif arguments.command == "fit-garch":
        from .training import run_training

        directory = service.runtime.directory("garch", arguments.ticker)
        if (directory / "manifest.json").exists():
          raise AppError("artifact_exists", "Existing GARCH artifact will not be overwritten.", 409)
        snapshot_id, _, metadata, returns = service.freeze(arguments.ticker)
        if len(returns) < 252:
          raise AppError("insufficient_garch_history", "GARCH requires 252 complete returns.", 422)
        result = run_training(store, "fit-garch", ticker=arguments.ticker,
                              snapshot_id=snapshot_id, origin=metadata["origin"])
      else:
        worker = ResearchWorker(store, service, poll_seconds=settings.worker_poll_seconds)
        if arguments.command == "daily":
          from .pipeline import ResearchPipeline

          def process_batch():
            if not arguments.enqueue_only:
              for _ in range(2 * arguments.batch_size):
                if not worker.run_once():
                  break

          result = ResearchPipeline(store, rate_seconds=arguments.rate_seconds).daily(
            service, limit=arguments.limit, index_limit=arguments.index_limit,
            batch_size=arguments.batch_size, fit_missing=not arguments.no_fit,
            on_batch=process_batch,
          )
        elif arguments.command == "worker":
          scheduler = None
          if arguments.schedule:
            from .pipeline import ResearchPipeline
            from .scheduler import ResearchScheduler

            config = None
            if arguments.training_config:
              if arguments.training_config.stat().st_size > 65536:
                raise ValueError("training_config_too_large")
              config = json.loads(arguments.training_config.read_text(encoding="utf-8"))
              if not isinstance(config, dict):
                raise ValueError("invalid_training_config")
            scheduler = ResearchScheduler(store,
              ResearchPipeline(store, rate_seconds=arguments.rate_seconds), service,
              training_config=config, limit=arguments.limit, index_limit=arguments.index_limit,
              batch_size=arguments.batch_size, retry_seconds=arguments.retry_seconds,
              catch_up_limit=arguments.catch_up_limit)
            scheduler.start()
          try:
            worker.run_forever()
          finally:
            if scheduler is not None:
              scheduler.stop()
          return 0
        else:
          result = {"processed": worker.run_once()}
    print(json.dumps(result, allow_nan=False))
    return 0
  except KeyboardInterrupt:
    return 0
  except Exception as error:
    from .providers import ERROR_CODES
    from .training import TRAINING_CODES

    allowed = ERROR_CODES | TRAINING_CODES | {
      "scheduler_already_running", "invalid_universe_limit", "invalid_batch_size",
      "invalid_date_range", "invalid_rate_budget", "invalid_training_config",
      "invalid_scheduler_budget",
    }
    candidate = str(error).split(":", 1)[0]
    code = candidate if candidate in allowed else safe_code(error, "research_command_failed")
    message = ("Historical industry publication times have not been verified; provide approved PIT snapshot IDs."
               if code == "historical_industry_publication_unverified" else "Research command failed: " + code)
    print(json.dumps({"error": {"code": code, "message": message}}), file=sys.stderr)
    return 1


if __name__ == "__main__":
  raise SystemExit(main())
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import pandas as pd

from .pipeline import OPERATION_ERRORS, error_code, snapshots
from .runtime import digest

logger = logging.getLogger(__name__)


class ResearchScheduler:
  def __init__(self, store, pipeline, service, *, training_config=None, trainer=None,
               limit=None, index_limit=6, batch_size=100, retry_seconds=300,
               catch_up_limit=100):
    from apscheduler.executors.pool import ThreadPoolExecutor
    from apscheduler.schedulers.background import BackgroundScheduler

    self.store, self.pipeline, self.service = store, pipeline, service
    self.training_config = training_config
    self.trainer = trainer
    self.options = {"limit": limit, "index_limit": index_limit, "batch_size": batch_size}
    if not 1 <= retry_seconds <= 3600 or not 1 <= catch_up_limit <= 10000:
      raise ValueError("invalid_scheduler_budget")
    self.retry_seconds, self.catch_up_limit = retry_seconds, catch_up_limit
    self.clock = lambda: datetime.now(timezone.utc)
    self.lease = None
    self.scheduler = BackgroundScheduler(
      timezone="Asia/Shanghai", executors={"default": ThreadPoolExecutor(1)},
      job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600},
    )

  def start(self, *, paused=False, catch_up=True):
    if self.lease is not None:
      raise ValueError("scheduler_already_running")
    lease = (self.store.root / ".scheduler.lock").open("a+b")
    if lease.tell() == 0:
      lease.write(b"0")
      lease.flush()
    lease.seek(0)
    try:
      if os.name == "nt":
        import msvcrt

        msvcrt.locking(lease.fileno(), msvcrt.LK_NBLCK, 1)
      else:
        import fcntl

        fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
      lease.close()
      raise ValueError("scheduler_already_running") from error
    self.lease = lease
    try:
      self.scheduler.add_job(self.daily, "cron", hour=18, minute=0, id="research-daily")
      if self.training_config:
        self.scheduler.add_job(self.weekly, "cron", day_of_week="sat", hour=19,
                               minute=0, id="research-weekly")
      if catch_up:
        self.scheduler.add_job(self.catch_up, "date", id="research-catch-up")
      self.scheduler.start(paused=paused)
    except Exception:
      self.stop()
      raise

  def stop(self):
    if self.scheduler.running:
      self.scheduler.shutdown(wait=True)
    if self.lease is not None:
      self.lease.close()
      self.lease = None

  def daily(self):
    return self._invoke("daily", lambda: self.pipeline.daily(self.service, **self.options))

  def catch_up(self):
    return self._invoke("catch_up", self._catch_up)

  def _catch_up(self):
    cutoff = self.service.session_date().isoformat()
    options = dict(self.options)
    if options["limit"] is None:
      universe = next(snapshots(self.store, "universe"), None)
      if universe is None:
        return {"status": "not_configured", "reason": "catch_up_universe_required",
                "evaluation": self.pipeline.score(as_of=cutoff)}
      count = len(self.store.read_snapshot(universe[0]).ticker.unique())
      options["limit"] = min(max(1, count), self.catch_up_limit)
    selection = {key: options[key] for key in ("limit", "index_limit")}
    latest = next((metadata for _, metadata in snapshots(self.store, "collection_report")
                   if metadata.get("mode") == "daily" and metadata.get("selection") == selection), None)
    if (latest is None or latest.get("as_of", "") < cutoff
        or latest.get("collection_success") is not True):
      return self.pipeline.daily(self.service, **options,
                                 submit_predictions=False, fit_missing=False)
    return self.pipeline.score(as_of=cutoff)

  def weekly(self):
    return self._invoke("weekly", self._weekly)

  def _weekly(self):
    if not self.training_config:
      return {"status": "not_configured"}
    from .pipeline import latest_bars
    from .runtime import digest
    from .training import run_training

    trainer = self.trainer or (lambda **kwargs: run_training(self.store, "train", **kwargs))
    result = trainer(**{**self.training_config, "activate": False}) or {}
    universe = next(snapshots(self.store, "universe"), None)
    if universe is not None:
      tickers = self.store.read_snapshot(universe[0]).ticker.tolist()[:self.options["limit"]]
      for ticker in tickers:
        found = latest_bars(self.store, ticker, "qfq")
        if found is not None:
          payload = {"ticker": ticker, "snapshot_id": found[0], "activate": False,
                     "week": self.service.session_date().isoformat()}
          self.store.submit_job("fit_garch", payload, digest(payload), priority=0)
    return result

  def _retry(self, operation, at):
    if self.scheduler.running:
      group = "weekly" if operation == "weekly" else "collection"
      self.scheduler.add_job(getattr(self, operation), "date", run_date=at,
                             id="research-retry-" + group, replace_existing=True)

  def _invoke(self, operation, action):
    now = self.clock()
    group = "weekly" if operation == "weekly" else "collection"
    scope = digest({"options": self.options, "training": self.training_config,
                    "catch_up_limit": self.catch_up_limit})
    attempt, cutoff, code = 1, None, None
    try:
      cutoff = self.service.session_date().isoformat()
      latest = next((metadata for _, metadata in snapshots(self.store, "scheduler_event")
                     if metadata.get("group") == group and metadata.get("scope") == scope
                     and metadata.get("as_of") == cutoff), None)
      if latest and latest.get("status") in {"failed", "partial"}:
        attempt = latest["attempt"] + 1
        if attempt > 3:
          return {"status": "failed", "error": "retry_exhausted", "attempts": 3}
        retry_at = datetime.fromisoformat(latest["retry_after"])
        if now < retry_at:
          self._retry(operation, retry_at)
          return {"status": "deferred", "retry_after": latest["retry_after"]}
      result = action() or {}
      status = result.get("status", "succeeded")
      if result.get("collection", {}).get("success") is False or result.get("errors"):
        status = "partial"
      if result.get("error"):
        status = "failed"
    except OPERATION_ERRORS as error:
      code = error_code(error)
      logger.error("research_%s_failed code=%s", operation, code)
      result = {"status": "failed", "error": "research_" + operation + "_failed"}
      status = "failed"
    event = {"operation": operation, "group": group, "scope": scope, "as_of": cutoff,
             "status": status, "attempt": attempt, "observed_at": now.isoformat(),
             "error_code": code, "report_snapshot": result.get("report_snapshot")}
    if status in {"failed", "partial"}:
      retry_at = now + timedelta(seconds=min(3600, self.retry_seconds * 2 ** (attempt - 1)))
      event["retry_after"] = retry_at.isoformat()
    identifier = self.store.save_snapshot(
      pd.DataFrame([{"report": json.dumps(event, sort_keys=True)}]),
      {"kind": "scheduler_event", **event},
    )
    if status in {"failed", "partial"} and attempt < 3:
      self._retry(operation, retry_at)
    return {**result, "scheduler_event": identifier}
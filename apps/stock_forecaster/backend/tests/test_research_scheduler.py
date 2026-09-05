import importlib
import threading
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from stock_forecaster.research.store import ResearchStore


def scheduler():
  return importlib.import_module("stock_forecaster.research.scheduler")


class Pipeline:
  def __init__(self):
    self.daily_calls = []
    self.scored = 0

  def daily(self, service, **kwargs):
    self.daily_calls.append(kwargs)
    return {"as_of": "2026-09-04"}

  def score(self, **kwargs):
    self.scored += 1
    return {}


def test_singleton_cron_timezone_and_no_unconfigured_weekly_training(tmp_path):
  store, pipeline = ResearchStore(tmp_path), Pipeline()
  service = SimpleNamespace(session_date=lambda: date(2026, 9, 4))
  first = scheduler().ResearchScheduler(store, pipeline, service)
  first.start(paused=True, catch_up=False)
  try:
    jobs = {job.id: job for job in first.scheduler.get_jobs()}
    assert set(jobs) == {"research-daily"}
    assert str(first.scheduler.timezone) == "Asia/Shanghai"
    assert "hour='18'" in str(jobs["research-daily"].trigger)
    second = scheduler().ResearchScheduler(store, pipeline, service)
    with pytest.raises(ValueError, match="scheduler_already_running"):
      second.start(paused=True, catch_up=False)
  finally:
    first.stop()
  second.start(paused=True, catch_up=False)
  second.stop()


def test_startup_catchup_refreshes_data_and_scores_without_backdated_predictions(tmp_path):
  store, pipeline = ResearchStore(tmp_path), Pipeline()
  service = SimpleNamespace(session_date=lambda: date(2026, 9, 4))
  runner = scheduler().ResearchScheduler(store, pipeline, service, limit=2)
  runner.catch_up()
  assert pipeline.daily_calls[0]["submit_predictions"] is False
  assert pipeline.daily_calls[0]["fit_missing"] is False


def test_scheduled_collection_does_not_block_worker_thread(tmp_path):
  store, pipeline = ResearchStore(tmp_path), Pipeline()
  service = SimpleNamespace(session_date=lambda: date(2026, 9, 4))
  started, release = threading.Event(), threading.Event()

  def block(*args, **kwargs):
    started.set()
    release.wait(5)

  pipeline.daily = block
  runner = scheduler().ResearchScheduler(store, pipeline, service)
  runner.start(catch_up=False)
  try:
    runner.scheduler.add_job(runner.daily, "date", id="test-background")
    assert started.wait(5)
    identifier = store.submit_job("prediction", {"ticker": "600519.SS"}, "interactive", priority=10)
    assert store.claim_job()["id"] == identifier
  finally:
    release.set()
    runner.stop()


def test_weekly_requires_explicit_dataset_and_stages_candidate_only(tmp_path):
  store, pipeline = ResearchStore(tmp_path), Pipeline()
  service = SimpleNamespace(session_date=lambda: date(2026, 9, 4))
  config = {"bars": "a" * 64, "index": "b" * 64, "industries": "c" * 64,
            "train_end": "2024-01-01", "validation_end": "2024-04-01",
            "calibration_end": "2024-07-01", "test_end": "2024-10-01"}
  calls = []
  runner = scheduler().ResearchScheduler(store, pipeline, service, training_config=config,
                                         trainer=lambda **kwargs: calls.append(kwargs))
  runner.start(paused=True, catch_up=False)
  try:
    jobs = {job.id: job for job in runner.scheduler.get_jobs()}
    assert "day_of_week='sat'" in str(jobs["research-weekly"].trigger)
    runner.weekly()
    assert calls[0]["activate"] is False
    assert calls[0]["bars"] == "a" * 64
  finally:
    runner.stop()


def test_empty_store_catchup_never_downloads_full_universe_implicitly(tmp_path):
  store, pipeline = ResearchStore(tmp_path), Pipeline()
  service = SimpleNamespace(session_date=lambda: date(2026, 9, 4))
  runner = scheduler().ResearchScheduler(store, pipeline, service)
  result = runner.catch_up()
  assert pipeline.daily_calls == []
  assert pipeline.scored == 1
  assert result["status"] == "not_configured"


@pytest.mark.parametrize("operation", ["daily", "catch_up", "weekly"])
def test_scheduler_failure_is_durable_sanitized_and_restart_backoff(tmp_path, operation):
  from stock_forecaster.errors import AppError
  from stock_forecaster.research.pipeline import snapshots

  store, pipeline = ResearchStore(tmp_path), Pipeline()
  service = SimpleNamespace(session_date=lambda: date(2026, 9, 4))
  attempts = []

  def fail(*args, **kwargs):
    attempts.append(True)
    raise AppError("market_data_unavailable", "token=secret", 502)

  pipeline.daily = fail
  pipeline.score = fail
  options = {"limit": 1, "training_config": {"bars": "approved"}, "trainer": fail}
  runner = scheduler().ResearchScheduler(store, pipeline, service, **options)
  now = datetime(2026, 9, 5, 10, tzinfo=timezone.utc)
  runner.clock = lambda: now
  first = getattr(runner, operation)()
  assert first["status"] == "failed"
  events = list(snapshots(store, "scheduler_event"))
  assert len(events) == 1
  assert "secret" not in str(store.read_snapshot(events[0][0]).to_dict())
  restarted = scheduler().ResearchScheduler(store, pipeline, service, **options)
  restarted.clock = lambda: now + timedelta(seconds=10)
  assert getattr(restarted, operation)()["status"] == "deferred"
  assert len(attempts) == 1
  restarted.clock = lambda: now + timedelta(hours=1)
  assert getattr(restarted, operation)()["status"] == "failed"
  assert len(attempts) == 2


def test_failed_collection_report_is_not_a_catchup_success_watermark(tmp_path):
  import pandas as pd

  store, pipeline = ResearchStore(tmp_path), Pipeline()
  service = SimpleNamespace(session_date=lambda: date(2026, 9, 4))
  store.save_snapshot(pd.DataFrame({"report": ["{}"]}), {
    "kind": "collection_report", "mode": "daily", "as_of": "2026-09-04",
    "collection_success": False, "selection": {"limit": 1, "index_limit": 6},
  })
  runner = scheduler().ResearchScheduler(store, pipeline, service, limit=1)
  runner.catch_up()
  assert len(pipeline.daily_calls) == 1
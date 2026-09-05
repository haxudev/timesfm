import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from test_research_service import RecordedProvider, make_service

from stock_forecaster.errors import AppError
from stock_forecaster.research.store import ResearchStore


def test_worker_durable_success_failure_then_new_job(tmp_path):
  from stock_forecaster.research.worker import ResearchWorker

  service, store, provider, _ = make_service(tmp_path)
  worker = ResearchWorker(store, service, poll_seconds=0.1)
  first = service.submit("600519", 5)["job_id"]
  assert worker.run_once()
  assert ResearchStore(tmp_path).get_job(first)["status"] == "partial"
  assert worker.run_once() is False

  def unavailable(*args, **kwargs):
    raise AppError("market_data_unavailable", "password=do-not-persist", 502)

  provider.fetch = unavailable
  failed = service.submit("000002", 5)["job_id"]
  assert worker.run_once()
  result = store.get_job(failed)
  assert result["status"] == "failed"
  assert result["error"]["code"] == "market_data_unavailable"
  assert "password" not in json.dumps(result)
  provider.fetch = RecordedProvider().fetch
  later = service.submit("000001", 5)["job_id"]
  assert worker.run_once()
  assert store.get_job(later)["status"] == "partial"


def test_daemon_waits_and_sees_jobs_submitted_on_later_iteration(tmp_path):
  from stock_forecaster.research.worker import ResearchWorker

  service, store, _, _ = make_service(tmp_path)
  worker = ResearchWorker(store, service, poll_seconds=0.1)

  class StopAfterTwoWaits:
    count = 0
    job_id = None

    def is_set(self):
      return self.count >= 2

    def wait(self, interval):
      assert interval == 0.1
      self.count += 1
      if self.count == 1:
        self.job_id = service.submit("600519", 5)["job_id"]
      return self.is_set()

  stop = StopAfterTwoWaits()
  worker.run_forever(stop)
  assert store.get_job(stop.job_id)["status"] == "partial"


def test_heartbeat_runs_only_while_job_executes_and_fences_lost_lease(tmp_path):
  from stock_forecaster.research.worker import ResearchWorker

  service, store, _, model = make_service(tmp_path)
  job_id = service.submit("600519", 5)["job_id"]
  started, release = threading.Event(), threading.Event()
  original_predict = model.predict

  def blocking(context, horizon):
    started.set()
    assert release.wait(5)
    return original_predict(context, horizon)

  model.predict = blocking
  worker = ResearchWorker(store, service, heartbeat_seconds=0.05)
  observed = threading.Event()
  heartbeat = store.heartbeat

  def observed_heartbeat(identifier, lease_seconds=300):
    result = heartbeat(identifier, lease_seconds)
    observed.set()
    return result

  store.heartbeat = observed_heartbeat
  thread = threading.Thread(target=worker.run_once)
  thread.start()
  try:
    assert started.wait(5)
    assert observed.wait(5)
    assert store.get_job(job_id)["status"] == "running"
    assert store.recover_jobs(0) == 1
  finally:
    release.set()
    thread.join(5)
  assert not thread.is_alive()
  assert store.get_job(job_id)["error"]["code"] == "lease_expired"
  assert store.latest_result("600519.SS") is None
  assert not any(item.name == "research-heartbeat-" + job_id for item in threading.enumerate())


def test_recovers_stale_job_before_claiming_new_identity(tmp_path):
  from stock_forecaster.research.worker import ResearchWorker

  service, store, _, _ = make_service(tmp_path)
  stale = service.submit("600519", 5)["job_id"]
  store.claim_job()
  old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
  with store._connect(write=True) as db:
    db.execute("UPDATE jobs SET heartbeat_at=? WHERE id=?", [old, stale])
  fresh = service.submit("600519", 5, "new-explicit-attempt")["job_id"]
  assert ResearchWorker(store, service).run_once()
  assert store.get_job(stale)["error"]["code"] == "lease_expired"
  assert store.get_job(fresh)["status"] == "partial"


def test_cli_replay_backup_retry_are_offline(tmp_path, monkeypatch, capsys):
  from stock_forecaster.research import cli
  from stock_forecaster.research.runtime import HttpTimesFMAdapter
  from stock_forecaster.research.worker import ResearchWorker

  root = tmp_path / "live"
  monkeypatch.setenv("STOCK_FORECASTER_RESEARCH_DATA_DIR", str(root))
  monkeypatch.setenv("STOCK_FORECASTER_DEVICE", "cpu")
  service, store, _, _ = make_service(root)
  job_id = service.submit("600519", 5)["job_id"]
  ResearchWorker(store, service).run_once()

  def offline(*args, **kwargs):
    raise AssertionError("offline command must not fetch or infer")

  monkeypatch.setattr(HttpTimesFMAdapter, "predict", offline)
  monkeypatch.setattr(cli.RoutedMarketDataProvider, "fetch", offline)
  assert cli.main(["replay", job_id]) == 0
  summary = json.loads(capsys.readouterr().out)
  assert summary["timesfm_returns"]["5"] == pytest.approx(np.expm1(0.05))
  assert summary["history_count"] == 513
  assert summary["replay_mode"] == "stored_outputs_no_inference"
  destination = tmp_path / "backup"
  assert cli.main(["backup", str(destination)]) == 0
  assert json.loads(capsys.readouterr().out)["scope"] == "jobs_snapshots_and_native_models"
  assert ResearchStore(destination).latest_result("600519.SS")["bundle_id"] == job_id
  assert cli.main(["retry", job_id]) == 1
  capsys.readouterr()
  failed = service.submit("000002", 5)["job_id"]
  store.claim_job()
  store.fail_job(failed, "market_data_unavailable", "Safe message")
  assert cli.main(["retry", failed]) == 0
  retry = json.loads(capsys.readouterr().out)
  assert retry["job_id"] != failed
  assert store.get_job(retry["job_id"])["status"] == "pending"


def test_cli_fit_garch_is_explicit_then_run_once_only_forecasts(tmp_path, monkeypatch, capsys):
  from test_research_garch import returns_fixture

  from stock_forecaster.research import cli
  from stock_forecaster.research.runtime import HttpTimesFMAdapter
  from stock_forecaster.research.volatility import GarchModel

  monkeypatch.setenv("STOCK_FORECASTER_RESEARCH_DATA_DIR", str(tmp_path))
  monkeypatch.setenv("STOCK_FORECASTER_DEVICE", "cpu")
  provider = RecordedProvider(581)
  provider.frame["Close"] = 100 * np.exp(np.cumsum(returns_fixture()[-581:]))
  monkeypatch.setattr(cli.RoutedMarketDataProvider, "fetch", lambda self, *args: provider.fetch(*args))
  assert cli.main(["fit-garch", "600519"]) == 0
  document = json.loads(capsys.readouterr().out)
  artifact = GarchModel.load(tmp_path / "models" / "garch" / "600519.SS")
  assert artifact.metadata["origin"] == "2025-06-30"
  assert artifact.metadata["snapshot_id"] == document["snapshot_id"]
  store = ResearchStore(tmp_path)
  assert len(store.read_snapshot(document["snapshot_id"])) == 513
  service, _, _, model = make_service(tmp_path)
  job_id = service.submit("600519", 5)["job_id"]

  def forbidden(*args, **kwargs):
    raise AssertionError("run-once must not fit")

  monkeypatch.setattr(GarchModel, "fit", forbidden)
  monkeypatch.setattr(HttpTimesFMAdapter, "predict", lambda self, *args: model.predict(*args))
  assert cli.main(["run-once"]) == 0
  capsys.readouterr()
  bundle = store.get_job(job_id)["result"]
  assert bundle["components"]["garch"]["status"] == "ready"
  assert bundle["components"]["timesfm"]["status"] == "ready"


def test_cancel_during_native_inference_stops_at_next_boundary(tmp_path):
  from stock_forecaster.research.worker import ResearchWorker

  service, store, _, model = make_service(tmp_path)
  identifier = service.submit("600519")["job_id"]
  original = model.predict
  later_models = []

  def cancelling(*args):
    assert store.cancel_job(identifier)
    assert store.get_job(identifier)["status"] == "running"
    return original(*args)

  def forbidden(*args, **kwargs):
    later_models.append(True)
    raise AssertionError("no later model after cancellation")

  model.predict = cancelling
  service.runtime.lightgbm = forbidden
  service.runtime.garch = forbidden
  ResearchWorker(store, service).run_once()
  assert later_models == []
  assert store.get_job(identifier)["status"] == "cancelled"
  assert store.get_job(identifier)["result"] is None
  assert store.latest_result("600519.SS") is None


@pytest.mark.parametrize("state", ["failed", "expired", "cancelled", "running"])
def test_fit_candidate_activation_and_descendant_are_fenced(tmp_path, monkeypatch, state):
  from stock_forecaster.research import training
  from stock_forecaster.research.volatility import GarchModel
  from stock_forecaster.research.worker import ResearchWorker

  service, store, _, _ = make_service(tmp_path)
  snapshot, _, metadata, _ = service.freeze("600519")
  parent = store.submit_job("fit_garch", {
    "ticker": "600519.SS", "snapshot_id": snapshot, "submit_prediction": True,
  }, "fit")
  candidate_only = []

  def fit(*args, **kwargs):
    candidate_only.append(kwargs["activate"] is False)
    if state == "failed":
      raise ValueError("training_failed")
    model = GarchModel(
      {"omega": 0.02, "alpha[1]": 0.1, "beta[1]": 0.85, "nu": 8},
      {**metadata, "snapshot_id": snapshot, "trained_through": metadata["origin"]},
    )
    staged = tmp_path / "models" / "artifacts" / "staged"
    identifier = model.save(staged)
    staged.rename(staged.parent / identifier)
    if state == "expired":
      with store._connect(write=True) as connection:
        connection.execute("UPDATE jobs SET heartbeat_at=? WHERE id=?", [
          (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), parent,
        ])
    if state == "cancelled":
      store.cancel_job(parent)
    return {"artifact_id": identifier, "activated": False}

  monkeypatch.setattr(training, "run_training", fit)
  ResearchWorker(store, service).run_once()
  children = [job for job in store.list_jobs() if job["kind"] == "prediction"]
  assert bool(children) == (state == "running")
  assert (tmp_path / "models" / "garch" / "600519.SS").exists() == (state == "running")
  assert candidate_only == [True]
  if state == "running":
    assert store.get_job(parent)["result"]["activated"] is True


@pytest.mark.parametrize("boundary", [
  "cancel_staged", "expire_staged", "cancel_after_publish", "commit_failure",
  "expire_before_child", "child_insert_failure", "parent_complete_failure", "cancel_during_publish",
])
def test_fit_publication_has_one_terminal_boundary(tmp_path, monkeypatch, boundary):
  from stock_forecaster.research import training
  from stock_forecaster.research.volatility import GarchModel
  from stock_forecaster.research.worker import ResearchWorker

  service, store, _, _ = make_service(tmp_path)
  snapshot, _, metadata, _ = service.freeze("600519")
  candidate = tmp_path / "models" / "artifacts" / "staged"
  artifact_id = GarchModel(
    {"omega": .02, "alpha[1]": .1, "beta[1]": .85, "nu": 8},
    {**metadata, "snapshot_id": snapshot, "trained_through": metadata["origin"]},
  ).save(candidate)
  candidate = candidate.rename(candidate.parent / artifact_id)
  target = tmp_path / "models" / "garch" / "600519.SS"
  parent = store.submit_job("fit_garch", {
    "ticker": "600519.SS", "snapshot_id": snapshot, "submit_prediction": True,
  }, "terminal-fit")
  monkeypatch.setattr(training, "run_training", lambda *args, **kwargs: {
    "artifact_id": artifact_id, "activated": False,
  })
  worker = ResearchWorker(store, service)
  connect = store._connect

  if boundary == "expire_before_child":
    from stock_forecaster.research import store as module

    revision = service.runtime.revision

    class ExpiredClock(datetime):
      @classmethod
      def now(cls, tz=None):
        return datetime.now(tz) + timedelta(hours=1)

    def expire_before_child(ticker):
      assert target.exists()
      monkeypatch.setattr(module, "datetime", ExpiredClock)
      return revision(ticker)

    monkeypatch.setattr(service.runtime, "revision", expire_before_child)
  if boundary in {"child_insert_failure", "parent_complete_failure"}:
    with connect(write=True) as connection:
      if boundary == "child_insert_failure":
        connection.execute("""
          CREATE TRIGGER reject_child BEFORE INSERT ON jobs WHEN NEW.kind='prediction'
          BEGIN SELECT RAISE(ABORT, 'injected child failure'); END
        """)
      else:
        connection.execute("""
          CREATE TRIGGER reject_completion BEFORE UPDATE OF status ON jobs
          WHEN NEW.kind='fit_garch' AND NEW.status='succeeded'
          BEGIN SELECT RAISE(ABORT, 'injected completion failure'); END
        """)
  if boundary == "cancel_during_publish":
    revision = service.runtime.revision
    cancelled = []
    cancel_started = threading.Event()

    def cancel():
      cancel_started.set()
      cancelled.append(store.cancel_job(parent))

    cancel_thread = threading.Thread(target=cancel)

    def cancel_during_revision(ticker):
      assert target.exists()
      if not cancel_started.is_set():
        cancel_thread.start()
        assert cancel_started.wait(5)
      return revision(ticker)

    monkeypatch.setattr(service.runtime, "revision", cancel_during_revision)
  if boundary in {"cancel_staged", "expire_staged"}:
    from stock_forecaster.research import worker as module

    copytree = module.shutil.copytree

    def interrupt_staging(*args, **kwargs):
      result = copytree(*args, **kwargs)
      if boundary == "cancel_staged":
        assert store.cancel_job(parent)
      else:
        with connect(write=True) as connection:
          connection.execute("UPDATE jobs SET heartbeat_at=? WHERE id=?", [
            (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), parent,
          ])
      return result

    monkeypatch.setattr(module.shutil, "copytree", interrupt_staging)
  if boundary == "cancel_after_publish":
    activate = worker._activate_garch
    cancelled = []

    def cancel_after_publish(*args):
      result = activate(*args)
      cancelled.append(store.cancel_job(parent))
      return result

    monkeypatch.setattr(worker, "_activate_garch", cancel_after_publish)
  if boundary == "commit_failure":
    @contextmanager
    def reject_commit(write=False):
      with connect(write=write) as connection:
        if write:
          connection.set_authorizer(lambda action, operation, *rest: (
            sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_TRANSACTION and operation == "COMMIT" and target.exists()
            else sqlite3.SQLITE_OK
          ))
        yield connection

    monkeypatch.setattr(store, "_connect", reject_commit)

  assert worker.run_once()
  if boundary == "cancel_during_publish":
    cancel_thread.join(5)
    assert not cancel_thread.is_alive()
  saved = store.get_job(parent)
  children = [job for job in store.list_jobs() if job["kind"] == "prediction"]
  assert candidate.is_dir()
  if boundary in {"cancel_after_publish", "cancel_during_publish"}:
    assert cancelled == [False]
    assert saved["status"] == "succeeded"
    assert saved["result"]["activated"] is True
    assert len(children) == 1
    assert children[0]["parent_job_id"] == parent
    assert children[0]["id"] == saved["result"]["prediction_job_id"]
    assert children[0]["payload"]["artifact_revision"] == service.runtime.revision("600519.SS")
    assert service.submit("600519")["job_id"] == children[0]["id"]
    assert worker.run_once()
    prediction = store.get_job(children[0]["id"])
    assert prediction["status"] == "partial"
    assert prediction["result"]["components"]["garch"]["status"] == "ready"
    assert all(row["volatility"] > 0 for row in prediction["result"]["horizons"])
  else:
    assert not target.exists()
    assert children == []
    assert saved["status"] == ("cancelled" if boundary == "cancel_staged" else "failed")
    assert saved["result"] is None
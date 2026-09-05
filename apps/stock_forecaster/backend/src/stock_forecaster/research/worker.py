import logging
import math
import os
import shutil
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

from ..errors import AppError
from .runtime import safe_code

logger = logging.getLogger(__name__)


class ResearchWorker:
  def __init__(
    self, store, service, poll_seconds=2.0, heartbeat_seconds=10.0,
    lease_seconds=300.0,
  ):
    if (
      not math.isfinite(poll_seconds) or not 0.1 <= poll_seconds <= 60
      or not math.isfinite(heartbeat_seconds) or not 0.05 <= heartbeat_seconds <= 30
      or not math.isfinite(lease_seconds) or lease_seconds < 3 * heartbeat_seconds
    ):
      raise ValueError("Invalid worker timing bounds")
    self.store = store
    self.service = service
    self.poll_seconds = poll_seconds
    self.heartbeat_seconds = heartbeat_seconds
    self.lease_seconds = lease_seconds

  def _commit_fit(self, job, result, publish=None):
    child = None
    if job["payload"].get("submit_prediction"):
      child = lambda: self.service.prepare_submission(
        job["payload"]["ticker"], priority=job["priority"],
      )
    self.store.commit_job(
      job["id"], result, publish=publish, child=child, lease_seconds=self.lease_seconds,
    )

  def _activate_garch(self, job, result):
    from .volatility import GarchModel

    models = self.store.root / "models"
    source = models / "artifacts" / result["artifact_id"]
    if source.resolve().parent != models / "artifacts" or any(
      path.is_symlink() for path in (models, source.parent, source, *source.rglob("*"))
    ):
      raise ValueError("unsafe_artifact_path")
    loaded = GarchModel.load(source)
    if loaded.metadata.get("ticker") != job["payload"]["ticker"]:
      raise ValueError("dataset_snapshot_mismatch")
    parent = models / "garch"
    parent.mkdir(exist_ok=True)
    if parent.is_symlink():
      raise ValueError("unsafe_artifact_path")
    with tempfile.TemporaryDirectory(prefix=".activate-", dir=models) as temporary:
      staged = Path(temporary) / "artifact"
      shutil.copytree(source, staged)
      GarchModel.load(staged)

      @contextmanager
      def publish():
        target = parent / job["payload"]["ticker"]
        if target.exists() or target.is_symlink():
          raise ValueError("active_reference_exists")
        os.rename(staged, target)
        try:
          yield
        except BaseException:
          os.rename(target, staged)
          raise

      self._commit_fit(job, {**result, "activated": True}, publish=publish)

  def run_once(self):
    self.store.recover_jobs(self.lease_seconds)
    job = self.store.claim_job()
    if job is None:
      return False
    stopped, lost_lease = threading.Event(), threading.Event()

    def heartbeat():
      while not stopped.wait(self.heartbeat_seconds):
        try:
          if not self.store.heartbeat(job["id"], self.lease_seconds):
            lost_lease.set()
            return
        except Exception:
          lost_lease.set()
          return

    thread = threading.Thread(
      target=heartbeat, name="research-heartbeat-" + job["id"], daemon=True,
    )
    thread.start()
    try:
      if job["kind"] == "fit_garch":
        from .training import run_training

        payload = job["payload"]
        result = run_training(
          self.store, "fit-garch", ticker=payload["ticker"],
          snapshot_id=payload["snapshot_id"], origin=payload.get("origin"), activate=False,
        )
        if lost_lease.is_set() or not self.store.job_active(job["id"], self.lease_seconds):
          raise AppError("job_inactive", "Job cancelled or lease expired.", 409)
        if payload.get("activate", True):
          self._activate_garch(job, result)
        else:
          self._commit_fit(job, result)
      elif job["kind"] == "prediction":
        result = self.service.execute(job)
        if not lost_lease.is_set():
          self.store.complete_job(job["id"], result, lease_seconds=self.lease_seconds)
      else:
        raise AppError("unsupported_job", "This worker only executes predictions.", 422)
    except Exception as error:
      code = safe_code(error, "job_failed")
      if job["kind"] == "fit_garch":
        from .training import training_error

        code = training_error(error)
      if not lost_lease.is_set():
        try:
          self.store.fail_job(job["id"], code, "Research job failed: " + code)
        except ValueError:
          pass
      logger.warning("research_job_failed job_id=%s code=%s", job["id"], code)
    finally:
      stopped.set()
      thread.join(timeout=6)
    return True

  def run_forever(self, stop=None):
    stop = stop if stop is not None else threading.Event()
    while not stop.is_set():
      try:
        self.run_once()
      except Exception:
        logger.error("research_worker_iteration_failed")
      stop.wait(self.poll_seconds)
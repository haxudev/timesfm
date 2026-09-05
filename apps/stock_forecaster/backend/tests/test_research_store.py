import math
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pandas as pd
import pytest


def test_snapshot_is_immutable_and_survives_restart(tmp_path):
  from stock_forecaster.research.store import ResearchStore

  frame = pd.DataFrame({"date": ["2026-09-03", "2026-09-04"], "close": [10.0, 11.0]})
  metadata = {"ticker": "600519.SS", "adjustment": "qfq", "source": "fixture"}
  store = ResearchStore(tmp_path)
  original = store.save_snapshot(frame, metadata)
  assert store.save_snapshot(frame, metadata) == original
  revised = store.save_snapshot(frame.assign(close=[10.0, 12.0]), metadata)
  assert revised != original
  reopened = ResearchStore(tmp_path)
  assert reopened.read_snapshot(original)["close"].tolist() == [10.0, 11.0]
  assert reopened.read_snapshot(revised)["close"].tolist() == [10.0, 12.0]
  assert reopened.snapshot_metadata(original) == metadata


def test_snapshot_metadata_is_part_of_identity(tmp_path):
  from stock_forecaster.research.store import ResearchStore

  store = ResearchStore(tmp_path)
  frame = pd.DataFrame({"close": [10.0]})
  assert store.save_snapshot(frame, {"adjustment": "qfq"}) != store.save_snapshot(
    frame, {"adjustment": "raw"}
  )


def test_snapshot_rejects_traversal_and_detects_corruption(tmp_path):
  from stock_forecaster.research.store import ResearchStore

  store = ResearchStore(tmp_path)
  with pytest.raises(ValueError):
    store.read_snapshot("../../outside")
  snapshot = store.save_snapshot(pd.DataFrame({"close": [10.0]}), {})
  path = next((tmp_path / "snapshots").glob("*.parquet"))
  path.write_bytes(b"broken")
  with pytest.raises(ValueError, match="integrity"):
    store.read_snapshot(snapshot)


def test_job_claim_is_exclusive_and_completed_result_is_durable(tmp_path):
  from stock_forecaster.research.store import ResearchStore

  store = ResearchStore(tmp_path)
  job = store.submit_job("prediction", {"ticker": "600519.SS"}, "same-input")
  assert store.submit_job("prediction", {"ticker": "600519.SS"}, "same-input") == job
  other = ResearchStore(tmp_path)
  claimed = store.claim_job()
  assert claimed["id"] == job
  assert other.claim_job() is None
  store.complete_job(job, {"ticker": "600519.SS", "value": 0.02})
  result = other.get_job(job)
  assert result["status"] == "succeeded"
  assert result["result"]["value"] == 0.02
  with pytest.raises(ValueError):
    store.complete_job(job, {"value": 99.0})


def test_pending_job_can_be_cancelled_without_erasing_a_result(tmp_path):
  from stock_forecaster.research.store import ResearchStore

  store = ResearchStore(tmp_path)
  job = store.submit_job("prediction", {}, "pending")
  assert store.cancel_job(job)
  assert store.claim_job() is None
  assert store.get_job(job)["status"] == "cancelled"


def test_snapshot_hash_preserves_order_schema_and_numeric_precision(tmp_path):
  from stock_forecaster.research.store import ResearchStore

  store = ResearchStore(tmp_path)
  frame = pd.DataFrame({"value": [1.0, 2.0], "count": [1, 2]})
  original = store.save_snapshot(frame, {"source": "fixture", "version": 1})
  assert store.save_snapshot(frame, {"version": 1, "source": "fixture"}) == original
  variants = [
    frame.iloc[::-1],
    frame[["count", "value"]],
    frame.astype({"count": "float64"}),
    frame.assign(value=[math.nextafter(1.0, 2.0), 2.0]),
  ]
  for variant in variants:
    assert store.save_snapshot(variant, {"source": "fixture", "version": 1}) != original
  assert store.save_snapshot(frame.set_axis([10, 20]), {
    "source": "fixture", "version": 1,
  }) == original


def test_snapshot_parquet_handles_nulls_empty_frames_and_nanoseconds(tmp_path):
  import duckdb

  from stock_forecaster.research.store import ResearchStore

  store = ResearchStore(tmp_path)
  frame = pd.DataFrame({
    "time": pd.to_datetime(["2026-09-04 00:00:00.000000001", None]),
    "count": pd.Series([1, None], dtype="Int64"),
    "value": [1.2345678901234567, float("nan")],
  })
  snapshot = store.save_snapshot(frame, {})
  restored = store.read_snapshot(snapshot)
  assert restored["time"].iloc[0] == frame["time"].iloc[0]
  assert restored["value"].iloc[0] == frame["value"].iloc[0]
  assert restored.iloc[1].isna().all()
  revised = frame.copy()
  revised.loc[0, "time"] += pd.Timedelta(1, unit="ns")
  assert store.save_snapshot(revised, {}) != snapshot
  with duckdb.connect() as connection:
    assert connection.execute(
      "SELECT count(*) FROM read_parquet(?)",
      [str(tmp_path / "snapshots" / f"{snapshot}.parquet")],
    ).fetchone()[0] == 2
  empty = store.save_snapshot(frame.iloc[:0], {})
  assert store.read_snapshot(empty).empty
  assert store.read_snapshot(empty).columns.tolist() == frame.columns.tolist()


def test_concurrent_snapshot_publication_does_not_replace_published_file(tmp_path):
  from stock_forecaster.research.store import ResearchStore

  stores = [ResearchStore(tmp_path), ResearchStore(tmp_path)]
  barrier = Barrier(2)
  frame = pd.DataFrame({"value": [1.0]})

  def publish(store):
    barrier.wait()
    return store.save_snapshot(frame, {})

  with ThreadPoolExecutor(max_workers=2) as executor:
    snapshots = list(executor.map(publish, stores))
  assert snapshots[0] == snapshots[1]
  path = tmp_path / "snapshots" / f"{snapshots[0]}.parquet"
  original = path.stat()
  stores[0].save_snapshot(frame, {})
  assert path.stat().st_mtime_ns == original.st_mtime_ns
  assert path.stat().st_ino == original.st_ino
  assert len(list(path.parent.iterdir())) == 1


def test_failed_manifest_publish_is_invisible_and_retryable(tmp_path):
  from stock_forecaster.research.store import ResearchStore

  store = ResearchStore(tmp_path)
  with closing(sqlite3.connect(store.db_path)) as connection:
    connection.execute("""
      CREATE TRIGGER reject_snapshot BEFORE INSERT ON snapshots
      BEGIN SELECT RAISE(ABORT, 'injected publication failure'); END
    """)
    connection.commit()
  frame = pd.DataFrame({"value": [1.0]})
  with pytest.raises(sqlite3.IntegrityError, match="injected"):
    store.save_snapshot(frame, {})
  paths = list((tmp_path / "snapshots").glob("*.parquet"))
  assert len(paths) == 1
  with pytest.raises(KeyError):
    store.read_snapshot(paths[0].stem)
  with closing(sqlite3.connect(store.db_path)) as connection:
    connection.execute("DROP TRIGGER reject_snapshot")
    connection.commit()
  snapshot = store.save_snapshot(frame, {})
  assert store.read_snapshot(snapshot)["value"].tolist() == [1.0]


def test_missing_or_corrupt_snapshot_is_not_silently_repaired(tmp_path):
  from stock_forecaster.research.store import ResearchStore

  store = ResearchStore(tmp_path)
  frame = pd.DataFrame({"value": [1.0]})
  snapshot = store.save_snapshot(frame, {})
  path = tmp_path / "snapshots" / f"{snapshot}.parquet"
  path.write_bytes(b"corrupt")
  with pytest.raises(ValueError, match="integrity"):
    store.save_snapshot(frame, {})
  assert path.read_bytes() == b"corrupt"
  path.unlink()
  with pytest.raises(ValueError, match="integrity"):
    store.read_snapshot(snapshot)


@pytest.mark.parametrize("identifier", ["../bad", "A" * 64, "a" * 63, "a" * 64 + ".parquet"])
def test_snapshot_metadata_rejects_unsafe_ids(tmp_path, identifier):
  from stock_forecaster.research.store import ResearchStore

  with pytest.raises(ValueError):
    ResearchStore(tmp_path).snapshot_metadata(identifier)


def test_job_priority_fifo_and_concurrent_claims(tmp_path):
  from stock_forecaster.research.store import ResearchStore

  store = ResearchStore(tmp_path)
  low = store.submit_job("prediction", {}, "low", priority=-1)
  first = store.submit_job("prediction", {}, "first", priority=5)
  second = store.submit_job("prediction", {}, "second", priority=5)
  assert store.claim_job()["id"] == first
  assert store.claim_job()["id"] == second
  stores = [ResearchStore(tmp_path), ResearchStore(tmp_path)]
  barrier = Barrier(2)

  def claim(candidate):
    barrier.wait()
    return candidate.claim_job()

  with ThreadPoolExecutor(max_workers=2) as executor:
    claimed = list(executor.map(claim, stores))
  assert [job["id"] for job in claimed if job is not None] == [low]
  assert sum(job is None for job in claimed) == 1


def test_idempotency_is_kind_scoped_and_payload_conflicts_are_rejected(tmp_path):
  from stock_forecaster.research.store import ResearchStore

  store = ResearchStore(tmp_path)
  job = store.submit_job("prediction", {"ticker": "AAA", "horizon": 5}, "key")
  assert store.submit_job("prediction", {"horizon": 5, "ticker": "AAA"}, "key") == job
  assert store.submit_job("training", {"ticker": "AAA"}, "key") != job
  with pytest.raises(ValueError, match="idempotency"):
    store.submit_job("prediction", {"ticker": "BBB"}, "key")


def test_partial_result_latest_and_failures_survive_restart(tmp_path):
  from stock_forecaster.research.store import ResearchStore

  store = ResearchStore(tmp_path)
  assert store.latest_result("AAA") is None
  first = store.submit_job("prediction", {"ticker": "AAA"}, "first")
  store.claim_job()
  store.complete_job(first, {"ticker": "AAA", "value": 1})
  partial = store.submit_job("prediction", {"ticker": "AAA"}, "partial")
  store.claim_job()
  bundle = {"ticker": "AAA", "status": "partial", "value": 2}
  store.complete_job(partial, bundle)
  failed = store.submit_job("prediction", {"ticker": "AAA"}, "failed")
  store.claim_job()
  store.fail_job(failed, "data_unavailable", "No observations")
  reopened = ResearchStore(tmp_path)
  assert reopened.get_job(partial)["status"] == "partial"
  assert reopened.get_job(failed)["error"] == {
    "code": "data_unavailable", "message": "No observations",
  }
  assert reopened.latest_result("AAA") == bundle
  bundle["value"] = 99
  assert reopened.latest_result("AAA")["value"] == 2
  assert [job["id"] for job in reopened.list_jobs(limit=2)] == [failed, partial]
  assert reopened.list_jobs(limit=0) == []


def test_only_running_jobs_finish_and_terminal_states_are_immutable(tmp_path):
  from stock_forecaster.research.store import ResearchStore

  store = ResearchStore(tmp_path)
  job = store.submit_job("prediction", {}, "key")
  with pytest.raises(ValueError):
    store.complete_job(job, {})
  with pytest.raises(ValueError):
    store.fail_job(job, "error", "message")
  assert not store.heartbeat(job)
  store.claim_job()
  store.complete_job(job, {"value": 1})
  assert not store.cancel_job(job)
  assert not store.heartbeat(job)
  with pytest.raises(ValueError):
    store.fail_job(job, "error", "message")
  assert store.get_job(job)["result"] == {"value": 1}
  failed = store.submit_job("prediction", {}, "failed")
  store.claim_job()
  store.fail_job(failed, "error", "message")
  with pytest.raises(ValueError):
    store.complete_job(failed, {})


def test_heartbeat_and_recovery_expire_only_stale_running_jobs(tmp_path):
  from stock_forecaster.research.store import ResearchStore

  store = ResearchStore(tmp_path)
  stale = store.submit_job("prediction", {}, "stale")
  fresh = store.submit_job("prediction", {}, "fresh")
  store.claim_job()
  store.claim_job()
  pending = store.submit_job("prediction", {}, "pending")
  old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
  with closing(sqlite3.connect(store.db_path)) as connection:
    connection.execute("UPDATE jobs SET heartbeat_at = ? WHERE id = ?", [old, stale])
    connection.commit()
  assert store.heartbeat(fresh)
  assert store.recover_jobs(age_seconds=60) == 1
  reopened = ResearchStore(tmp_path)
  assert reopened.get_job(stale)["status"] == "failed"
  assert reopened.get_job(stale)["error"]["code"] == "lease_expired"
  assert reopened.get_job(fresh)["status"] == "running"
  assert reopened.get_job(pending)["status"] == "pending"
  assert reopened.recover_jobs(age_seconds=60) == 0
  with pytest.raises(ValueError):
    reopened.complete_job(stale, {})
  for job in reopened.list_jobs():
    for field in ("created_at", "updated_at", "started_at", "heartbeat_at", "finished_at"):
      if job[field] is not None:
        assert datetime.fromisoformat(job[field]).utcoffset() == timedelta(0)


@pytest.mark.parametrize("method", ["get_job", "cancel_job", "heartbeat"])
def test_jobs_reject_unsafe_ids(tmp_path, method):
  from stock_forecaster.research.store import ResearchStore

  with pytest.raises(ValueError):
    getattr(ResearchStore(tmp_path), method)("../../outside")


def test_backup_restores_only_referenced_files_and_durable_jobs(tmp_path):
  from stock_forecaster.research.store import ResearchStore

  store = ResearchStore(tmp_path / "live")
  snapshot = store.save_snapshot(pd.DataFrame({"close": [10.0]}), {"ticker": "AAA"})
  job = store.submit_job("prediction", {"ticker": "AAA"}, "key")
  store.claim_job()
  store.complete_job(job, {"ticker": "AAA", "snapshot_id": snapshot})
  (tmp_path / "live" / "snapshots" / ("f" * 64 + ".parquet")).write_bytes(b"orphan")
  destination = tmp_path / "backup"
  assert store.backup(destination) == destination
  restored = ResearchStore(destination)
  assert restored.read_snapshot(snapshot)["close"].tolist() == [10.0]
  assert restored.snapshot_metadata(snapshot) == {"ticker": "AAA"}
  assert restored.get_job(job)["status"] == "succeeded"
  assert restored.latest_result("AAA")["snapshot_id"] == snapshot
  assert len(list((destination / "snapshots").iterdir())) == 1
  store.submit_job("prediction", {}, "after-backup")
  assert len(restored.list_jobs()) == 1
  with pytest.raises(FileExistsError):
    store.backup(destination)


def test_backup_does_not_publish_corrupted_snapshot(tmp_path):
  from stock_forecaster.research.store import ResearchStore

  store = ResearchStore(tmp_path / "live")
  snapshot = store.save_snapshot(pd.DataFrame({"value": [1.0]}), {})
  (tmp_path / "live" / "snapshots" / f"{snapshot}.parquet").write_bytes(b"bad")
  destination = tmp_path / "backup"
  with pytest.raises(ValueError, match="integrity"):
    store.backup(destination)
  assert not destination.exists()


def test_invalid_json_and_ambiguous_columns_leave_no_published_state(tmp_path):
  from stock_forecaster.research.store import ResearchStore

  store = ResearchStore(tmp_path)
  with pytest.raises(ValueError):
    store.submit_job("prediction", {"value": float("nan")}, "bad")
  with pytest.raises(ValueError):
    store.save_snapshot(pd.DataFrame({"value": [1]}), {"value": float("inf")})
  with pytest.raises(ValueError):
    store.save_snapshot(pd.DataFrame([[1, 2]], columns=["close", "CLOSE"]), {})
  assert store.list_jobs() == []
  assert list((tmp_path / "snapshots").iterdir()) == []


def test_invalid_document_and_priority_types_are_rejected(tmp_path):
  from stock_forecaster.research.store import ResearchStore

  store = ResearchStore(tmp_path)
  with pytest.raises(TypeError):
    store.save_snapshot(pd.DataFrame({"value": [1]}), [])
  with pytest.raises(TypeError):
    store.submit_job("prediction", [], "invalid-payload")
  with pytest.raises(TypeError):
    store.submit_job("prediction", {}, "invalid-priority", priority=1.5)
  assert store.list_jobs() == []


def test_running_cancel_is_durable_request_and_completion_discards_result(tmp_path):
  from stock_forecaster.research.store import ResearchStore

  store = ResearchStore(tmp_path)
  job = store.submit_job("prediction", {"ticker": "AAA"}, "cancel-running")
  store.claim_job()
  assert store.cancel_job(job)
  reopened = ResearchStore(tmp_path)
  requested = reopened.get_job(job)
  assert requested["status"] == "running"
  assert requested["cancellation_requested"] is True
  assert requested["finished_at"] is None
  reopened.complete_job(job, {"ticker": "AAA", "value": 99})
  assert reopened.get_job(job)["status"] == "cancelled"
  assert reopened.get_job(job)["result"] is None
  assert reopened.latest_result("AAA") is None


def test_cancellation_column_migrates_existing_database(tmp_path):
  from stock_forecaster.research.store import ResearchStore

  store = ResearchStore(tmp_path)
  job = store.submit_job("prediction", {}, "existing")
  with store._connect(write=True) as connection:
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(jobs)")}
    if "cancellation_requested" in columns:
      connection.execute("ALTER TABLE jobs DROP COLUMN cancellation_requested")
    connection.execute("PRAGMA user_version=0")
  reopened = ResearchStore(tmp_path)
  assert reopened.get_job(job)["cancellation_requested"] is False
  with reopened._connect() as connection:
    assert connection.execute("PRAGMA user_version").fetchone()[0] >= 1


@pytest.mark.parametrize("state", ["expired", "cancelled", "failed", "pending", "running", "succeeded"])
def test_child_submission_is_fenced_in_store_transaction(tmp_path, state):
  from stock_forecaster.research.store import ResearchStore

  store = ResearchStore(tmp_path)
  parent = store.submit_job("fit_garch", {}, "parent")
  if state != "pending":
    store.claim_job()
  if state == "expired":
    with store._connect(write=True) as connection:
      connection.execute("UPDATE jobs SET heartbeat_at=? WHERE id=?", [
        (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), parent,
      ])
  if state == "cancelled":
    store.cancel_job(parent)
  if state == "failed":
    store.fail_job(parent, "fit_failed", "fit failed")
  if state == "succeeded":
    store.complete_job(parent, {})
  if state == "running":
    child = store.submit_job("prediction", {}, "child", parent_job_id=parent)
    assert store.get_job(child)["status"] == "pending"
  else:
    with pytest.raises(ValueError, match="parent_job_inactive"):
      store.submit_job("prediction", {}, "child", parent_job_id=parent)
    assert len(store.list_jobs()) == 1


def test_backup_restores_native_models_and_excludes_secrets(tmp_path):
  import numpy as np
  from test_research_lightgbm import fit_small, training_fixture

  from stock_forecaster.research.gradient_boosting import GradientBoostingModel
  from stock_forecaster.research.store import ResearchStore
  from stock_forecaster.research.volatility import GarchModel

  store = ResearchStore(tmp_path / "live")
  frames, targets = training_fixture()
  tree = fit_small(frames, targets)
  tree.save(store.root / "models" / "lightgbm" / "5")
  risk = GarchModel({"omega": .02, "alpha[1]": .1, "beta[1]": .85, "nu": 8}, {})
  staged = store.root / "models" / "artifacts" / "staged"
  identifier = risk.save(staged)
  staged.rename(staged.parent / identifier)
  risk.save(store.root / "models" / "garch" / "600519.SS")
  (store.root / "token").write_text("excluded-secret")
  (store.root / "models" / "lightgbm" / "5" / ".env").write_text("excluded-secret")
  backup = store.backup(tmp_path / "backup")
  loaded = GradientBoostingModel.load(backup / "models" / "lightgbm" / "5")
  np.testing.assert_allclose(loaded.predict(frames[1]), tree.predict(frames[1]))
  restored = GarchModel.load(backup / "models" / "garch" / "600519.SS")
  assert restored.parameters == risk.parameters
  assert GarchModel.load(backup / "models" / "artifacts" / identifier).parameters == risk.parameters
  assert not (backup / "token").exists()
  assert not list(backup.rglob(".env"))


@pytest.mark.parametrize("mutation", ["corrupt", "midcopy", "symlink"])
def test_backup_rejects_untrusted_or_changing_models(tmp_path, monkeypatch, mutation):
  from pathlib import Path

  from stock_forecaster.research import store as module
  from stock_forecaster.research.volatility import GarchModel

  store = module.ResearchStore(tmp_path / "live")
  directory = store.root / "models" / "garch" / "600519.SS"
  model = GarchModel({"omega": .02, "alpha[1]": .1, "beta[1]": .85, "nu": 8}, {})
  model.save(directory)
  manifest = directory / "manifest.json"
  if mutation == "corrupt":
    manifest.write_text("{}")
  if mutation == "symlink":
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == directory or original(path))
  if mutation == "midcopy":
    original = module.shutil.copyfile

    def changing(source, target, *args, **kwargs):
      result = original(source, target, *args, **kwargs)
      if Path(source) == manifest:
        manifest.write_text("{}")
      return result

    monkeypatch.setattr(module.shutil, "copyfile", changing)
  with pytest.raises(ValueError):
    store.backup(tmp_path / "backup")
  assert not (tmp_path / "backup").exists()


def test_expired_heartbeat_cannot_revive_lease_without_recovery(tmp_path):
  from stock_forecaster.research.store import ResearchStore

  store = ResearchStore(tmp_path)
  identifier = store.submit_job("prediction", {}, "lease")
  store.claim_job()
  with store._connect(write=True) as connection:
    connection.execute("UPDATE jobs SET heartbeat_at=? WHERE id=?", [
      (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), identifier,
    ])
  assert store.heartbeat(identifier) is False
  with pytest.raises(ValueError):
    store.complete_job(identifier, {"value": 1})


def test_expired_cancelled_request_recovers_as_cancelled(tmp_path):
  from stock_forecaster.research.store import ResearchStore

  store = ResearchStore(tmp_path)
  identifier = store.submit_job("prediction", {}, "cancel")
  store.claim_job()
  store.cancel_job(identifier)
  store.recover_jobs(0)
  assert store.get_job(identifier)["status"] == "cancelled"
  assert store.get_job(identifier)["error"] is None


@pytest.mark.parametrize("state", ["running", "cancelled", "failed", "expired", "partial", "succeeded"])
def test_child_claim_requires_durable_successful_parent(tmp_path, state):
  from stock_forecaster.research.store import ResearchStore

  store = ResearchStore(tmp_path)
  parent = store.submit_job("fit_garch", {}, "parent")
  store.claim_job()
  child = store.submit_job("prediction", {}, "child", priority=10, parent_job_id=parent)
  if state == "cancelled":
    store.cancel_job(parent)
    store.complete_job(parent, {})
  elif state == "failed":
    store.fail_job(parent, "fit_failed", "fit failed")
  elif state == "expired":
    store.recover_jobs(0)
  elif state in {"partial", "succeeded"}:
    store.complete_job(parent, {"status": state})
  independent = store.submit_job("prediction", {}, "independent")
  reopened = ResearchStore(tmp_path)
  assert reopened.get_job(child)["parent_job_id"] == parent
  assert reopened.claim_job()["id"] == (child if state == "succeeded" else independent)
  if state != "succeeded":
    assert reopened.claim_job() is None


def test_parent_relation_migration_preserves_existing_jobs(tmp_path):
  from stock_forecaster.research.store import ResearchStore

  store = ResearchStore(tmp_path)
  identifier = store.submit_job("prediction", {}, "legacy")
  with store._connect(write=True) as connection:
    connection.execute("ALTER TABLE jobs DROP COLUMN parent_job_id")
    connection.execute("PRAGMA user_version=1")
  reopened = ResearchStore(tmp_path)
  assert reopened.get_job(identifier)["parent_job_id"] is None
  assert reopened.claim_job()["id"] == identifier
  with reopened._connect() as connection:
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
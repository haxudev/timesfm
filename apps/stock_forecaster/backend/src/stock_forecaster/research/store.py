import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import tempfile
from contextlib import ExitStack, closing, contextmanager
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import duckdb
import numpy as np
import pandas as pd


def _now() -> str:
  return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _json(value) -> str:
  return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _object_json(value: dict) -> str:
  def check_keys(item):
    if isinstance(item, dict):
      if any(not isinstance(key, str) for key in item):
        raise ValueError("JSON object keys must be strings")
      for child in item.values():
        check_keys(child)
    elif isinstance(item, (list, tuple)):
      for child in item:
        check_keys(child)

  if not isinstance(value, dict):
    raise TypeError("Expected a JSON object")
  check_keys(value)
  return _json(value)


def _cell(value):
  if value is None or value is pd.NA or value is pd.NaT:
    return ["null"]
  if isinstance(value, np.datetime64):
    return _cell(pd.Timestamp(value))
  if isinstance(value, np.timedelta64):
    return _cell(pd.Timedelta(value))
  if isinstance(value, np.generic):
    return _cell(value.item())
  if isinstance(value, bool):
    return ["bool", value]
  if isinstance(value, int):
    return ["int", value]
  if isinstance(value, float):
    return ["float", value.hex()]
  if isinstance(value, str):
    return ["str", value]
  if isinstance(value, bytes):
    return ["bytes", value.hex()]
  if isinstance(value, Decimal):
    return ["decimal", str(value)]
  if isinstance(value, (datetime, date, time)):
    return ["datetime", value.isoformat()]
  if isinstance(value, (pd.Timedelta, timedelta)):
    return ["timedelta_ns", pd.Timedelta(value).value]
  raise ValueError(f"Unsupported snapshot scalar type: {type(value).__name__}")


def _identity(frame: pd.DataFrame, metadata: str) -> tuple[str, str]:
  if not len(frame.columns) or any(
    not isinstance(column, str) or not column for column in frame.columns
  ):
    raise ValueError("Snapshot columns must be nonempty strings")
  if len({column.casefold() for column in frame.columns}) != len(frame.columns):
    raise ValueError("Snapshot columns must be unique, ignoring case")
  schema = []
  for column, dtype in frame.dtypes.items():
    field = {"name": column, "dtype": str(dtype)}
    if isinstance(dtype, pd.CategoricalDtype):
      field["categories"] = [_cell(value) for value in dtype.categories]
      field["ordered"] = dtype.ordered
    schema.append(field)
  content = {
    "version": 1,
    "schema": schema,
    "rows": [[_cell(value) for value in row]
             for row in frame.itertuples(index=False, name=None)],
    "metadata": json.loads(metadata),
  }
  return hashlib.sha256(_json(content).encode("utf-8")).hexdigest(), _json(schema)


def _validate_id(identifier: str, length: int) -> None:
  if not isinstance(identifier, str) or not re.fullmatch(
    rf"[0-9a-f]{{{length}}}", identifier
  ):
    raise ValueError("Invalid identifier")


def _file_hash(path: Path) -> str:
  digest = hashlib.sha256()
  with path.open("rb") as stream:
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
      digest.update(chunk)
  return digest.hexdigest()


def _verify_file(path: Path, expected_hash: str) -> None:
  try:
    if path.is_symlink() or _file_hash(path) != expected_hash:
      raise ValueError("Snapshot integrity check failed")
  except OSError as error:
    raise ValueError("Snapshot integrity check failed") from error


def _sync_file(path: Path) -> None:
  with path.open("r+b") as stream:
    os.fsync(stream.fileno())


def _sync_directory(path: Path) -> None:
  if os.name != "nt":
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
      os.fsync(descriptor)
    finally:
      os.close(descriptor)


class ResearchStore:
  """Local immutable snapshots and jobs; each operation owns its connections.

  Snapshots contain ordered columns and rows, not the pandas index. Reads use
  DuckDB's pandas dtype mapping. Metadata and job documents must be JSON objects.
  Higher priorities run first. Expired jobs fail rather than reusing an ID that
  a stale worker could complete; retries require a new idempotency key.
  """

  def __init__(self, root: str | Path, *, read_only: bool = False):
    self.root = Path(root).resolve()
    self.read_only = read_only
    self.snapshots_dir = self.root / "snapshots"
    self.db_path = self.root / "research.sqlite3"
    if self.snapshots_dir.resolve() != self.snapshots_dir:
      raise ValueError("Unsafe snapshots directory")
    if read_only:
      if not self.db_path.is_file() or self.db_path.is_symlink():
        raise ValueError("Read-only store unavailable")
      return
    self.root.mkdir(parents=True, exist_ok=True)
    self.snapshots_dir.mkdir(exist_ok=True)
    with self._connect() as connection:
      connection.execute("PRAGMA journal_mode=WAL")
      connection.executescript("""
        CREATE TABLE IF NOT EXISTS snapshots (
          id TEXT PRIMARY KEY,
          file_sha256 TEXT NOT NULL,
          schema_json TEXT NOT NULL,
          metadata TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY,
          kind TEXT NOT NULL,
          payload TEXT NOT NULL,
          idempotency_key TEXT NOT NULL,
          priority INTEGER NOT NULL,
          status TEXT NOT NULL CHECK (
            status IN ('pending','running','succeeded','partial','failed','cancelled')
          ),
          ticker TEXT,
          result TEXT,
          error TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          started_at TEXT,
          heartbeat_at TEXT,
          finished_at TEXT,
          UNIQUE(kind, idempotency_key)
        );
        CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(status, priority DESC);
        CREATE INDEX IF NOT EXISTS jobs_results ON jobs(kind, ticker, finished_at DESC);
        CREATE INDEX IF NOT EXISTS jobs_leases ON jobs(status, heartbeat_at);
      """)
    with self._connect(write=True) as connection:
      columns = {row["name"] for row in connection.execute("PRAGMA table_info(jobs)")}
      if "cancellation_requested" not in columns:
        connection.execute(
          "ALTER TABLE jobs ADD COLUMN cancellation_requested INTEGER NOT NULL DEFAULT 0"
        )
      if "parent_job_id" not in columns:
        connection.execute("ALTER TABLE jobs ADD COLUMN parent_job_id TEXT REFERENCES jobs(id)")
      if connection.execute("PRAGMA user_version").fetchone()[0] < 2:
        connection.execute("PRAGMA user_version=2")

  @contextmanager
  def _connect(self, write: bool = False):
    if self.read_only and write:
      raise ValueError("Read-only store")
    if self.db_path.is_symlink():
      raise ValueError("Unsafe database path")
    with closing(sqlite3.connect(
      self.db_path.as_uri() + "?mode=ro" if self.read_only else self.db_path,
      uri=self.read_only, timeout=5, isolation_level=None
    )) as connection:
      connection.row_factory = sqlite3.Row
      connection.execute("PRAGMA busy_timeout=5000")
      connection.execute("PRAGMA synchronous=FULL")
      if write:
        connection.execute("BEGIN IMMEDIATE")
      with connection:
        yield connection

  def _snapshot_path(self, snapshot_id: str) -> Path:
    _validate_id(snapshot_id, 64)
    path = self.snapshots_dir / f"{snapshot_id}.parquet"
    if path.is_symlink() or path.resolve().parent != self.snapshots_dir:
      raise ValueError("Snapshot integrity check failed: unsafe path")
    return path

  def _snapshot(self, snapshot_id: str) -> sqlite3.Row:
    _validate_id(snapshot_id, 64)
    with self._connect() as connection:
      row = connection.execute(
        "SELECT * FROM snapshots WHERE id = ?", [snapshot_id]
      ).fetchone()
    if row is None:
      raise KeyError(snapshot_id)
    return row

  def save_snapshot(self, frame: pd.DataFrame, metadata: dict) -> str:
    if self.read_only:
      raise ValueError("Read-only store")
    metadata_json = _object_json(metadata)
    frame = frame.copy(deep=True)
    snapshot_id, schema = _identity(frame, metadata_json)
    path = self._snapshot_path(snapshot_id)
    try:
      existing = self._snapshot(snapshot_id)
    except KeyError:
      existing = None
    if existing is not None:
      _verify_file(path, existing["file_sha256"])
      return snapshot_id
    descriptor, name = tempfile.mkstemp(dir=self.snapshots_dir, suffix=".tmp")
    os.close(descriptor)
    temporary = Path(name)
    try:
      with duckdb.connect(config={"threads": 1}) as connection:
        connection.register("snapshot_frame", frame)
        connection.execute(
          "COPY snapshot_frame TO ? (FORMAT PARQUET)", [str(temporary)]
        )
      _sync_file(temporary)
      file_hash = _file_hash(temporary)
      with self._connect(write=True) as connection:
        existing = connection.execute(
          "SELECT * FROM snapshots WHERE id = ?", [snapshot_id]
        ).fetchone()
        if existing is None:
          os.replace(temporary, self._snapshot_path(snapshot_id))
          _sync_directory(self.snapshots_dir)
          connection.execute(
            "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?)",
            [snapshot_id, file_hash, schema, metadata_json, _now()],
          )
      if existing is not None:
        _verify_file(path, existing["file_sha256"])
      return snapshot_id
    finally:
      temporary.unlink(missing_ok=True)

  def read_snapshot(self, snapshot_id: str) -> pd.DataFrame:
    row = self._snapshot(snapshot_id)
    path = self._snapshot_path(snapshot_id)
    _verify_file(path, row["file_sha256"])
    with duckdb.connect(config={"threads": 1}) as connection:
      return connection.execute("SELECT * FROM read_parquet(?)", [str(path)]).df()

  def snapshot_metadata(self, snapshot_id: str) -> dict:
    return json.loads(self._snapshot(snapshot_id)["metadata"])

  @staticmethod
  def _job(row: sqlite3.Row | None) -> dict | None:
    if row is None:
      return None
    job = dict(row)
    job["cancellation_requested"] = bool(job["cancellation_requested"])
    for field in ("payload", "result", "error"):
      job[field] = json.loads(job[field]) if job[field] is not None else None
    return job

  def submit_job(
    self, kind: str, payload: dict, idempotency_key: str, priority: int = 0,
    *, parent_job_id: str | None = None, lease_seconds: float = 300,
  ) -> str:
    with self._connect(write=True) as connection:
      if parent_job_id is not None and not self._active_job(connection, parent_job_id, lease_seconds):
        raise ValueError("parent_job_inactive")
      return self._submit_job(connection, kind, payload, idempotency_key, priority, parent_job_id)

  def _submit_job(self, connection, kind, payload, idempotency_key, priority=0, parent_job_id=None):
    if not isinstance(kind, str) or not kind.strip():
      raise ValueError("Job kind must be nonempty")
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
      raise ValueError("Job idempotency key must be nonempty")
    if isinstance(priority, bool) or not isinstance(priority, int):
      raise TypeError("Priority must be an integer")
    payload_json = _object_json(payload)
    existing = connection.execute(
      "SELECT id, payload, parent_job_id FROM jobs WHERE kind = ? AND idempotency_key = ?",
      [kind, idempotency_key],
    ).fetchone()
    if existing is not None:
      if (existing["payload"] != payload_json
          or parent_job_id is not None and existing["parent_job_id"] != parent_job_id):
        raise ValueError("Job idempotency key conflicts with its original payload or parent")
      connection.execute("""
        UPDATE jobs SET priority = MAX(priority, ?), updated_at = ?
        WHERE id = ? AND status = 'pending' AND priority < ?
      """, [priority, _now(), existing["id"], priority])
      return existing["id"]
    job_id = uuid4().hex
    now = _now()
    connection.execute("""
      INSERT INTO jobs (
        id, kind, payload, idempotency_key, priority, status, created_at, updated_at, parent_job_id
      ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)
    """, [job_id, kind, payload_json, idempotency_key, priority, now, now, parent_job_id])
    return job_id

  def claim_job(self) -> dict | None:
    with self._connect(write=True) as connection:
      row = connection.execute("""
        SELECT id FROM jobs WHERE status = 'pending'
          AND (parent_job_id IS NULL OR EXISTS (
            SELECT 1 FROM jobs AS parent WHERE parent.id = jobs.parent_job_id
              AND parent.status = 'succeeded'
          ))
        ORDER BY priority DESC, rowid LIMIT 1
      """).fetchone()
      if row is None:
        return None
      now = _now()
      connection.execute("""
        UPDATE jobs SET status = 'running', started_at = ?, heartbeat_at = ?,
          updated_at = ? WHERE id = ? AND status = 'pending'
      """, [now, now, now, row["id"]])
      return self._job(connection.execute(
        "SELECT * FROM jobs WHERE id = ?", [row["id"]]
      ).fetchone())

  def complete_job(self, job_id: str, result: dict, *, lease_seconds=300) -> None:
    with self._connect(write=True) as connection:
      self._complete_job(connection, job_id, result, lease_seconds)

  def _complete_job(self, connection, job_id, result, lease_seconds=300):
    _validate_id(job_id, 32)
    result_json = _object_json(result)
    document = json.loads(result_json)
    status = "partial" if document.get("status") == "partial" else "succeeded"
    row = connection.execute(
      "SELECT * FROM jobs WHERE id = ? AND status = 'running'", [job_id]
    ).fetchone()
    if row is None:
      raise ValueError("Only a running job can be completed")
    if row["cancellation_requested"]:
      now = _now()
      connection.execute("""
        UPDATE jobs SET status = 'cancelled', result = NULL, finished_at = ?,
          updated_at = ? WHERE id = ?
      """, [now, now, job_id])
      return
    if not self._active_job(connection, job_id, lease_seconds):
      raise ValueError("parent_job_inactive")
    ticker = document.get("ticker", json.loads(row["payload"]).get("ticker"))
    if ticker is not None and not isinstance(ticker, str):
      raise ValueError("Result ticker must be a string")
    now = _now()
    connection.execute("""
      UPDATE jobs SET status = ?, result = ?, ticker = ?, finished_at = ?,
        updated_at = ? WHERE id = ? AND status = 'running'
    """, [status, result_json, ticker, now, now, job_id])

  def commit_job(self, job_id, result, *, publish=None, child=None, lease_seconds=300):
    document = json.loads(_object_json(result))
    if document.get("status") == "partial":
      raise ValueError("Publication requires a successful job")
    with ExitStack() as publication, self._connect(write=True) as connection:
      if not self._active_job(connection, job_id, lease_seconds):
        raise ValueError("parent_job_inactive")
      if publish is not None:
        publication.enter_context(publish())
      if child is not None:
        document["prediction_job_id"] = self._submit_job(
          connection, **child(), parent_job_id=job_id,
        )
      self._complete_job(connection, job_id, document, lease_seconds)

  def fail_job(self, job_id: str, code: str, message: str) -> None:
    _validate_id(job_id, 32)
    if not isinstance(code, str) or not code or not isinstance(message, str):
      raise ValueError("Failure code and message must be strings with a nonempty code")
    error = _object_json({"code": code, "message": message})
    with self._connect(write=True) as connection:
      now = _now()
      changed = connection.execute("""
        UPDATE jobs SET status = CASE WHEN cancellation_requested THEN 'cancelled'
          ELSE 'failed' END, error = CASE WHEN cancellation_requested THEN NULL ELSE ? END,
          finished_at = ?, updated_at = ?
        WHERE id = ? AND status = 'running'
      """, [error, now, now, job_id]).rowcount
      if not changed:
        raise ValueError("Only a running job can fail")

  def get_job(self, job_id: str) -> dict | None:
    _validate_id(job_id, 32)
    with self._connect() as connection:
      return self._job(connection.execute(
        "SELECT * FROM jobs WHERE id = ?", [job_id]
      ).fetchone())

  def cancel_job(self, job_id: str) -> bool:
    _validate_id(job_id, 32)
    with self._connect(write=True) as connection:
      now = _now()
      return bool(connection.execute("""
        UPDATE jobs SET cancellation_requested = 1,
          status = CASE WHEN status = 'pending' THEN 'cancelled' ELSE status END,
          finished_at = CASE WHEN status = 'pending' THEN ? ELSE NULL END, updated_at = ?
        WHERE id = ? AND status IN ('pending', 'running')
      """, [now, now, job_id]).rowcount)

  def heartbeat(self, job_id: str, lease_seconds=300) -> bool:
    _validate_id(job_id, 32)
    with self._connect(write=True) as connection:
      now = _now()
      cutoff = (datetime.now(timezone.utc) - timedelta(seconds=lease_seconds)).isoformat()
      return bool(connection.execute("""
        UPDATE jobs SET heartbeat_at = ?, updated_at = ?
        WHERE id = ? AND status = 'running' AND heartbeat_at >= ?
      """, [now, now, job_id, cutoff]).rowcount)

  def _active_job(self, connection, job_id, lease_seconds=300):
    _validate_id(job_id, 32)
    if not math.isfinite(lease_seconds) or lease_seconds < 0:
      raise ValueError("Invalid lease age")
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=lease_seconds)).isoformat()
    return connection.execute("""
      SELECT id FROM jobs WHERE id=? AND status='running'
        AND cancellation_requested=0 AND heartbeat_at>=?
    """, [job_id, cutoff]).fetchone() is not None

  def job_active(self, job_id, lease_seconds=300):
    with self._connect() as connection:
      return self._active_job(connection, job_id, lease_seconds)

  @contextmanager
  def guard_job(self, job_id, lease_seconds=300):
    with self._connect(write=True) as connection:
      if not self._active_job(connection, job_id, lease_seconds):
        raise ValueError("parent_job_inactive")
      yield

  def recover_jobs(self, age_seconds: float = 300) -> int:
    """Fail expired leases; never let an old worker finish a newly claimed retry."""
    if not math.isfinite(age_seconds) or age_seconds < 0:
      raise ValueError("Lease age must be finite and nonnegative")
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat(
      timespec="microseconds"
    )
    error = _json({"code": "lease_expired", "message": "Worker heartbeat expired"})
    with self._connect(write=True) as connection:
      now = _now()
      return connection.execute("""
        UPDATE jobs SET status = CASE WHEN cancellation_requested THEN 'cancelled' ELSE 'failed' END,
          error = CASE WHEN cancellation_requested THEN NULL ELSE ? END,
          finished_at = ?, updated_at = ?
        WHERE status = 'running' AND heartbeat_at < ?
      """, [error, now, now, cutoff]).rowcount

  def latest_result(self, ticker: str) -> dict | None:
    with self._connect() as connection:
      row = connection.execute("""
        SELECT result FROM jobs WHERE kind = 'prediction' AND ticker = ?
          AND status IN ('succeeded', 'partial')
        ORDER BY finished_at DESC, rowid DESC LIMIT 1
      """, [ticker]).fetchone()
    return json.loads(row["result"]) if row is not None else None

  def list_jobs(self, limit: int = 50) -> list[dict]:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
      raise ValueError("Limit must be a nonnegative integer")
    with self._connect() as connection:
      return [self._job(row) for row in connection.execute(
        "SELECT * FROM jobs ORDER BY rowid DESC LIMIT ?", [limit]
      ).fetchall()]

  def backup(self, destination: str | Path) -> Path:
    """Atomically publish a new backup directory; existing destinations are refused."""
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
      raise FileExistsError(destination)
    if destination.resolve().is_relative_to(self.root):
      raise ValueError("Backup destination must be outside the live store")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".research-backup-", dir=destination.parent))
    try:
      models = self._model_inventory()
      database = staging / self.db_path.name
      with self._connect() as source, closing(sqlite3.connect(database)) as target:
        source.backup(target)
        rows = target.execute("SELECT id, file_sha256 FROM snapshots").fetchall()
        target.execute("PRAGMA journal_mode=DELETE")
      snapshots = staging / "snapshots"
      snapshots.mkdir()
      for snapshot_id, expected_hash in rows:
        source_path = self._snapshot_path(snapshot_id)
        target_path = snapshots / f"{snapshot_id}.parquet"
        shutil.copyfile(source_path, target_path)
        _verify_file(target_path, expected_hash)
        _sync_file(target_path)
      for relative, (expected_hash, _) in models.items():
        source_path = self.root / relative
        target_path = staging / relative
        target_path.parent.mkdir(parents=True, exist_ok=True)
        _verify_file(source_path, expected_hash)
        shutil.copyfile(source_path, target_path)
        _verify_file(target_path, expected_hash)
        _sync_file(target_path)
      if self._model_inventory() != models:
        raise ValueError("Model artifacts changed during backup")
      for directory in sorted((staging / "models").rglob("*"), reverse=True):
        if directory.is_dir():
          _sync_directory(directory)
      if (staging / "models").exists():
        _sync_directory(staging / "models")
      _sync_file(database)
      _sync_directory(snapshots)
      _sync_directory(staging)
      os.rename(staging, destination)
      _sync_directory(destination.parent)
      return destination
    finally:
      if staging.exists():
        shutil.rmtree(staging)

  def _model_inventory(self):
    root = self.root / "models"
    files = {}
    if root.is_symlink():
      raise ValueError("Unsafe model directory")
    if not root.exists():
      return files
    for directory, folders, names in os.walk(root, followlinks=False):
      for name in [*folders, *names]:
        if (Path(directory) / name).is_symlink():
          raise ValueError("Unsafe model symlink")
    for category in ("artifacts", "lightgbm", "garch"):
      parent = root / category
      if not parent.exists():
        continue
      for directory in parent.iterdir():
        if not directory.is_dir() or directory.name.startswith("."):
          continue
        manifest_path = directory / "manifest.json"
        try:
          manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
          identifier = manifest.pop("artifact_id")
          _validate_id(identifier, 64)
          if hashlib.sha256(_json(manifest).encode()).hexdigest() != identifier:
            raise ValueError("Model manifest integrity check failed")
          model_type = manifest.get("model_type")
          if model_type == "lightgbm_return_probability":
            native = manifest["files"]
            if set(native) != {"regressor.txt", "classifier.txt"}:
              raise ValueError("Unsafe model file list")
          elif model_type == "zero_mean_garch_1_1_student_t":
            native = {}
          else:
            raise ValueError("Unsupported model artifact")
          for name, expected in native.items():
            _verify_file(directory / name, expected)
          for name in ("manifest.json", *native):
            path = directory / name
            stat = path.stat()
            files[path.relative_to(self.root)] = (
              _file_hash(path), (stat.st_size, stat.st_mtime_ns, stat.st_ino),
            )
        except (OSError, KeyError, TypeError) as error:
          raise ValueError("Model artifact integrity check failed") from error
    return files
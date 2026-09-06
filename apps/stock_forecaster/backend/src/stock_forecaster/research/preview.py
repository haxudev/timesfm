import argparse
import asyncio
import json
import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..errors import install_error_handlers
from .evidence import evidence_overview
from .store import ResearchStore


def _check_sensitive(value):
  if isinstance(value, dict):
    for key, item in value.items():
      if re.search(r"(?:^|_)(?:password|passwd|token|secret|credentials?|private_path|authorization|api_key)(?:_|$)", key, re.IGNORECASE):
        raise ValueError("sensitive_preview_report")
      _check_sensitive(item)
  elif isinstance(value, list):
    for item in value:
      _check_sensitive(item)
  elif isinstance(value, str) and re.search(r"\b(?:token|password|api_key)=|\bbearer\s+", value, re.IGNORECASE):
    raise ValueError("sensitive_preview_report")


def seed_reports(destination, sources):
  destination = Path(destination).resolve()
  if destination.exists():
    raise ValueError("preview_destination_exists")
  readers = [ResearchStore(source, read_only=True) for source in sources]
  if not readers or any(destination.is_relative_to(reader.root) for reader in readers):
    raise ValueError("invalid_preview_destination")
  selected = {}
  for reader in readers:
    with reader._connect() as connection:
      rows = connection.execute(
        "SELECT id, metadata, created_at FROM snapshots "
        "WHERE json_extract(metadata, '$.kind') IN ('collection_report','training_report','readiness_report','provider_capabilities') "
        "ORDER BY created_at DESC, rowid DESC LIMIT 100"
      ).fetchall()
    for row in rows:
      if (reader.snapshots_dir / (row["id"] + ".parquet")).stat().st_size > 2 * 1024 * 1024:
        raise ValueError("preview_report_budget_exceeded")
      frame = reader.read_snapshot(row["id"])
      if len(frame) != 1 or list(frame) != ["report"] or not isinstance(json.loads(frame.iloc[0]["report"]), dict):
        raise ValueError("invalid_preview_report")
      _check_sensitive(json.loads(frame.iloc[0]["report"]))
      _check_sensitive(json.loads(row["metadata"]))
      selected.setdefault(row["id"], (row, frame))
  store = ResearchStore(destination)
  for identifier, (row, frame) in selected.items():
    if store.save_snapshot(frame, json.loads(row["metadata"])) != identifier:
      raise ValueError("preview_snapshot_identity_mismatch")
    with store._connect(write=True) as connection:
      connection.execute("UPDATE snapshots SET created_at=? WHERE id=?", [row["created_at"], identifier])
  evidence_overview(destination)
  return {"imported_reports": len(selected), "snapshot_ids": list(selected)}


def create_preview_app(root, *, web_port=5175):
  if type(web_port) is not int or not 1024 <= web_port <= 65535:
    raise ValueError("invalid_preview_port")
  root = Path(root).resolve()
  app = FastAPI(title="Local Research Evidence", docs_url=None, redoc_url=None, openapi_url=None)
  install_error_handlers(app)
  app.add_middleware(CORSMiddleware, allow_origins=[f"http://localhost:{web_port}", f"http://127.0.0.1:{web_port}"],
                     allow_methods=["GET"], allow_headers=["Content-Type"], allow_credentials=False)

  @app.get("/api/v1/health")
  async def health():
    return {"status": "ok", "mode": "read_only_evidence"}

  @app.get("/api/v2/evidence")
  async def evidence():
    return await asyncio.to_thread(evidence_overview, root)

  return app


def main(argv=None):
  parser = argparse.ArgumentParser()
  commands = parser.add_subparsers(dest="command", required=True)
  seed = commands.add_parser("seed")
  seed.add_argument("--root", required=True)
  seed.add_argument("--source", required=True, action="append")
  serve = commands.add_parser("serve")
  serve.add_argument("--root", required=True)
  serve.add_argument("--port", type=int, default=8013)
  serve.add_argument("--web-port", type=int, default=5175)
  arguments = parser.parse_args(argv)
  if arguments.command == "seed":
    print(json.dumps(seed_reports(arguments.root, arguments.source)))
  else:
    import uvicorn

    if not 1024 <= arguments.port <= 65535:
      raise ValueError("invalid_preview_port")
    evidence_overview(arguments.root)
    uvicorn.run(create_preview_app(arguments.root, web_port=arguments.web_port),
                host="127.0.0.1", port=arguments.port)


if __name__ == "__main__":
  main()
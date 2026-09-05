import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_research_service import RecordedProvider

from stock_forecaster.config import Settings
from stock_forecaster.errors import AppError
from stock_forecaster.main import create_app
from stock_forecaster.model import ModelManager, ModelOutput
from stock_forecaster.research.store import ResearchStore


class InternalModel:
  def predict(self, context, horizon):
    return ModelOutput(np.full(horizon, 0.01), np.zeros((horizon, 9)))

  def predict_many(self, contexts, horizon):
    return [ModelOutput(np.full(horizon, context[-1]), np.zeros((horizon, 9))) for context in contexts]


@pytest.fixture
def api_parts(tmp_path):
  root = tmp_path / "research"
  token_file = tmp_path / "internal-token"
  token_file.write_text("test-only-token")
  settings = Settings(
    research_data_dir=root, internal_token_file=token_file,
    research_http_url="http://testserver", device="cpu",
  )
  provider = RecordedProvider()
  loads = []

  def factory(checkpoint, device):
    loads.append((checkpoint, device))
    return InternalModel()

  manager = ModelManager(settings.checkpoint, factory=factory)
  app = create_app(settings, provider, manager)
  return TestClient(app), root, provider, manager, loads, settings


def test_lazy_store_v1_compatibility_and_offline_v2_lifecycle(api_parts):
  client, root, provider, manager, loads, _ = api_parts
  assert not root.exists()
  assert client.get("/api/v1/health").status_code == 200
  assert not root.exists()
  response = client.post("/api/v2/predictions", json={"ticker": "600519"})
  assert response.status_code == 202
  assert response.json()["status"] == "pending"
  assert loads == []
  job_id = response.json()["job_id"]
  assert client.post("/api/v2/predictions", json={"ticker": "600519"}).json() == response.json()
  service = client.app.state.research_service
  job = service.store.claim_job()
  service.store.complete_job(job_id, service.execute(job))

  def offline(*args, **kwargs):
    raise AssertionError("latest must not access market or inference")

  provider.fetch = offline
  manager.predict = offline
  latest = client.get("/api/v2/predictions/600519/latest")
  assert latest.status_code == 200
  assert set(latest.json()) == {
    "schema_version", "bundle_id", "ticker", "name", "instrument_type",
    "origin", "issued_at", "snapshot_id", "status", "horizons", "path",
    "history", "components", "warnings",
  }
  assert latest.json()["bundle_id"] == job_id
  assert client.get(f"/api/v2/jobs/{job_id}").json() == {
    "id": job_id, "status": "partial", "result": latest.json(), "error": None,
    "cancellation_requested": False,
  }
  assert client.get("/api/v2/predictions/000001/latest").status_code == 404
  assert ResearchStore(root).latest_result("600519.SS") == latest.json()


def test_validation_cancel_conflict_and_cors(api_parts):
  client, root, _, _, _, _ = api_parts
  for payload in (
    {"ticker": "SPY"}, {"ticker": "../../secret"}, {"ticker": "600519", "horizon": 3},
    {"ticker": "600519", "horizon": True}, {"ticker": "600519", "device": "cuda"},
  ):
    assert client.post("/api/v2/predictions", json=payload).status_code == 422
  assert ResearchStore(root).list_jobs() == []
  body = {"ticker": "600519", "horizon": 5}
  header = {"Idempotency-Key": "once"}
  first = client.post("/api/v2/predictions", json=body, headers=header)
  assert first.status_code == 202
  assert client.post("/api/v2/predictions", json={**body, "horizon": 20}, headers=header).status_code == 409
  job_id = first.json()["job_id"]
  assert client.delete(f"/api/v2/jobs/{job_id}").json()["status"] == "cancelled"
  assert client.delete(f"/api/v2/jobs/{job_id}").status_code == 409
  assert client.delete("/api/v2/jobs/" + "a" * 32).status_code == 404
  assert client.get("/api/v2/jobs/not-an-id").status_code == 404
  next_job = client.post("/api/v2/predictions", json=body).json()["job_id"]
  client.app.state.research_service.store.claim_job()
  cancelled = client.delete(f"/api/v2/jobs/{next_job}")
  assert cancelled.status_code == 200
  assert cancelled.json()["status"] == "running"
  assert cancelled.json()["cancellation_requested"] is True
  preflight = client.options("/api/v2/jobs/" + job_id, headers={
    "Origin": "http://localhost:5173", "Access-Control-Request-Method": "DELETE",
    "Access-Control-Request-Headers": "Idempotency-Key",
  })
  assert preflight.status_code == 200


def test_symbols_use_persisted_universe_without_invented_names(api_parts):
  client, root, _, _, _, _ = api_parts
  store = ResearchStore(root)
  store.save_snapshot(pd.DataFrame([{
    "ticker": "600519.SS", "name": "Recorded issuer", "instrument_type": "stock",
  }]), {"kind": "universe"})
  assert client.get("/api/v2/symbols", params={"q": "Recorded"}).json() == {
    "items": [{"ticker": "600519.SS", "name": "Recorded issuer", "instrument_type": "stock"}],
  }
  items = client.get("/api/v2/symbols", params={"q": "000002"}).json()["items"]
  assert items == [{"ticker": "000002.SZ", "name": None, "instrument_type": "stock"}]
  assert len(client.get("/api/v2/symbols", params={"q": ""}).json()["items"]) == 7
  assert client.get("/api/v2/symbols", params={"q": "no-such-symbol"}).json() == {"items": []}


def test_reports_api_is_offline_and_only_exposes_report_kinds(api_parts):
  import json

  client, root, provider, manager, _, _ = api_parts
  store = ResearchStore(root)
  document = {"collection": {"succeeded": 3}, "execution": {"predicted": 0}}
  identifier = store.save_snapshot(pd.DataFrame({"report": [json.dumps(document)]}), {
    "kind": "daily_report", "as_of": "2026-09-04",
  })
  private = store.save_snapshot(pd.DataFrame({"report": ["{}"]}), {"kind": "features"})

  def forbidden(*args, **kwargs):
    raise AssertionError("reports must be offline")

  provider.fetch = forbidden
  manager.predict = forbidden
  response = client.get("/api/v2/reports/latest")
  assert response.status_code == 200
  assert response.json() == {"snapshot_id": identifier, "kind": "daily_report", "report": document}
  assert client.get(f"/api/v2/reports/{identifier}").json() == response.json()
  assert client.get(f"/api/v2/reports/{private}").status_code == 404
  assert client.get("/api/v2/reports/latest?kind=features").status_code == 422


def test_internal_auth_caps_and_shared_model_adapter(api_parts):
  from stock_forecaster.research.runtime import HttpTimesFMAdapter

  client, root, _, manager, loads, settings = api_parts
  body = {"context": [0.001] * 32, "horizon": 20}
  endpoint = "/api/v2/internal/timesfm"
  assert client.post(endpoint, json=body).status_code == 401
  auth = {"Authorization": "Bearer test-only-token"}
  for invalid in (
    {**body, "context": [0.1] * 513}, {**body, "context": [0.1] * 31},
    {**body, "horizon": 21}, {**body, "contexts": [[0.1] * 32]},
    {**body, "device": "cuda"}, {**body, "context": [[0.1] * 32]},
    {**body, "context": [1e100] * 32},
  ):
    assert client.post(endpoint, json=invalid, headers=auth).status_code == 422
  adapter = HttpTimesFMAdapter(settings, client=client)
  output = adapter.predict(np.zeros(32), 20)
  assert output.point.shape == (20,)
  assert output.quantiles.shape == (20, 9)
  assert len(loads) == 1
  assert not root.exists()
  manager.predict(np.zeros(32), 20, "cpu")
  assert len(loads) == 1
  with manager._inference, pytest.raises(AppError) as caught:
    adapter.predict(np.zeros(32), 20)
  assert caught.value.code == "model_capacity_exceeded"
  assert caught.value.status_code == 429


def test_internal_missing_secret_disables_inference_without_files(tmp_path):
  settings = Settings(research_data_dir=tmp_path / "not-created")
  client = TestClient(create_app(settings))
  response = client.post("/api/v2/internal/timesfm", json={"context": [0.1] * 32, "horizon": 20})
  assert response.status_code == 503
  assert not (tmp_path / "not-created").exists()


def test_research_settings_environment_and_bounds(monkeypatch, tmp_path):
  monkeypatch.setenv("STOCK_FORECASTER_RESEARCH_DATA_DIR", str(tmp_path))
  monkeypatch.setenv("STOCK_FORECASTER_INTERNAL_TOKEN_FILE", str(tmp_path / "token"))
  settings = Settings()
  assert Path(settings.research_data_dir) == tmp_path
  assert settings.internal_token_file == tmp_path / "token"
  assert settings.research_http_url == "http://backend:8000"
  for seconds in (0, -1, 61, float("inf")):
    with pytest.raises(ValidationError):
      Settings(worker_poll_seconds=seconds)
  for url in ("file:///token", "http://user:password@backend:8000", "http://backend:8000?token=x"):
    with pytest.raises(ValidationError):
      Settings(research_http_url=url)


def test_http_adapter_sanitizes_network_and_upstream_errors(api_parts, caplog):
  import httpx

  from stock_forecaster.research.runtime import HttpTimesFMAdapter

  _, _, _, _, _, settings = api_parts

  def broken(request):
    return httpx.Response(503, json={
      "error": {"code": "model_load_failed", "message": "private-token-path"},
    })

  with (
    httpx.Client(transport=httpx.MockTransport(broken)) as client,
    pytest.raises(AppError) as caught,
  ):
    HttpTimesFMAdapter(settings, client).predict(np.zeros(32), 20)
  assert caught.value.code == "model_load_failed"
  assert caught.value.status_code == 503
  assert "private-token" not in str(caught.value)
  assert "test-only-token" not in caplog.text

  def timed_out(request):
    raise httpx.ReadTimeout("private-token-path")

  with (
    httpx.Client(transport=httpx.MockTransport(timed_out)) as client,
    pytest.raises(AppError) as caught,
  ):
    HttpTimesFMAdapter(settings, client).predict(np.zeros(32), 20)
  assert caught.value.code == "internal_inference_unavailable"


ARTIFACT = "hf:google/timesfm-3.0-pytorch@43046b85ec22d584a13f8098c2ed39c889e129c2"
AUTH = {"Authorization": "Bearer test-only-token"}
BATCH_ENDPOINT = "/api/v2/internal/timesfm-batch"


@pytest.mark.parametrize("identity", [None, ARTIFACT])
def test_internal_single_response_and_adapters_propagate_actual_identity(api_parts, monkeypatch, identity):
  from stock_forecaster.research.runtime import (
    HttpTimesFMAdapter,
    ManagerTimesFMAdapter,
  )

  client, _, _, manager, _, settings = api_parts
  if identity:
    monkeypatch.setattr(InternalModel, "artifact_id", identity, raising=False)
  response = client.post("/api/v2/internal/timesfm", json={"context": [0.0] * 32}, headers=AUTH)
  assert response.status_code == 200
  assert response.json()["artifact_id"] == identity
  local = ManagerTimesFMAdapter(manager, settings)
  local.predict(np.zeros(32), 20)
  assert local.artifact_id == identity
  remote = HttpTimesFMAdapter(settings, client)
  remote.predict(np.zeros(32), 20)
  assert remote.artifact_id == identity


def test_internal_batch_and_http_client_share_loaded_model_and_identity(api_parts, monkeypatch):
  from stock_forecaster.research.runtime import (
    HttpTimesFMAdapter,
    ManagerTimesFMAdapter,
  )

  client, root, _, manager, loads, settings = api_parts
  monkeypatch.setattr(InternalModel, "artifact_id", ARTIFACT, raising=False)
  contexts = [[float(index)] * (32 if index % 2 else 512) for index in range(8)]
  body = {"contexts": contexts, "horizon": 20}
  assert client.post(BATCH_ENDPOINT, json=body).status_code == 401
  assert client.post(BATCH_ENDPOINT, json=body, headers={"Authorization": "Bearer wrong"}).status_code == 401
  assert loads == []
  response = client.post(BATCH_ENDPOINT, json=body, headers=AUTH)
  assert response.status_code == 200
  document = response.json()
  assert document["artifact_id"] == ARTIFACT
  assert [output["point"] for output in document["outputs"]] == [[float(index)] * 20 for index in range(8)]
  assert all(set(output) == {"point", "quantiles"} for output in document["outputs"])
  remote = HttpTimesFMAdapter(settings, client)
  outputs = remote.predict_many([np.array(context) for context in contexts], 1)
  assert [output.point.tolist() for output in outputs] == [[float(index)] for index in range(8)]
  assert remote.artifact_id == ARTIFACT
  local = ManagerTimesFMAdapter(manager, settings)
  assert len(local.predict_many([np.zeros(32)], 1)) == 1
  assert local.artifact_id == ARTIFACT
  manager.predict(np.zeros(32), 20, "cpu")
  assert len(loads) == 1
  assert not root.exists()
  with manager._inference, pytest.raises(AppError) as caught:
    remote.predict_many([np.zeros(32)], 1)
  assert caught.value.code == "model_capacity_exceeded"
  assert caught.value.status_code == 429
  assert remote.artifact_id is None


@pytest.mark.parametrize("change", [
  {"contexts": []}, {"contexts": [[0.0] * 32] * 9},
  {"contexts": [[0.0] * 31]}, {"contexts": [[0.0] * 513]},
  {"contexts": [0.0] * 32}, {"contexts": [[[0.0]] * 32]},
  {"contexts": [[1e100] * 32]}, {"contexts": [[float("nan")] * 32]},
  {"contexts": [[float("inf")] * 32]}, {"contexts": [["0.1"] * 32]},
  {"contexts": [[True] * 32]}, {"contexts": [[None] * 32]},
  {"horizon": 0}, {"horizon": 21}, {"horizon": True}, {"horizon": "1"},
  {"horizon": 1.5}, {"device": "cuda"},
])
def test_internal_batch_rejects_invalid_input_without_loading(api_parts, change):
  client, root, _, _, loads, _ = api_parts
  body = {"contexts": [[0.0] * 32], "horizon": 20, **change}
  response = client.post(BATCH_ENDPOINT, content=json.dumps(body), headers={
    **AUTH, "Content-Type": "application/json",
  })
  assert response.status_code == 422
  assert loads == []
  assert not root.exists()


def test_internal_batch_missing_token_and_configured_horizon_cap(api_parts):
  client, _, _, _, loads, settings = api_parts
  body = {"contexts": [[0.0] * 32], "horizon": 20}
  settings.max_horizon = 5
  assert client.post(BATCH_ENDPOINT, json=body, headers=AUTH).status_code == 422
  settings.internal_token_file = None
  assert client.post(BATCH_ENDPOINT, json={**body, "horizon": 5}, headers=AUTH).status_code == 503
  assert loads == []


@pytest.mark.parametrize("invalid", ["count", "shape", "nonfinite", "identity"])
def test_http_batch_rejects_malformed_output_and_drops_stale_identity(api_parts, invalid):
  import httpx

  from stock_forecaster.research.runtime import HttpTimesFMAdapter

  *_, settings = api_parts
  document = {"outputs": [{"point": [0.0], "quantiles": [[0.0] * 9]}], "artifact_id": ARTIFACT}

  def response(request):
    assert request.url.path == BATCH_ENDPOINT
    assert json.loads(request.content) == {"contexts": [[0.0] * 32], "horizon": 1}
    return httpx.Response(200, content=json.dumps(document), headers={"Content-Type": "application/json"})

  with httpx.Client(transport=httpx.MockTransport(response)) as client:
    adapter = HttpTimesFMAdapter(settings, client)
    adapter.predict_many([np.zeros(32)], 1)
    assert adapter.artifact_id == ARTIFACT
    if invalid == "count":
      document["outputs"].append(document["outputs"][0])
    elif invalid == "shape":
      document["outputs"][0]["quantiles"] = [[0.0] * 10]
    elif invalid == "nonfinite":
      document["outputs"][0]["point"] = [float("nan")]
    else:
      document["artifact_id"] = {"untrusted": "object"}
    with pytest.raises(AppError) as caught:
      adapter.predict_many([np.zeros(32)], 1)
    assert caught.value.code == "model_output_invalid"
    assert adapter.artifact_id is None


def test_http_identity_is_optional_and_not_reused_for_old_server(api_parts):
  import httpx

  from stock_forecaster.research.runtime import HttpTimesFMAdapter

  *_, settings = api_parts
  document = {"point": [0.0], "quantiles": [[0.0] * 9], "artifact_id": ARTIFACT}
  with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=document))) as client:
    adapter = HttpTimesFMAdapter(settings, client)
    adapter.predict(np.zeros(32), 1)
    assert adapter.artifact_id == ARTIFACT
    del document["artifact_id"]
    adapter.predict(np.zeros(32), 1)
    assert adapter.artifact_id is None


def test_create_app_passes_requested_revision_to_manager(api_parts, monkeypatch):
  from stock_forecaster import main

  client, _, provider, _, _, settings = api_parts
  settings.checkpoint_revision = "b" * 40
  constructed = []

  def factory(*args, **kwargs):
    constructed.append(kwargs)
    return ModelManager(*args, **kwargs, factory=lambda checkpoint, device: InternalModel())

  monkeypatch.setattr(main, "ModelManager", factory)
  client = TestClient(create_app(settings, provider))
  assert client.get("/api/v1/health").status_code == 200
  assert constructed[0]["revision"] == "b" * 40


@pytest.mark.parametrize("transport", ["manager", "http"])
def test_inference_identity_survives_persisted_bundle_and_api_reload(api_parts, monkeypatch, transport):
  from stock_forecaster.research.runtime import HttpTimesFMAdapter

  client, root, _, _, _, settings = api_parts
  monkeypatch.setattr(InternalModel, "artifact_id", ARTIFACT, raising=False)
  response = client.post("/api/v2/predictions", json={"ticker": "600519"})
  assert response.status_code == 202
  job_id = response.json()["job_id"]
  service = client.app.state.research_service
  if transport == "http":
    service.model = HttpTimesFMAdapter(settings, client)
  job = service.store.claim_job()
  bundle = service.execute(job)
  service.store.complete_job(job_id, bundle)
  stored = ResearchStore(root)
  assert stored.latest_result("600519.SS")["components"]["timesfm"]["artifact_id"] == ARTIFACT
  assert stored.snapshot_metadata(bundle["snapshot_id"])["prediction_identity"]["checkpoint_revision"] == ARTIFACT.split("@")[1]
  assert client.get(f"/api/v2/jobs/{job_id}").json()["result"] == bundle
  assert client.get("/api/v2/predictions/600519/latest").json() == bundle
  assert not any("immutable weight revision is not pinned" in warning for warning in bundle["warnings"])
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from stock_forecaster.config import Settings
from stock_forecaster.errors import AppError
from stock_forecaster.forecasting import _china_calendar
from stock_forecaster.market_data import MarketDataService, ProviderResult
from stock_forecaster.model import ModelOutput
from stock_forecaster.research.store import ResearchStore


class RecordedProvider:
  def __init__(self, count=580):
    dates = _china_calendar().sessions_in_range("2022-01-04", "2025-06-30")[-count:]
    self.frame = pd.DataFrame({
      "Close": 100 * np.exp(np.arange(len(dates)) * 0.001),
      "Volume": np.arange(len(dates), dtype=float) + 1000,
    }, index=dates)

  def fetch(self, ticker, selection):
    return ProviderResult(self.frame, "CNY", "akshare", "stock", None)


class BoundaryModel:
  def __init__(self, store, error=None):
    self.store = store
    self.error = error
    self.context = None

  def predict(self, context, horizon):
    assert list(self.store.snapshots_dir.glob("*.parquet"))
    self.context = context.copy()
    assert horizon == 20
    if self.error:
      raise self.error
    return ModelOutput(np.full(horizon, 0.01), np.zeros((horizon, 9)))


def make_service(tmp_path, count=580, error=None):
  from stock_forecaster.research.service import PredictionService

  store = ResearchStore(tmp_path)
  provider = RecordedProvider(count)
  model = BoundaryModel(store, error)
  service = PredictionService(
    store, MarketDataService(provider, 0), model,
    Settings(device="cpu", research_data_dir=tmp_path),
  )
  return service, store, provider, model


def execute(service, store, ticker="600519", horizon=5):
  submitted = service.submit(ticker, horizon)
  job = store.claim_job()
  assert submitted["job_id"] == job["id"]
  bundle = service.execute(job)
  store.complete_job(job["id"], bundle)
  return bundle


def test_freezes_before_inference_and_persists_three_horizons(tmp_path):
  service, store, provider, model = make_service(tmp_path)
  bundle = execute(service, store)
  assert bundle["schema_version"] == 2
  assert bundle["ticker"] == "600519.SS"
  assert bundle["name"] is None
  assert bundle["origin"] == "2025-06-30"
  assert bundle["status"] == "partial"
  assert [row["horizon"] for row in bundle["horizons"]] == [1, 5, 20]
  assert [row["target_date"] for row in bundle["horizons"]] == [
    "2025-07-01", "2025-07-07", "2025-07-28",
  ]
  assert bundle["horizons"][1]["timesfm_return"] == pytest.approx(np.expm1(0.05))
  assert len(bundle["path"]) == 20
  assert len(model.context) == 512
  assert set(bundle["components"]) == {"timesfm", "lightgbm", "garch"}
  assert bundle["components"]["lightgbm"]["status"] == "not_ready"
  assert bundle["components"]["garch"]["status"] == "not_ready"
  assert bundle["horizons"][0]["up_probability"] is None
  assert "quantiles" not in str(bundle)
  assert datetime.fromisoformat(bundle["issued_at"]).tzinfo is not None
  frozen = store.read_snapshot(bundle["snapshot_id"])
  metadata = store.snapshot_metadata(bundle["snapshot_id"])
  assert metadata["adjustment"] == "qfq"
  assert metadata["point_in_time"] is False
  assert metadata["source"] == "akshare"
  assert "PIT" in " ".join(bundle["warnings"])
  provider.frame.iloc[-1, 0] = 1
  assert store.read_snapshot(bundle["snapshot_id"]).equals(frozen)
  assert "volume" in frozen
  assert ResearchStore(tmp_path).latest_result("600519.SS") == bundle


@pytest.mark.parametrize("artifact_id", [
  "hf:google/timesfm-3.0-pytorch@43046b85ec22d584a13f8098c2ed39c889e129c2",
  "sha256:" + "a" * 64,
  None,
])
def test_freezes_requested_revision_and_persists_only_actual_model_identity(tmp_path, artifact_id):
  service, store, _, model = make_service(tmp_path)
  original = model.predict

  def predict(context, horizon):
    output = original(context, horizon)
    if artifact_id is not None:
      model.artifact_id = artifact_id
    return output

  model.predict = predict
  bundle = execute(service, store)
  assert bundle["components"]["timesfm"] == {
    "status": "ready", "reason": None, "artifact_id": artifact_id,
  }
  stored = ResearchStore(tmp_path)
  assert stored.latest_result("600519.SS") == bundle
  metadata = stored.snapshot_metadata(bundle["snapshot_id"])
  assert metadata["prediction_identity"]["checkpoint_revision"] == "43046b85ec22d584a13f8098c2ed39c889e129c2"
  unpinned = [warning for warning in bundle["warnings"] if "immutable weight revision is not pinned" in warning]
  assert bool(unpinned) == (artifact_id is None)


@pytest.mark.parametrize("key", [None, "same-explicit-key"])
def test_requested_revision_changes_prediction_identity_and_rejects_old_job(tmp_path, key):
  service, store, _, model = make_service(tmp_path)
  first = service.submit("600519", idempotency_key=key)
  job = store.claim_job()
  service.settings = Settings(device="cpu", research_data_dir=tmp_path, checkpoint_revision="b" * 40)
  second = service.submit("600519", idempotency_key=key)
  assert second["job_id"] != first["job_id"]
  assert store.get_job(second["job_id"])["payload"]["identity"]["checkpoint_revision"] == "b" * 40
  with pytest.raises(AppError) as caught:
    service.execute(job)
  assert caught.value.code == "configuration_changed"
  assert model.context is None


def test_custom_checkpoint_snapshot_does_not_claim_default_revision(tmp_path):
  service, store, _, _ = make_service(tmp_path)
  service.settings = Settings(checkpoint="trusted/other", device="cpu", research_data_dir=tmp_path)
  bundle = execute(service, store)
  identity = store.snapshot_metadata(bundle["snapshot_id"])["prediction_identity"]
  assert identity["checkpoint"] == "trusted/other"
  assert identity["checkpoint_revision"] is None
  assert bundle["components"]["timesfm"]["artifact_id"] is None


@pytest.mark.parametrize("count,missing,code", [
  (32, False, "insufficient_history"),
  (80, True, "non_continuous_sessions"),
])
def test_rejects_short_or_noncontinuous_context(tmp_path, count, missing, code):
  service, store, provider, model = make_service(tmp_path, count)
  if missing:
    provider.frame = provider.frame.drop(provider.frame.index[-10])
  service.submit("600519", 5)
  with pytest.raises(AppError) as caught:
    service.execute(store.claim_job())
  assert caught.value.code == code
  assert model.context is None


def test_timesfm_failure_is_sanitized_partial_not_fake_prediction(tmp_path):
  service, store, _, _ = make_service(
    tmp_path, error=AppError("model_load_failed", "sensitive upstream detail", 503),
  )
  bundle = execute(service, store)
  assert bundle["components"]["timesfm"]["status"] == "unavailable"
  assert bundle["components"]["timesfm"]["reason"] == "model_load_failed"
  assert bundle["path"] == []
  assert all(row["timesfm_return"] is None for row in bundle["horizons"])
  assert "sensitive" not in str(bundle)


def test_submission_validates_before_writing_and_conflicts_on_changed_payload(tmp_path):
  service, store, _, _ = make_service(tmp_path)
  with pytest.raises(AppError):
    service.submit("../../models", 5)
  assert store.list_jobs() == []
  first = service.submit("600519", 5, "request-1")
  assert service.submit("600519.SS", 5, "request-1") == first
  with pytest.raises(AppError) as caught:
    service.submit("600519", 20, "request-1")
  assert caught.value.status_code == 409


def test_incomplete_current_session_is_not_an_origin(tmp_path):
  service, store, _, _ = make_service(tmp_path, 80)
  service.clock = lambda: datetime(2025, 6, 30, 4, tzinfo=timezone.utc)
  bundle = execute(service, store)
  assert bundle["origin"] == "2025-06-27"


def test_garch_loads_directory_without_fitting_and_isolates_bad_artifact(tmp_path, monkeypatch):
  from stock_forecaster.research.volatility import GarchModel

  service, store, _, _ = make_service(tmp_path)
  _, prices, provenance, _ = service.freeze("600519")
  fit_metadata = {**provenance, "origin": "2025-06-27"}
  fit_snapshot = store.save_snapshot(prices.iloc[:-1], fit_metadata)
  directory = tmp_path / "models" / "garch" / "600519.SS"
  artifact_id = GarchModel(
    {"omega": 0.02, "alpha[1]": 0.1, "beta[1]": 0.85, "nu": 8},
    {**fit_metadata, "snapshot_id": fit_snapshot, "trained_through": "2025-06-27"},
  ).save(directory)

  def forbidden(*args, **kwargs):
    raise AssertionError("prediction must never fit")

  monkeypatch.setattr(GarchModel, "fit", forbidden)
  bundle = execute(service, store)
  assert bundle["components"]["garch"] == {
    "status": "ready", "reason": None, "artifact_id": artifact_id,
  }
  assert 0 < bundle["horizons"][0]["volatility"] < bundle["horizons"][2]["volatility"]
  (directory / "manifest.json").write_text("{}")
  bundle = execute(service, store)
  assert bundle["components"]["garch"]["status"] == "unavailable"
  assert bundle["components"]["timesfm"]["status"] == "ready"
  assert bundle["horizons"][0]["volatility"] is None


def test_lightgbm_requires_trusted_origin_schema_and_preserves_other_horizons(tmp_path, monkeypatch):
  from dataclasses import replace

  from test_research_lightgbm import fit_small, training_fixture

  from stock_forecaster.research.gradient_boosting import GradientBoostingModel

  service, store, _, _ = make_service(tmp_path)
  frames, targets = training_fixture()
  input_id, input_prices, input_meta, _ = service.freeze("600519")
  training_id = store.save_snapshot(input_prices.iloc[:-30], {**input_meta, "origin": str(input_prices.date.iloc[-31])})
  context = {"snapshot_id": input_id}
  schema = {"version": "fixture-v1", "columns": list(frames[0].columns)}
  base = fit_small(frames, targets)
  for horizon in (1, 5, 20):
    replace(base, horizon=horizon, metadata={
      "feature_schema": schema, "trained_through": "2025-05-30",
      "dataset_ids": {"bars": training_id},
      "source": "akshare", "adjustment": "qfq",
    }).save(tmp_path / "models" / "lightgbm" / str(horizon))

  def forbidden(*args, **kwargs):
    raise AssertionError("prediction must never train")

  monkeypatch.setattr(GradientBoostingModel, "train", forbidden)
  unavailable, values = service.runtime.lightgbm("600519.SS", "2025-06-30", "stock")
  assert unavailable.status == "not_ready"
  assert values == {}
  features = frames[1].iloc[:1].copy()
  metadata = {
    "kind": "features", "ticker": "600519.SS", "origin": "2025-06-30",
    "feature_schema": schema, "trusted": True, "point_in_time": True,
    "price_snapshot_id": input_id,
  }
  store.save_snapshot(features, {**metadata, "origin": "2025-06-27"})
  assert service.runtime.lightgbm("600519.SS", "2025-06-30", "stock")[1] == {}
  store.save_snapshot(features, {**metadata, "trusted": False})
  assert service.runtime.lightgbm("600519.SS", "2025-06-30", "stock")[1] == {}
  store.save_snapshot(features, metadata)
  state, values = service.runtime.lightgbm("600519.SS", "2025-06-30", "stock", context=context)
  assert state.status == "ready"
  expected = base.predict(features)
  for horizon in (1, 5, 20):
    assert values[horizon] == pytest.approx((expected[0][0], expected[1][0]))
  (tmp_path / "models" / "lightgbm" / "5" / "regressor.txt").write_text("corrupt")
  state, values = service.runtime.lightgbm("600519.SS", "2025-06-30", "stock", context=context)
  assert state.status == "unavailable"
  assert set(values) == {1, 20}
  assert "5:" in state.reason
  assert service.runtime.lightgbm("000001.SS", "2025-06-30", "index")[0].status == "not_supported"


def test_artifact_revision_fences_changes_since_auto_submission(tmp_path):
  from stock_forecaster.research.volatility import GarchModel

  service, store, _, model = make_service(tmp_path)
  service.submit("600519", 5)
  GarchModel(
    {"omega": 0.02, "alpha[1]": 0.1, "beta[1]": 0.85, "nu": 8},
    {"ticker": "600519.SS", "origin": "2025-06-27"},
  ).save(tmp_path / "models" / "garch" / "600519.SS")
  with pytest.raises(AppError) as caught:
    service.execute(store.claim_job())
  assert caught.value.code == "artifact_changed"
  assert model.context is None


def test_auto_identity_reuses_all_horizons_but_refreshes_when_features_arrive(tmp_path):
  service, store, _, _ = make_service(tmp_path)
  first = service.submit("600519", 1)
  assert service.submit("600519", 20) == first
  store.save_snapshot(pd.DataFrame({"momentum_5": [0.1]}), {
    "kind": "features", "ticker": "600519.SS", "origin": "2025-06-30",
    "trusted": True, "point_in_time": True, "feature_schema": "fixture-v1",
  })
  assert service.submit("600519", 5)["job_id"] != first["job_id"]


def test_model_artifact_change_during_inference_cannot_publish_mixed_bundle(tmp_path):
  from stock_forecaster.research.volatility import GarchModel

  service, store, _, model = make_service(tmp_path)
  original = model.predict

  def changed(context, horizon):
    GarchModel(
      {"omega": 0.02, "alpha[1]": 0.1, "beta[1]": 0.85, "nu": 8},
      {"ticker": "600519.SS", "origin": "2025-06-27"},
    ).save(tmp_path / "models" / "garch" / "600519.SS")
    return original(context, horizon)

  model.predict = changed
  service.submit("600519", 5, "explicit-attempt")
  with pytest.raises(AppError) as caught:
    service.execute(store.claim_job())
  assert caught.value.code == "artifact_changed"
  assert store.latest_result("600519.SS") is None


def test_freeze_binds_exact_persisted_bars_identity(tmp_path):
  from stock_forecaster.research.pipeline import PersistedMarketProvider

  service, store, provider, _ = make_service(tmp_path)
  frame = provider.frame.reset_index().rename(columns={"index": "date", "Close": "close", "Volume": "volume"})
  frame["ticker"] = "600519.SS"
  bars = store.save_snapshot(frame, {
    "kind": "bars", "tickers": ["600519.SS"], "source": "akshare",
    "adjustment": "qfq", "adjustment_revision": "v7", "source_version": "2025",
  })
  service.market = MarketDataService(PersistedMarketProvider(store), 0)
  identifier, _, metadata, _ = service.freeze("600519")
  assert metadata["bars_snapshot_id"] == bars
  assert metadata["dataset_ids"]["bars"] == bars
  assert metadata["adjustment_revision"] == "v7"
  assert metadata["source_version"] == "2025"
  assert store.snapshot_metadata(identifier) == metadata


@pytest.mark.parametrize("change", [
  "none", "price", "source", "adjustment", "version", "missing_ref", "missing_trained",
  "future", "unavailable_snapshot", "missing_file", "corrupt_file",
])
def test_garch_old_fit_requires_compatible_overlapping_basis(tmp_path, change):
  from stock_forecaster.research.volatility import GarchModel

  service, store, _, _ = make_service(tmp_path)
  live, prices, metadata, returns = service.freeze("600519")
  old = prices.iloc[:-1].copy()
  old_metadata = {**metadata, "origin": "2025-06-27"}
  if change == "price":
    old.loc[old.index[-2], "price"] *= 1.1
  if change in {"source", "adjustment"}:
    old_metadata[change] = "other"
  if change == "version":
    old_metadata["adjustment_revision"] = "different"
  reference = store.save_snapshot(old, old_metadata)
  artifact_metadata = {**old_metadata, "snapshot_id": reference,
                       "trained_through": "2025-06-27"}
  if change == "missing_ref":
    artifact_metadata.pop("snapshot_id")
  if change == "missing_trained":
    artifact_metadata.pop("trained_through")
  if change == "future":
    artifact_metadata["trained_through"] = "2025-07-01"
  if change == "unavailable_snapshot":
    artifact_metadata["snapshot_id"] = "f" * 64
  if change == "missing_file":
    (store.snapshots_dir / f"{reference}.parquet").unlink()
  if change == "corrupt_file":
    (store.snapshots_dir / f"{reference}.parquet").write_bytes(b"corrupt")
  GarchModel({"omega": .02, "alpha[1]": .1, "beta[1]": .85, "nu": 8},
             artifact_metadata).save(tmp_path / "models" / "garch" / "600519.SS")
  state, values = service.runtime.garch("600519.SS", "2025-06-30", returns,
                                        context={"snapshot_id": live})
  assert (state.status == "ready") == (change == "none")
  assert bool(values) == (change == "none")
  if change in {"unavailable_snapshot", "missing_file"}:
    assert state.status == "not_ready"
    assert state.reason == "fit_snapshot_unavailable"
  if change == "corrupt_file":
    assert state.status == "unavailable"
    assert state.reason == "artifact_invalid"


@pytest.mark.parametrize("change", ["none", "wrong_prices", "missing_ref", "missing_trained", "missing_schema", "future"])
def test_lightgbm_inference_binding_is_not_training_dataset_identity(tmp_path, change):
  from dataclasses import replace

  from test_research_lightgbm import fit_small, training_fixture

  service, store, _, _ = make_service(tmp_path)
  live, prices, basis, _ = service.freeze("600519")
  train_id = store.save_snapshot(prices.iloc[:-30], {**basis, "origin": str(prices.date.iloc[-31])})
  frames, targets = training_fixture()
  base = fit_small(frames, targets)
  schema = {"version": "fixture", "columns": list(frames[0].columns)}
  metadata = {"feature_schema": schema, "trained_through": "2025-05-30",
              "dataset_ids": {"bars": train_id}}
  if change in {"missing_trained", "missing_schema"}:
    metadata.pop("trained_through" if change == "missing_trained" else "feature_schema")
  if change == "future":
    metadata["trained_through"] = "2025-07-01"
  for horizon in (1, 5, 20):
    replace(base, horizon=horizon, metadata=metadata).save(tmp_path / "models" / "lightgbm" / str(horizon))
  feature_meta = {"kind": "features", "ticker": "600519.SS", "origin": "2025-06-30",
                  "feature_schema": schema, "trusted": True, "point_in_time": True,
                  "price_snapshot_id": live}
  if change == "missing_ref":
    feature_meta.pop("price_snapshot_id")
  if change == "wrong_prices":
    feature_meta["price_snapshot_id"] = store.save_snapshot(prices.assign(price=prices.price * 1.1), basis)
  store.save_snapshot(frames[1].iloc[:1], feature_meta)
  state, values = service.runtime.lightgbm("600519.SS", "2025-06-30", "stock",
                                           context={"snapshot_id": live})
  assert (state.status == "ready") == (change == "none")
  assert bool(values) == (change == "none")


def test_freeze_records_training_references_and_prior_separately(tmp_path):
  from dataclasses import replace

  from test_research_lightgbm import fit_small, training_fixture

  service, _, _, _ = make_service(tmp_path)
  train_id, _, _, _ = service.freeze("600519")
  frames, targets = training_fixture()
  base = fit_small(frames, targets)
  replace(base, horizon=5, metadata={"dataset_ids": {"bars": train_id},
    "feature_schema": "fixture", "trained_through": "2025-05-30", "training_up_prior": .63,
  }).save(tmp_path / "models" / "lightgbm" / "5")
  live_id, _, metadata, _ = service.freeze("600519")
  provenance = metadata["model_provenance"]["lightgbm"]["5"]
  assert provenance["training_refs"] == {"bars": train_id}
  assert provenance["trained_through"] == "2025-05-30"
  assert provenance["training_up_prior"] == .63
  assert train_id != live_id
  assert "inference_refs" not in provenance


def test_freeze_retains_the_bars_version_read_before_concurrent_publication(tmp_path, monkeypatch):
  from stock_forecaster.research.pipeline import PersistedMarketProvider

  service, store, provider, _ = make_service(tmp_path)
  frame = provider.frame.reset_index().rename(columns={"index": "date", "Close": "close", "Volume": "volume"})
  frame["ticker"] = "600519.SS"
  metadata = {"kind": "bars", "tickers": ["600519.SS"], "source": "akshare",
              "adjustment": "qfq", "adjustment_revision": "read-version"}
  original_id = store.save_snapshot(frame, metadata)
  read = store.read_snapshot
  published = []

  def concurrently_publish(identifier):
    result = read(identifier)
    if identifier == original_id and not published:
      published.append(store.save_snapshot(frame, {**metadata, "adjustment_revision": "new-version"}))
    return result

  monkeypatch.setattr(store, "read_snapshot", concurrently_publish)
  service.market = MarketDataService(PersistedMarketProvider(store), 0)
  _, _, frozen, _ = service.freeze("600519")
  assert frozen["bars_snapshot_id"] == original_id
  assert frozen["adjustment_revision"] == "read-version"


def test_interactive_request_promotes_same_scheduled_job_without_new_identity(tmp_path):
  service, store, _, _ = make_service(tmp_path)
  scheduled = service.submit("600519", priority=0)["job_id"]
  before = store.get_job(scheduled)["payload"]
  interactive = service.submit("600519", priority=10)["job_id"]
  assert interactive == scheduled
  assert store.get_job(scheduled)["priority"] == 10
  assert store.get_job(scheduled)["payload"] == before
  service.submit("600519", priority=0)
  assert store.get_job(scheduled)["priority"] == 10
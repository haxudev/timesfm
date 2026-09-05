import hashlib
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError

from stock_forecaster.config import Settings
from stock_forecaster.errors import AppError
from stock_forecaster.model import (
  ModelManager,
  ModelOutput,
  TimesFM3Adapter,
  resolve_device,
  validate_output,
)


class Adapter:
  def predict(self, context, horizon):
    return ModelOutput(np.zeros(horizon), np.zeros((horizon, 9)))


def test_quantile_shape_maps_nine_timesfm3_columns():
  quantiles = np.arange(27).reshape(3, 9)
  result = validate_output(np.arange(3), quantiles, 3)
  assert result.quantiles[:, 0].tolist() == [0, 9, 18]
  assert result.quantiles[:, 8].tolist() == [8, 17, 26]


@pytest.mark.parametrize(
  ("point", "quantiles"),
  [(np.zeros(2), np.zeros((3, 9))), (np.zeros(3), np.zeros((3, 10)))],
)
def test_rejects_wrong_output_shape(point, quantiles):
  with pytest.raises(AppError) as caught:
    validate_output(point, quantiles, 3)
  assert caught.value.code == "model_output_invalid"


def test_rejects_non_finite_output():
  with pytest.raises(AppError):
    validate_output(np.array([np.nan]), np.zeros((1, 9)), 1)


def test_concurrent_calls_initialize_model_once():
  calls = 0
  lock = threading.Lock()

  def factory(checkpoint, device):
    nonlocal calls
    with lock:
      calls += 1
    time.sleep(0.02)
    return Adapter()

  manager = ModelManager("checkpoint", factory=factory, max_concurrent=4)
  with ThreadPoolExecutor(max_workers=4) as executor:
    results = list(
      executor.map(
        lambda _: manager.predict(np.ones(32), 2, "cpu"),
        range(4),
      )
    )
  assert calls == 1
  assert len(results) == 4


def test_auto_falls_back_to_cpu(monkeypatch):
  monkeypatch.setattr("stock_forecaster.model.cuda_available", lambda: False)
  assert resolve_device("auto") == (
    "cpu",
    "CUDA is unavailable; inference is using CPU.",
  )


def test_explicit_unavailable_cuda_errors(monkeypatch):
  monkeypatch.setattr("stock_forecaster.model.cuda_available", lambda: False)
  with pytest.raises(AppError) as caught:
    resolve_device("cuda")
  assert caught.value.code == "device_unavailable"


REPO = "google/timesfm-3.0-pytorch"
REVISION = "43046b85ec22d584a13f8098c2ed39c889e129c2"


@pytest.fixture
def external_model(monkeypatch, tmp_path):
  snapshot = tmp_path / "snapshots" / REVISION
  snapshot.mkdir(parents=True)
  (snapshot / "config.json").write_bytes(b"{}")
  (snapshot / "model.safetensors").write_bytes(b"weights")
  captured = {"downloads": [], "configs": [], "batches": []}

  def download(**kwargs):
    captured["downloads"].append(kwargs)
    return str(snapshot)

  class Evaluator:
    def __init__(self, config):
      captured["configs"].append(config)

    def predict_batch(self, **kwargs):
      captured["batches"].append(kwargs)
      return [
        SimpleNamespace(
          forecast=np.full(kwargs["horizon"], context[-1]),
          quantiles=np.full((kwargs["horizon"], 9), context[-1]),
        )
        for context in kwargs["contexts"]
      ]

  monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=download))
  monkeypatch.setitem(sys.modules, "timesfm3", SimpleNamespace(
    ModelConfig=lambda **kwargs: SimpleNamespace(**kwargs), TimesFM3Evaluator=Evaluator,
  ))
  return snapshot, captured


def test_default_checkpoint_loads_exact_immutable_local_snapshot(external_model):
  snapshot, captured = external_model
  settings = Settings(_env_file=None)
  manager = ModelManager(settings.checkpoint, revision=settings.checkpoint_revision)
  output, device, _ = manager.predict(np.ones(32), 2, "cpu")
  assert settings.checkpoint_revision == REVISION
  assert manager.checkpoint == REPO
  assert captured["downloads"] == [{
    "repo_id": REPO, "revision": REVISION,
    "allow_patterns": ["config.json", "model.safetensors"],
  }]
  assert captured["configs"][0].checkpoint_path == str(snapshot)
  assert manager.artifact_id == f"hf:{REPO}@{REVISION}"
  assert device == "cpu"
  assert output.point.tolist() == [1, 1]


def test_official_adapter_never_resolves_moving_main(external_model):
  _, captured = external_model
  adapter = TimesFM3Adapter(REPO, "cpu")
  assert captured["downloads"][0]["revision"] == REVISION
  assert adapter.artifact_id == f"hf:{REPO}@{REVISION}"


def test_evaluator_cannot_resolve_remote_files_after_snapshot_download(external_model):
  _, captured = external_model
  TimesFM3Adapter(REPO, "cpu")
  assert captured["configs"][0].local_files_only is True


def test_trusted_local_home_path_preserves_v1_expansion(external_model, monkeypatch):
  snapshot, captured = external_model
  monkeypatch.setenv("USERPROFILE", str(snapshot.parent))
  monkeypatch.setenv("HOME", str(snapshot.parent))
  adapter = TimesFM3Adapter("~/" + snapshot.name, "cpu")
  assert captured["downloads"] == []
  assert captured["configs"][0].checkpoint_path == str(snapshot)
  assert adapter.artifact_id.startswith("sha256:")


def test_custom_repo_does_not_inherit_official_revision(external_model):
  _, captured = external_model
  settings = Settings(checkpoint="trusted/custom", _env_file=None)
  manager = ModelManager(settings.checkpoint, revision=settings.checkpoint_revision)
  manager.predict(np.ones(32), 1, "cpu")
  assert settings.checkpoint_revision is None
  assert captured["downloads"][0]["revision"] is None
  assert manager.artifact_id == f"hf:trusted/custom@{REVISION}"


@pytest.mark.parametrize("revision", ["main", "v1", "a" * 39, "a" * 41, "g" * 40, ""])
def test_checkpoint_revision_rejects_non_commit_refs(revision):
  with pytest.raises(ValidationError):
    Settings(checkpoint_revision=revision, _env_file=None)


def test_checkpoint_revision_normalizes_full_sha():
  settings = Settings(checkpoint="trusted/custom", checkpoint_revision="A" * 40, _env_file=None)
  assert settings.checkpoint_revision == "a" * 40


def test_local_checkpoint_hashes_files_without_claiming_remote_identity(external_model, monkeypatch):
  snapshot, captured = external_model
  opened = []
  original_open = type(snapshot).open

  def tracked_open(path, *args, **kwargs):
    opened.append(path.name)
    return original_open(path, *args, **kwargs)

  monkeypatch.setattr(type(snapshot), "open", tracked_open)
  adapter = TimesFM3Adapter(str(snapshot), "cpu", revision=REVISION)
  adapter.predict(np.ones(32), 2)
  adapter.predict(np.ones(32), 2)
  expected = hashlib.sha256(
    b"config.json\0" + (2).to_bytes(8, "big") + b"{}"
    + b"model.safetensors\0" + (7).to_bytes(8, "big") + b"weights"
  ).hexdigest()
  assert adapter.artifact_id == f"sha256:{expected}"
  assert opened == ["config.json", "model.safetensors"]
  assert captured["downloads"] == []
  assert captured["configs"][0].checkpoint_path == str(snapshot)


def test_custom_two_argument_factory_has_no_fabricated_artifact():
  calls = []

  def factory(checkpoint, device):
    calls.append((checkpoint, device))
    return Adapter()

  manager = ModelManager(REPO, factory=factory)
  manager.predict(np.ones(32), 2, "cpu")
  assert calls == [(REPO, "cpu")]
  assert manager.artifact_id is None


def test_predict_many_uses_one_native_batch_in_order(external_model):
  _, captured = external_model
  adapter = TimesFM3Adapter(REPO, "cpu")
  contexts = [np.full(32 + index * 3, index, dtype=np.float64) for index in range(8)]
  outputs = adapter.predict_many(contexts, 20)
  assert [output.point.tolist() for output in outputs] == [[index] * 20 for index in range(8)]
  assert len(captured["batches"]) == 1
  batch = captured["batches"][0]
  assert batch["horizon"] == 20
  assert batch["return_quantiles"] is True
  assert batch["sort_quantiles"] is True
  assert batch["use_symmetric_averaging"] is False
  assert batch["make_positive"] is False
  assert [len(context) for context in batch["contexts"]] == [32, 35, 38, 41, 44, 47, 50, 53]
  assert all(context.dtype == np.float32 and context.flags.c_contiguous for context in batch["contexts"])
  assert captured["configs"][0].per_core_batch_size == 8


@pytest.mark.parametrize("invalid", ["count", "shape", "point_nan", "quantiles_inf"])
def test_batch_rejects_invalid_output_in_any_member(external_model, invalid):
  adapter = TimesFM3Adapter(REPO, "cpu")
  outputs = [SimpleNamespace(forecast=np.zeros(2), quantiles=np.zeros((2, 9))) for _ in range(2)]
  if invalid == "count":
    outputs.pop()
  elif invalid == "shape":
    outputs[1].quantiles = np.zeros((2, 10))
  elif invalid == "point_nan":
    outputs[1].forecast[0] = np.nan
  else:
    outputs[1].quantiles[0, 0] = np.inf
  adapter._model.predict_batch = lambda **kwargs: outputs
  with pytest.raises(AppError) as caught:
    adapter.predict_many([np.zeros(32), np.ones(64)], 2)
  assert caught.value.code == "model_output_invalid"


@pytest.mark.parametrize(("contexts", "horizon"), [
  ([], 1), ([np.zeros(32)] * 9, 1), ([np.zeros(31)], 1), ([np.zeros(513)], 1),
  ([np.zeros((32, 1))], 1), ([np.full(32, np.nan)], 1), ([np.full(32, np.inf)], 1),
  ([np.full(32, 1e100)], 1), ([np.zeros(32)], 0), ([np.zeros(32)], 21),
  ([np.zeros(32)], True), ([np.zeros(32)], 1.5), ([np.array(["1"] * 32)], 1),
])
def test_invalid_batch_rejected_before_model_loading(contexts, horizon):
  def forbidden(*args):
    raise AssertionError("invalid input must not load a model")

  manager = ModelManager("checkpoint", factory=forbidden)
  with pytest.raises(AppError) as caught:
    manager.predict_many(contexts, horizon, "cpu")
  assert caught.value.code == "validation_error"
  assert manager.state == "not_loaded"


def test_manager_single_and_batch_share_loader_and_capacity(external_model):
  _, captured = external_model
  manager = ModelManager(REPO)
  single, _, _ = manager.predict(np.zeros(1024), 60, "cpu")
  outputs, device, warning = manager.predict_many([np.ones(32), np.full(512, 2)], 20, "cpu")
  assert single.point.shape == (60,)
  assert [output.point[0] for output in outputs] == [1, 2]
  assert device == "cpu" and warning is None
  assert len(captured["configs"]) == 1
  with manager._inference, pytest.raises(AppError) as caught:
    manager.predict_many([np.ones(32)], 1, "cpu")
  assert caught.value.code == "model_capacity_exceeded"
  assert len(captured["batches"]) == 2


def test_batch_holds_same_semaphore_against_single_requests(external_model):
  manager = ModelManager(REPO)
  manager.predict(np.zeros(32), 1, "cpu")
  started, release = threading.Event(), threading.Event()
  model = manager._models["cpu"]._model
  original = model.predict_batch

  def blocked(**kwargs):
    started.set()
    assert release.wait(5)
    return original(**kwargs)

  model.predict_batch = blocked
  with ThreadPoolExecutor(max_workers=1) as executor:
    future = executor.submit(manager.predict_many, [np.zeros(32)], 1, "cpu")
    try:
      assert started.wait(5)
      with pytest.raises(AppError) as caught:
        manager.predict(np.zeros(32), 1, "cpu")
      assert caught.value.code == "model_capacity_exceeded"
    finally:
      release.set()
    assert len(future.result()[0]) == 1


def test_manager_validates_custom_batch_outputs_and_releases_capacity():
  class BatchAdapter(Adapter):
    def predict_many(self, contexts, horizon):
      return [ModelOutput(np.zeros(horizon), np.zeros((horizon, 10))) for _ in contexts]

  manager = ModelManager("checkpoint", factory=lambda checkpoint, device: BatchAdapter())
  with pytest.raises(AppError) as caught:
    manager.predict_many([np.ones(32)], 2, "cpu")
  assert caught.value.code == "model_output_invalid"
  assert manager.predict(np.zeros(32), 2, "cpu")[0].point.tolist() == [0, 0]

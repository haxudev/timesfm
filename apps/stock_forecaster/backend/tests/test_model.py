import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from stock_forecaster.errors import AppError
from stock_forecaster.model import (
  ModelManager,
  ModelOutput,
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

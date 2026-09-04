from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from .errors import AppError


@dataclass(frozen=True)
class ModelOutput:
  point: np.ndarray
  quantiles: np.ndarray


class ModelAdapter(Protocol):
  def predict(self, context: np.ndarray, horizon: int) -> ModelOutput: ...


def cuda_available() -> bool:
  try:
    import torch

    return bool(torch.cuda.is_available())
  except ImportError:
    return False


def resolve_device(requested: str) -> tuple[str, str | None]:
  if requested == "cuda":
    if not cuda_available():
      raise AppError(
        "device_unavailable",
        "CUDA was requested but is not available.",
        422,
      )
    return "cuda", None
  if requested == "auto":
    if cuda_available():
      return "cuda", None
    return "cpu", "CUDA is unavailable; inference is using CPU."
  return "cpu", None


class TimesFM3Adapter:
  def __init__(self, checkpoint: str, device: str) -> None:
    try:
      from timesfm3 import ModelConfig, TimesFM3Evaluator

      config = ModelConfig(
        checkpoint_path=checkpoint,
        per_core_batch_size=1,
        device=device,
      )
      self._model = TimesFM3Evaluator(config)
    except Exception as error:
      raise AppError(
        "model_load_failed",
        "TimesFM could not be loaded.",
        503,
      ) from error

  def predict(self, context: np.ndarray, horizon: int) -> ModelOutput:
    try:
      outputs = list(
        self._model.predict_batch(
          contexts=[np.ascontiguousarray(context, dtype=np.float32)],
          horizon=horizon,
          return_quantiles=True,
          use_symmetric_averaging=False,
          make_positive=False,
          sort_quantiles=True,
        )
      )
    except Exception as error:
      raise AppError(
        "model_inference_failed",
        "TimesFM inference failed.",
        503,
      ) from error
    if len(outputs) != 1:
      raise AppError("model_output_invalid", "TimesFM returned invalid output.", 502)
    return validate_output(outputs[0].forecast, outputs[0].quantiles, horizon)


def validate_output(
  point: np.ndarray | None,
  quantiles: np.ndarray | None,
  horizon: int,
) -> ModelOutput:
  point_array = np.asarray(point, dtype=np.float64)
  quantile_array = np.asarray(quantiles, dtype=np.float64)
  if point_array.shape != (horizon,) or quantile_array.shape != (horizon, 9):
    raise AppError(
      "model_output_invalid",
      "TimesFM returned an unexpected output shape.",
      502,
    )
  if not np.isfinite(point_array).all() or not np.isfinite(quantile_array).all():
    raise AppError(
      "model_output_invalid",
      "TimesFM returned non-finite values.",
      502,
    )
  return ModelOutput(point=point_array, quantiles=quantile_array)


class ModelManager:
  def __init__(
    self,
    checkpoint: str,
    enabled: bool = True,
    factory: Callable[[str, str], ModelAdapter] = TimesFM3Adapter,
    max_concurrent: int = 1,
  ) -> None:
    self.checkpoint = checkpoint
    self.enabled = enabled
    self.factory = factory
    self._models: dict[str, ModelAdapter] = {}
    self._load_lock = threading.Lock()
    self._inference = threading.BoundedSemaphore(max_concurrent)
    self.state = "disabled" if not enabled else "not_loaded"

  def predict(
    self,
    context: np.ndarray,
    horizon: int,
    requested_device: str,
  ) -> tuple[ModelOutput, str, str | None]:
    if not self.enabled:
      raise AppError("model_disabled", "Forecasting is disabled.", 503)
    device, warning = resolve_device(requested_device)
    try:
      with self._load_lock:
        model = self._models.get(device)
        if model is None:
          self.state = "loading"
          model = self.factory(self.checkpoint, device)
          self._models[device] = model
          self.state = "ready"
      if not self._inference.acquire(blocking=False):
        raise AppError(
          "model_capacity_exceeded",
          "The model is busy; try again shortly.",
          429,
        )
      try:
        output = model.predict(context, horizon)
      finally:
        self._inference.release()
      return validate_output(output.point, output.quantiles, horizon), device, warning
    except AppError:
      if self.state == "loading":
        self.state = "error"
      raise
    except Exception as error:
      self.state = "error"
      raise AppError("model_load_failed", "TimesFM could not be loaded.", 503) from error

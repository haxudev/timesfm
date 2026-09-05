from __future__ import annotations

import hashlib
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from .config import resolve_checkpoint_revision
from .errors import AppError


@dataclass(frozen=True)
class ModelOutput:
  point: np.ndarray
  quantiles: np.ndarray


class ModelAdapter(Protocol):
  def predict(self, context: np.ndarray, horizon: int) -> ModelOutput: ...

  def predict_many(self, contexts: list[np.ndarray], horizon: int) -> list[ModelOutput]: ...


def validate_contexts(contexts: list[np.ndarray], horizon: int) -> list[np.ndarray]:
  if type(horizon) is not int or not 1 <= horizon <= 20 or not 1 <= len(contexts) <= 8:
    raise AppError("validation_error", "Invalid inference batch or horizon.", 422)
  normalized = []
  for context in contexts:
    try:
      array = np.asarray(context)
      if (
        array.ndim != 1 or not 32 <= array.size <= 512 or array.dtype.kind not in "iuf"
        or not np.isfinite(array).all() or np.any(array < -3.4e38) or np.any(array > 3.4e38)
      ):
        raise ValueError("Invalid context")
      normalized.append(np.ascontiguousarray(array, dtype=np.float32))
    except (TypeError, ValueError, OverflowError) as error:
      raise AppError("validation_error", "Invalid inference context.", 422) from error
  return normalized


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
  def __init__(self, checkpoint: str, device: str, revision: str | None = None) -> None:
    revision = resolve_checkpoint_revision(checkpoint, revision)
    try:
      from timesfm3 import ModelConfig, TimesFM3Evaluator

      snapshot = Path(checkpoint).expanduser()
      self.artifact_id: str | None = None
      if not snapshot.is_dir():
        from huggingface_hub import snapshot_download

        snapshot = Path(snapshot_download(
          repo_id=checkpoint,
          revision=revision,
          allow_patterns=["config.json", "model.safetensors"],
        ))
        if snapshot.parent.name == "snapshots" and re.fullmatch(r"[0-9a-f]{40}", snapshot.name):
          if revision is not None and snapshot.name != revision:
            raise ValueError("Resolved checkpoint does not match the requested revision")
          self.artifact_id = f"hf:{checkpoint}@{snapshot.name}"
      if self.artifact_id is None:
        digest = hashlib.sha256()
        for name in ("config.json", "model.safetensors"):
          path = snapshot / name
          digest.update(name.encode("ascii") + b"\0")
          digest.update(path.stat().st_size.to_bytes(8, "big"))
          with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
              digest.update(chunk)
        self.artifact_id = f"sha256:{digest.hexdigest()}"
      config = ModelConfig(
        checkpoint_path=str(snapshot),
        local_files_only=True,
        per_core_batch_size=8,
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
    return self._predict_batch([context], horizon)[0]

  def predict_many(self, contexts: list[np.ndarray], horizon: int) -> list[ModelOutput]:
    return self._predict_batch(validate_contexts(contexts, horizon), horizon)

  def _predict_batch(self, contexts: list[np.ndarray], horizon: int) -> list[ModelOutput]:
    try:
      outputs = list(
        self._model.predict_batch(
          contexts=[np.ascontiguousarray(context, dtype=np.float32) for context in contexts],
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
    if len(outputs) != len(contexts):
      raise AppError("model_output_invalid", "TimesFM returned invalid output.", 502)
    return [validate_output(output.forecast, output.quantiles, horizon) for output in outputs]


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
    factory: Callable[[str, str], ModelAdapter] | None = None,
    max_concurrent: int = 1,
    revision: str | None = None,
  ) -> None:
    self.checkpoint = checkpoint
    self.checkpoint_revision = resolve_checkpoint_revision(checkpoint, revision)
    self.enabled = enabled
    self.factory = factory or TimesFM3Adapter
    self._models: dict[str, ModelAdapter] = {}
    self._load_lock = threading.Lock()
    self._inference = threading.BoundedSemaphore(max_concurrent)
    self.state = "disabled" if not enabled else "not_loaded"

  @property
  def artifact_id(self) -> str | None:
    identities = {getattr(model, "artifact_id", None) for model in self._models.copy().values()}
    return identities.pop() if len(identities) == 1 else None

  def predict(
    self,
    context: np.ndarray,
    horizon: int,
    requested_device: str,
  ) -> tuple[ModelOutput, str, str | None]:
    outputs, device, warning = self._predict([context], horizon, requested_device, many=False)
    return outputs[0], device, warning

  def predict_many(
    self,
    contexts: list[np.ndarray],
    horizon: int,
    requested_device: str,
  ) -> tuple[list[ModelOutput], str, str | None]:
    contexts = validate_contexts(contexts, horizon)
    return self._predict(contexts, horizon, requested_device, many=True)

  def _predict(
    self,
    contexts: list[np.ndarray],
    horizon: int,
    requested_device: str,
    *,
    many: bool,
  ) -> tuple[list[ModelOutput], str, str | None]:
    if not self.enabled:
      raise AppError("model_disabled", "Forecasting is disabled.", 503)
    device, warning = resolve_device(requested_device)
    try:
      with self._load_lock:
        model = self._models.get(device)
        if model is None:
          self.state = "loading"
          if self.factory is TimesFM3Adapter:
            model = self.factory(self.checkpoint, device, revision=self.checkpoint_revision)
          else:
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
        outputs = list(model.predict_many(contexts, horizon)) if many else [model.predict(contexts[0], horizon)]
        if len(outputs) != len(contexts):
          raise AppError("model_output_invalid", "TimesFM returned invalid output.", 502)
        validated = [validate_output(output.point, output.quantiles, horizon) for output in outputs]
      finally:
        self._inference.release()
      return validated, device, warning
    except AppError as error:
      if self.state == "loading" or error.code in {
        "model_inference_failed",
        "model_output_invalid",
      }:
        self.state = "error"
      raise
    except Exception as error:
      self.state = "error"
      raise AppError("model_load_failed", "TimesFM could not be loaded.", 503) from error

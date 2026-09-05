import hashlib
import json
import sqlite3
from contextlib import closing, nullcontext
from datetime import date
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from ..errors import AppError
from ..model import validate_output
from .contracts import (
  ComponentState,
  InternalTimesFMBatchRequest,
  InternalTimesFMBatchResponse,
  InternalTimesFMRequest,
  InternalTimesFMResponse,
)

SAFE_CODES = {
  "invalid_ticker", "validation_error", "insufficient_history",
  "non_continuous_sessions", "calendar_unavailable", "market_data_unavailable",
  "market_data_empty", "market_data_malformed", "non_finite_prices",
  "model_disabled", "model_load_failed", "model_inference_failed",
  "model_output_invalid", "model_capacity_exceeded", "device_unavailable",
  "internal_inference_unavailable", "internal_unauthorized", "artifact_invalid",
  "insufficient_garch_history", "configuration_changed", "artifact_changed",
}


def safe_code(error: Exception, fallback="component_unavailable") -> str:
  return error.code if isinstance(error, AppError) and error.code in SAFE_CODES else fallback


def digest(value) -> str:
  encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
  return hashlib.sha256(encoded.encode()).hexdigest()


def read_internal_token(settings) -> str:
  try:
    if settings.internal_token_file is None:
      raise ValueError("missing token file")
    with settings.internal_token_file.open(encoding="ascii") as stream:
      token = stream.read(4097).strip()
    if not token or len(token) > 4096 or any(character.isspace() for character in token):
      raise ValueError("invalid token")
    return token
  except (OSError, ValueError, UnicodeError) as error:
    raise AppError(
      "internal_inference_unavailable", "Internal inference is not configured.", 503,
    ) from error


class ManagerTimesFMAdapter:
  def __init__(self, manager, settings):
    self.manager = manager
    self.settings = settings

  @property
  def artifact_id(self):
    return getattr(self.manager, "artifact_id", None)

  def predict(self, context, horizon):
    output, _, _ = self.manager.predict(context, horizon, self.settings.device)
    return output

  def predict_many(self, contexts, horizon):
    outputs, _, _ = self.manager.predict_many(contexts, horizon, self.settings.device)
    return outputs


class HttpTimesFMAdapter:
  def __init__(self, settings, client=None):
    self.settings = settings
    self.client = client
    self._artifact_id = None

  @property
  def artifact_id(self):
    return self._artifact_id

  def predict(self, context, horizon):
    return self._predict([context], horizon, many=False)[0]

  def predict_many(self, contexts, horizon):
    return self._predict(contexts, horizon, many=True)

  def _predict(self, contexts, horizon, *, many):
    self._artifact_id = None
    try:
      request = InternalTimesFMBatchRequest(
        contexts=[np.asarray(context).tolist() for context in contexts], horizon=horizon,
      ) if many else InternalTimesFMRequest(context=np.asarray(contexts[0]).tolist(), horizon=horizon)
    except (ValueError, TypeError) as error:
      raise AppError("validation_error", "Invalid internal inference request.", 422) from error
    token = read_internal_token(self.settings)
    try:
      connection = nullcontext(self.client) if self.client is not None else httpx.Client(
        timeout=httpx.Timeout(120, connect=5), trust_env=False, follow_redirects=False,
      )
      with connection as client:
        response = client.post(
          self.settings.research_http_url + "/api/v2/internal/timesfm" + ("-batch" if many else ""),
          json=request.model_dump(), headers={"Authorization": "Bearer " + token},
        )
      if not response.is_success:
        try:
          code = response.json()["error"]["code"]
        except (ValueError, KeyError, TypeError):
          code = "internal_inference_unavailable"
        if code not in SAFE_CODES:
          code = "internal_inference_unavailable"
        status = response.status_code if 400 <= response.status_code < 600 else 503
        raise AppError(code, "Internal inference request failed.", status)
      schema = InternalTimesFMBatchResponse if many else InternalTimesFMResponse
      document = schema.model_validate(response.json())
      outputs = document.outputs if many else [document]
      if len(outputs) != len(contexts):
        raise ValueError("Invalid batch output count")
      validated = [validate_output(output.point, output.quantiles, horizon) for output in outputs]
      self._artifact_id = document.artifact_id
      return validated
    except AppError:
      raise
    except httpx.HTTPError as error:
      raise AppError("internal_inference_unavailable", "Internal inference is unreachable.", 503) from error
    except (KeyError, ValueError, TypeError) as error:
      raise AppError("model_output_invalid", "Invalid internal inference response.", 502) from error


def latest_snapshot(store, kind: str, **matches):
  with closing(sqlite3.connect(store.db_path.as_uri() + "?mode=ro", uri=True)) as db:
    rows = db.execute(
      "SELECT id, metadata FROM snapshots ORDER BY created_at DESC, rowid DESC"
    )
    for snapshot_id, document in rows:
      metadata = json.loads(document)
      if metadata.get("kind") == kind and all(
        metadata.get(key) == value for key, value in matches.items()
      ):
        return snapshot_id, metadata
  return None


def same_price_basis(first, second):
  adjustment = lambda value: "raw" if value == "unadjusted" else value
  return bool(first.get("source")) and (
    first.get("source") == second.get("source")
    and adjustment(first.get("adjustment")) in {"raw", "qfq"}
    and adjustment(first.get("adjustment")) == adjustment(second.get("adjustment"))
    and all(first.get(key) == second.get(key) for key in
            ("adjustment_revision", "source_version"))
  )


def price_snapshot(store, identifier, ticker, origin):
  metadata = store.snapshot_metadata(identifier)
  frame = store.read_snapshot(identifier)
  if metadata.get("kind") == "bars":
    frame = frame.loc[frame.ticker == ticker].rename(columns={"close": "price"})
  elif metadata.get("kind") != "prices" or metadata.get("ticker") != ticker:
    raise ValueError("price_snapshot_mismatch")
  prices = frame.assign(date=pd.to_datetime(frame.date)).set_index("date").price.sort_index()
  prices = prices.loc[:origin]
  if (prices.empty or not prices.index.is_unique or not np.isfinite(prices.to_numpy(float)).all()
      or (prices <= 0).any()):
    raise ValueError("price_snapshot_invalid")
  return prices, metadata


class PredictionRuntime:
  def __init__(self, store):
    self.store = store

  def directory(self, *parts):
    directory = self.store.root / "models" / Path(*parts)
    if (not directory.resolve().is_relative_to(self.store.root / "models")
        or any(path.is_symlink() for path in (directory, *directory.parents)
               if path.is_relative_to(self.store.root))):
      raise AppError("artifact_invalid", "Unsafe artifact directory.", 503)
    for name in ("manifest.json", "regressor.txt", "classifier.txt"):
      path = directory / name
      if path.is_symlink():
        raise AppError("artifact_invalid", "Unsafe artifact file.", 503)
    return directory

  def revision(self, ticker):
    paths = [self.directory("garch", ticker)]
    paths += [self.directory("lightgbm", str(value)) for value in (1, 5, 20)]
    files = {}
    for directory in paths:
      for name in ("manifest.json", "regressor.txt", "classifier.txt"):
        path = directory / name
        if path.is_file():
          files[str(path.relative_to(self.store.root))] = hashlib.sha256(
            path.read_bytes()
          ).hexdigest()
    snapshot = latest_snapshot(
      self.store, "features", ticker=ticker, trusted=True, point_in_time=True,
    )
    files["trusted_features"] = snapshot[0] if snapshot else None
    return digest(files)

  def _context(self, context, ticker, origin):
    if not context or not context.get("snapshot_id"):
      raise ValueError("inference_provenance_required")
    prices, basis = price_snapshot(self.store, context["snapshot_id"], ticker, origin)
    if prices.index[-1] != pd.Timestamp(origin):
      raise ValueError("inference_origin_mismatch")
    return prices, basis

  def training_provenance(self, ticker, origin):
    result = {"lightgbm": {}, "garch": {}}
    for kind, key in [("lightgbm", str(value)) for value in (1, 5, 20)] + [("garch", ticker)]:
      try:
        directory = self.directory(kind, key)
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        artifact_id = manifest.pop("artifact_id")
        if digest(manifest) != artifact_id:
          continue
        files = manifest.get("files", {})
        if kind == "lightgbm" and set(files) != {"regressor.txt", "classifier.txt"}:
          continue
        if any(hashlib.sha256((directory / name).read_bytes()).hexdigest() != expected
               for name, expected in files.items()):
          continue
        metadata = manifest["metadata"]
        refs = metadata.get("dataset_ids", {})
        if kind == "garch":
          refs = {"prices": metadata.get("snapshot_id")}
        info = {"artifact_id": artifact_id, "training_refs": refs,
                "trained_through": metadata.get("trained_through"),
                "feature_schema": metadata.get("feature_schema")}
        prior = metadata.get("training_up_prior")
        if (kind == "lightgbm" and type(prior) in (int, float) and 0 <= prior <= 1
            and metadata.get("feature_schema") and metadata.get("trained_through")
            and date.fromisoformat(metadata["trained_through"]) <= date.fromisoformat(origin)):
          info["training_up_prior"] = prior
        result[kind][key] = info
      except (OSError, ValueError, KeyError, TypeError):
        continue
    return result

  def _training_basis(self, metadata):
    reference = metadata.get("dataset_ids", {}).get("bars")
    if reference:
      basis = self.store.snapshot_metadata(reference)
      declared = {**basis, **{key: metadata[key] for key in (
        "source", "adjustment", "adjustment_revision", "source_version",
      ) if key in metadata}}
      if not same_price_basis(basis, declared):
        raise ValueError("training_basis_mismatch")
      return basis
    return metadata

  def lightgbm(self, ticker, origin, instrument_type, context=None):
    if instrument_type == "index":
      return ComponentState(status="not_supported", reason="stock_models_only"), {}
    predictions, artifacts, failures = {}, {}, {}
    unavailable = False
    for horizon in (1, 5, 20):
      try:
        directory = self.directory("lightgbm", str(horizon))
        if not (directory / "manifest.json").is_file():
          failures[horizon] = "artifact_missing"
          continue
        from .gradient_boosting import GradientBoostingModel

        model = GradientBoostingModel.load(directory)
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        artifacts[str(horizon)] = manifest["artifact_id"]
        schema = model.metadata.get("feature_schema")
        if model.horizon != horizon:
          raise ValueError("artifact horizon differs")
        if not schema:
          failures[horizon] = "feature_schema_missing"
          continue
        trained = model.metadata.get("trained_through")
        if not trained:
          failures[horizon] = "trained_through_required"
          continue
        if date.fromisoformat(trained) > date.fromisoformat(origin):
          failures[horizon] = "artifact_after_origin"
          continue
        snapshot = latest_snapshot(
          self.store, "features", ticker=ticker, origin=origin,
          feature_schema=schema, trusted=True, point_in_time=True,
        )
        if snapshot is None:
          failures[horizon] = "trusted_features_required"
          continue
        prices, basis = self._context(context, ticker, origin)
        if not same_price_basis(self._training_basis(model.metadata), basis):
          raise ValueError("training_basis_mismatch")
        feature_meta = snapshot[1]
        reference = (feature_meta.get("price_snapshot_id") or feature_meta.get("bars_snapshot_id")
                     or feature_meta.get("dataset_ids", {}).get("bars"))
        if not reference:
          raise ValueError("feature_price_reference_required")
        feature_prices, feature_basis = price_snapshot(self.store, reference, ticker, origin)
        declared = {**feature_basis, **{key: feature_meta[key] for key in (
          "source", "adjustment", "adjustment_revision", "source_version",
        ) if key in feature_meta}}
        if (not same_price_basis(basis, feature_basis) or not same_price_basis(basis, declared)
            or not prices.index.isin(feature_prices.index).all()
            or not np.allclose(prices.to_numpy(float), feature_prices.reindex(prices.index).to_numpy(float),
                               rtol=1e-12, atol=1e-12)):
          raise ValueError("feature_price_basis_mismatch")
        features = self.store.read_snapshot(snapshot[0])
        if len(features) != 1:
          failures[horizon] = "feature_snapshot_invalid"
          continue
        returns, probability = model.predict(features)
        if not 0 <= probability[0] <= 1:
          raise ValueError("invalid probability")
        predictions[horizon] = (float(returns[0]), float(probability[0]))
      except ImportError:
        failures[horizon] = "dependency_unavailable"
        unavailable = True
      except Exception:
        failures[horizon] = "artifact_or_features_invalid"
        unavailable = True
    return ComponentState(
      status="unavailable" if unavailable else "not_ready" if failures else "ready",
      reason=";".join(f"{key}:{value}" for key, value in failures.items()) or None,
      artifact_id=("sha256:" + digest(artifacts)) if artifacts else None,
    ), predictions

  def garch(self, ticker, origin, returns, context=None):
    try:
      directory = self.directory("garch", ticker)
      if not (directory / "manifest.json").is_file():
        return ComponentState(status="not_ready", reason="artifact_missing"), {}
      from .volatility import GarchModel

      model = GarchModel.load(directory)
      manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
      artifact_id = manifest["artifact_id"]
      fitted_origin = model.metadata.get("origin")
      trained = model.metadata.get("trained_through")
      reference = model.metadata.get("snapshot_id")
      if model.metadata.get("ticker") != ticker or not fitted_origin or not trained or not reference:
        return ComponentState(
          status="not_ready", reason="artifact_provenance_missing", artifact_id=artifact_id,
        ), {}
      if max(date.fromisoformat(fitted_origin), date.fromisoformat(trained)) > date.fromisoformat(origin):
        return ComponentState(
          status="not_ready", reason="artifact_after_origin", artifact_id=artifact_id,
        ), {}
      prices, basis = self._context(context, ticker, origin)
      if not same_price_basis(model.metadata, basis):
        raise ValueError("fit_basis_mismatch")
      live_returns = np.diff(np.log(prices.to_numpy(float)))
      if len(live_returns) != len(returns) or not np.allclose(live_returns, returns, rtol=1e-7, atol=1e-12):
        raise ValueError("inference_returns_mismatch")
      try:
        fitted, fit_basis = price_snapshot(self.store, reference, ticker, fitted_origin)
      except (KeyError, ValueError) as error:
        if isinstance(error, ValueError) and not isinstance(error.__cause__, OSError):
          raise
        return ComponentState(
          status="not_ready", reason="fit_snapshot_unavailable", artifact_id=artifact_id,
        ), {}
      if fitted.index[-1] != pd.Timestamp(fitted_origin) or not same_price_basis(fit_basis, basis):
        raise ValueError("fit_snapshot_mismatch")
      common = fitted.index.intersection(prices.index)
      if len(common) < 2 or not np.allclose(
        np.diff(np.log(fitted.loc[common].to_numpy(float))),
        np.diff(np.log(prices.loc[common].to_numpy(float))), rtol=1e-9, atol=1e-12,
      ):
        raise ValueError("fit_return_basis_mismatch")
      if len(returns) < 252:
        return ComponentState(
          status="not_ready", reason="insufficient_garch_history", artifact_id=artifact_id,
        ), {}
      forecast = model.forecast(np.asarray(returns)[-512:], horizon=20)
      return ComponentState(status="ready", artifact_id=artifact_id), {
        horizon: forecast["cumulative_volatility"][str(horizon)] for horizon in (1, 5, 20)
      }
    except ImportError:
      return ComponentState(status="unavailable", reason="dependency_unavailable"), {}
    except Exception:
      return ComponentState(status="unavailable", reason="artifact_invalid"), {}
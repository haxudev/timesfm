"""Unweighted paired-sample metrics; callers own date/horizon grouping and coverage."""

import numpy as np
from sklearn.metrics import roc_auc_score


def _pairs(actual, predicted) -> tuple[np.ndarray, np.ndarray]:
  observed = np.asarray(actual, dtype=float)
  forecast = np.asarray(predicted, dtype=float)
  if (
    observed.ndim != 1
    or observed.size == 0
    or forecast.shape != observed.shape
    or not np.isfinite(observed).all()
    or not np.isfinite(forecast).all()
  ):
    raise ValueError("invalid_metric_input: matching finite nonempty vectors required")
  return observed, forecast


def return_metrics(actual, predicted) -> dict:
  """MAE/RMSE of decimal simple returns; direction treats zero as non-up."""
  observed, forecast = _pairs(actual, predicted)
  error = observed - forecast
  return {
    "n": len(observed),
    "mae": float(np.abs(error).mean()),
    "rmse": float(np.sqrt(np.square(error).mean())),
    "direction_accuracy": float(((observed > 0) == (forecast > 0)).mean()),
  }


def probability_metrics(actual_binary, probability) -> dict:
  """Brier uses raw probabilities; log loss clips to [1e-12, 1-1e-12].

  AUC is None for one-class evaluation samples (not a fabricated 0.5).
  """
  observed, forecast = _pairs(actual_binary, probability)
  if not np.isin(observed, [0, 1]).all() or ((forecast < 0) | (forecast > 1)).any():
    raise ValueError(
      "invalid_probability_input: binary labels and probabilities required"
    )
  clipped = np.clip(forecast, 1e-12, 1 - 1e-12)
  return {
    "n": len(observed),
    "brier": float(np.square(observed - forecast).mean()),
    "log_loss": float(
      -(observed * np.log(clipped) + (1 - observed) * np.log1p(-clipped)).mean()
    ),
    "auc": float(roc_auc_score(observed, forecast))
    if len(np.unique(observed)) == 2
    else None,
    "probability_clip": 1e-12,
  }


def volatility_metrics(realized_variance, predicted_variance) -> dict:
  """Variance MAE/RMSE and normalized QLIKE = mean(r - log(r) - 1).

  Both variances are floored at 1e-12 ONLY for QLIKE, r = actual/predicted.
  This makes zero-realized-variance sessions explicit and finite; errors use
  original nonnegative decimal-squared inputs. Negative values are rejected.
  """
  observed, forecast = _pairs(realized_variance, predicted_variance)
  if (observed < 0).any() or (forecast < 0).any():
    raise ValueError("invalid_variance_input: variance cannot be negative")
  ratio = np.maximum(observed, 1e-12) / np.maximum(forecast, 1e-12)
  error = observed - forecast
  return {
    "n": len(observed),
    "mae": float(np.abs(error).mean()),
    "rmse": float(np.sqrt(np.square(error).mean())),
    "qlike": float((ratio - np.log(ratio) - 1).mean()),
    "variance_floor": 1e-12,
  }

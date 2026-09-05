"""Stationary Student-t GARCH(1,1) with explicit offline fitting and fixed inference."""

import hashlib
import json
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

import numpy as np
from arch import arch_model

_PARAMETERS = ("omega", "alpha[1]", "beta[1]", "nu")


def _canonical(value: dict) -> str:
  return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _context(returns: np.ndarray) -> np.ndarray:
  values = np.asarray(returns, dtype=float)
  if values.ndim != 1 or not np.isfinite(values).all():
    raise ValueError(
      "invalid_garch_returns: finite one-dimensional log returns required"
    )
  if len(values) < 252:
    raise ValueError(
      "insufficient_garch_history: at least 252 complete returns required"
    )
  return values[-512:] * 100


def _arch(scaled: np.ndarray):
  return arch_model(
    scaled, mean="Zero", vol="GARCH", p=1, o=0, q=1, dist="StudentsT", rescale=False
  )


@dataclass(frozen=True)
class GarchModel:
  """Parameters are in percentage-return units; public variances are decimal^2.

  Caller supplies consecutive, completed daily DECIMAL LOG returns, already
  cut at origin and validated against its exchange calendar. Arrays cannot
  prove dates or provenance. At least 252 returns, most recent 512 used.
  Metadata is copied as a JSON object; publication and snapshots are external.
  """

  parameters: dict[str, float]
  metadata: dict

  def __post_init__(self):
    if set(self.parameters) != set(_PARAMETERS):
      raise ValueError("invalid_garch_parameters: omega/alpha[1]/beta[1]/nu required")
    try:
      parameters = {name: float(self.parameters[name]) for name in _PARAMETERS}
    except (ValueError, TypeError) as error:
      raise ValueError(
        "invalid_garch_parameters: numeric parameters required"
      ) from error
    if (
      not np.isfinite(list(parameters.values())).all()
      or parameters["omega"] <= 0
      or parameters["alpha[1]"] < 0
      or parameters["beta[1]"] < 0
      or parameters["alpha[1]"] + parameters["beta[1]"] >= 1
      or parameters["nu"] <= 2
    ):
      raise ValueError(
        "invalid_garch_parameters: positive finite stationary variance required"
      )
    if not isinstance(self.metadata, dict):
      raise TypeError("invalid_metadata: JSON object required")
    object.__setattr__(self, "parameters", parameters)
    object.__setattr__(self, "metadata", json.loads(_canonical(self.metadata)))

  @classmethod
  def fit(cls, returns: np.ndarray, metadata: dict) -> "GarchModel":
    """Offline optimization only; no fallback if convergence or stationarity fails."""
    scaled = _context(returns)
    if np.var(scaled) <= 0:
      raise ValueError("invalid_garch_returns: cannot fit constant returns")
    fitted = _arch(scaled).fit(disp="off")
    if fitted.convergence_flag != 0:
      raise ValueError(
        f"garch_nonconvergence: optimizer status {fitted.convergence_flag}"
      )
    model = cls({name: float(fitted.params[name]) for name in _PARAMETERS}, metadata)
    model.forecast(returns, horizon=1)
    return model

  def forecast(self, returns: np.ndarray, horizon: int = 20) -> dict:
    """Re-filter the recent context with fixed parameters, never run an optimizer.

    step_variance contains h=1..horizon in decimal^2. Aggregates include
    1/5/20 when <= horizon, plus horizon itself, using string keys. Cumulative
    variance sums steps; cumulative volatility is its square root (decimal,
    not annualized). These are risk estimates, not return/probability signals.
    """
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 1:
      raise ValueError("invalid_horizon: positive integer required")
    scaled = _context(returns)
    fixed = _arch(scaled).fix([self.parameters[name] for name in _PARAMETERS])
    values = fixed.forecast(horizon=horizon, method="analytic", reindex=False)
    steps = values.variance.iloc[-1].to_numpy(dtype=float) / 10000
    if steps.shape != (horizon,) or not np.isfinite(steps).all() or (steps <= 0).any():
      raise ValueError("invalid_garch_variance: forecast must be finite and positive")
    horizons = sorted({item for item in (1, 5, 20, horizon) if item <= horizon})
    variances = {str(item): float(steps[:item].sum()) for item in horizons}
    return {
      "step_variance": steps.tolist(),
      "cumulative_variance": variances,
      "cumulative_volatility": {
        key: float(np.sqrt(value)) for key, value in variances.items()
      },
    }

  def save(self, directory: str | Path) -> str:
    """Write canonical manifest.json containing native numeric params; return SHA256.

    No pickle, histories or fitted optimizer objects. Do not overwrite artifacts.
    Integrity hashes do not authenticate authors; caller supplies trusted paths.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "manifest.json"
    if path.exists():
      raise FileExistsError("artifact_exists: use a new artifact directory")
    manifest = {
      "schema_version": 1,
      "model_type": "zero_mean_garch_1_1_student_t",
      "parameters": self.parameters,
      "metadata": self.metadata,
      "scale": 100,
      "rescale": False,
      "minimum_observations": 252,
      "context_limit": 512,
      "return_unit": "decimal_log",
      "variance_unit": "decimal_squared",
      "arch_version": version("arch"),
    }
    artifact_id = hashlib.sha256(_canonical(manifest).encode()).hexdigest()
    manifest["artifact_id"] = artifact_id
    path.write_text(_canonical(manifest), encoding="utf-8")
    return artifact_id

  @classmethod
  def load(cls, directory: str | Path) -> "GarchModel":
    """Validate manifest hash, model/units and stationary parameters without fitting."""
    path = Path(directory) / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    artifact_id = manifest.pop("artifact_id", None)
    if hashlib.sha256(_canonical(manifest).encode()).hexdigest() != artifact_id:
      raise ValueError("artifact_hash_mismatch: manifest modified")
    expected = {
      "schema_version": 1,
      "model_type": "zero_mean_garch_1_1_student_t",
      "scale": 100,
      "rescale": False,
      "minimum_observations": 252,
      "context_limit": 512,
      "return_unit": "decimal_log",
      "variance_unit": "decimal_squared",
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
      raise ValueError("invalid_artifact_schema: unsupported GARCH model or units")
    try:
      return cls(manifest["parameters"], manifest["metadata"])
    except (KeyError, TypeError) as error:
      raise ValueError(
        "invalid_artifact_schema: missing or malformed fields"
      ) from error

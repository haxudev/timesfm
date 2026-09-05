import hashlib
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from arch import arch_model
from arch.univariate import ZeroMean


def returns_fixture():
  random = np.random.default_rng(871)
  innovations = random.standard_t(8, 1100) * np.sqrt(6 / 8)
  values = np.zeros(1100)
  variance = 1.0
  for position in range(1, len(values)):
    variance = 0.02 + 0.08 * values[position - 1] ** 2 + 0.88 * variance
    values[position] = np.sqrt(variance) * innovations[position]
  return values[-700:] / 100


def fixed_model(**changes):
  from stock_forecaster.research.volatility import GarchModel

  parameters = {"omega": 0.02, "alpha[1]": 0.1, "beta[1]": 0.85, "nu": 8.0}
  parameters.update(changes)
  return GarchModel(parameters=parameters, metadata={"snapshot_id": "synthetic"})


def test_fixed_forecast_matches_hand_recursion_and_decimal_units(monkeypatch):
  model = fixed_model()

  def forbidden(*args, **kwargs):
    raise AssertionError("forecast must not optimize parameters")

  monkeypatch.setattr(ZeroMean, "fit", forbidden)
  result = model.forecast(np.zeros(512))
  steps = np.array(result["step_variance"])
  assert len(steps) == 20
  assert steps[0] == pytest.approx(0.00001333333333333333, rel=1e-9)
  assert steps[1] == pytest.approx(0.000014666666666666667, rel=1e-9)
  np.testing.assert_allclose(steps[1:], 0.02 / 10000 + 0.95 * steps[:-1], rtol=1e-12)
  assert set(result["cumulative_variance"]) == {"1", "5", "20"}
  for horizon in (1, 5, 20):
    assert result["cumulative_variance"][str(horizon)] == pytest.approx(
      steps[:horizon].sum()
    )
    assert result["cumulative_volatility"][str(horizon)] == pytest.approx(
      np.sqrt(steps[:horizon].sum())
    )
  shorter = model.forecast(np.zeros(512), horizon=3)
  assert set(shorter["cumulative_variance"]) == {"1", "3"}
  assert "return" not in result and "probability" not in result


def test_real_fit_matches_last_512_scaled_zero_mean_student_t():
  from stock_forecaster.research.volatility import GarchModel

  returns = returns_fixture()
  model = GarchModel.fit(returns, {"snapshot_id": "fixture"})
  reference = arch_model(
    100 * returns[-512:],
    mean="Zero",
    vol="GARCH",
    p=1,
    q=1,
    dist="StudentsT",
    rescale=False,
  ).fit(disp="off")
  assert reference.convergence_flag == 0
  for name, expected in reference.params.items():
    assert model.parameters[name] == pytest.approx(expected, rel=1e-10)
  changed = returns.copy()
  changed[:-512] = 0.9
  np.testing.assert_allclose(
    model.forecast(returns)["step_variance"],
    model.forecast(changed)["step_variance"],
    atol=0,
  )
  assert np.isfinite(model.forecast(returns)["step_variance"]).all()


@pytest.mark.parametrize(
  "returns",
  [
    np.zeros(251),
    np.zeros((252, 1)),
    np.r_[np.zeros(251), np.nan],
    np.r_[np.zeros(251), np.inf],
  ],
)
def test_requires_complete_minimum_history(returns):
  from stock_forecaster.research.volatility import GarchModel

  with pytest.raises(
    ValueError, match="insufficient_garch_history|invalid_garch_returns"
  ):
    GarchModel.fit(returns, {})
  with pytest.raises(
    ValueError, match="insufficient_garch_history|invalid_garch_returns"
  ):
    fixed_model().forecast(returns)


@pytest.mark.parametrize(
  "changes",
  [
    {"omega": 0},
    {"omega": -1},
    {"alpha[1]": -0.1},
    {"beta[1]": -0.1},
    {"alpha[1]": 0.2, "beta[1]": 0.8},
    {"nu": 2},
    {"nu": np.inf},
  ],
)
def test_invalid_parameters_are_not_publishable(changes):
  with pytest.raises(ValueError, match="invalid_garch_parameters"):
    fixed_model(**changes)


def test_failed_optimizer_is_rejected(monkeypatch):
  from stock_forecaster.research.volatility import GarchModel

  monkeypatch.setattr(
    ZeroMean, "fit", lambda *args, **kwargs: SimpleNamespace(convergence_flag=9)
  )
  with pytest.raises(ValueError, match="garch_nonconvergence"):
    GarchModel.fit(returns_fixture(), {})


def test_optimizer_output_still_passes_stationarity_gate(monkeypatch):
  from stock_forecaster.research.volatility import GarchModel

  params = pd.Series({"omega": 0.02, "alpha[1]": 0.2, "beta[1]": 0.8, "nu": 8})
  monkeypatch.setattr(
    ZeroMean,
    "fit",
    lambda *args, **kwargs: SimpleNamespace(convergence_flag=0, params=params),
  )
  with pytest.raises(ValueError, match="invalid_garch_parameters"):
    GarchModel.fit(returns_fixture(), {})


def test_json_roundtrip_and_hash_rejects_changed_parameters(tmp_path):
  from stock_forecaster.research.volatility import GarchModel

  model = fixed_model()
  artifact_id = model.save(tmp_path)
  path = tmp_path / "manifest.json"
  manifest = json.loads(path.read_text())
  assert manifest.pop("artifact_id") == artifact_id
  canonical = json.dumps(
    manifest, sort_keys=True, separators=(",", ":"), allow_nan=False
  )
  assert hashlib.sha256(canonical.encode()).hexdigest() == artifact_id
  loaded = GarchModel.load(tmp_path)
  assert loaded.parameters == model.parameters
  assert loaded.metadata == model.metadata
  np.testing.assert_array_equal(
    loaded.forecast(returns_fixture())["step_variance"],
    model.forecast(returns_fixture())["step_variance"],
  )
  assert list(tmp_path.iterdir()) == [path]
  with pytest.raises(FileExistsError):
    model.save(tmp_path)
  manifest["artifact_id"] = artifact_id
  manifest["parameters"]["omega"] = 10
  path.write_text(json.dumps(manifest))
  with pytest.raises(ValueError, match="artifact_hash_mismatch"):
    GarchModel.load(tmp_path)


@pytest.mark.parametrize("horizon", [0, -1, True, 1.5])
def test_invalid_forecast_horizon(horizon):
  with pytest.raises(ValueError, match="invalid_horizon"):
    fixed_model().forecast(np.zeros(512), horizon=horizon)

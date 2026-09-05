import hashlib
import json

import numpy as np
import pandas as pd
import pytest
from scipy.special import expit, logit
from sklearn.linear_model import LogisticRegression


def training_fixture():
  random = np.random.default_rng(1729)
  frames = []
  targets = []
  for count in (100, 40, 60):
    values = random.normal(size=count)
    frames.append(
      pd.DataFrame({"momentum_5": values, "industry": np.where(values > 0, "X", "Y")})
    )
    targets.append(0.02 * values + random.normal(0, 0.01, count))
  frames[2].loc[0, "industry"] = "CALIBRATION_ONLY"
  return frames, targets


def fit_small(frames, targets, **kwargs):
  from stock_forecaster.research.gradient_boosting import GradientBoostingModel

  return GradientBoostingModel.train(
    frames[0],
    targets[0],
    frames[1],
    targets[1],
    frames[2],
    targets[2],
    horizon=5,
    metadata={"snapshot_id": "fixture-only", "research_only": True},
    n_estimators=12,
    num_threads=1,
    **kwargs,
  )


def test_real_small_models_calibrate_only_on_calibration_logits():
  frames, targets = training_fixture()
  model = fit_small(frames, targets)
  predicted, probability = model.predict(frames[2])
  assert predicted.shape == probability.shape == (60,)
  assert np.isfinite(predicted).all()
  assert ((probability >= 0) & (probability <= 1)).all()
  assert model.categorical_vocabulary == {"industry": ["X", "Y"]}
  calibration_frame = frames[2].copy()
  calibration_frame["industry"] = pd.Categorical(
    calibration_frame.industry, categories=["X", "Y"]
  )
  raw = model.classifier.predict(calibration_frame, num_threads=1)
  logits = logit(np.clip(raw, 1e-6, 1 - 1e-6)).reshape(-1, 1)
  expected = LogisticRegression().fit(logits, (targets[2] > 0).astype(int))
  np.testing.assert_allclose(
    probability, expected.predict_proba(logits)[:, 1], atol=1e-12
  )
  np.testing.assert_allclose(
    probability,
    expit(
      logits[:, 0] * model.calibration["coefficient"] + model.calibration["intercept"]
    ),
  )
  changed = [*targets[:2], -targets[2]]
  inverted = fit_small(frames, changed)
  np.testing.assert_allclose(predicted, inverted.predict(frames[2])[0], atol=0)
  np.testing.assert_allclose(inverted.predict(frames[2])[1], 1 - probability, atol=1e-6)


@pytest.mark.parametrize("part", [0, 2])
def test_one_class_train_or_calibration_fails_before_publication(part):
  frames, targets = training_fixture()
  targets[part] = np.ones(len(targets[part])) * 0.01
  with pytest.raises(ValueError, match="one_class_training|one_class_calibration"):
    fit_small(frames, targets)


def test_zero_return_is_not_an_up_label():
  frames, targets = training_fixture()
  targets[0] = np.zeros(len(targets[0]))
  with pytest.raises(ValueError, match="one_class_training"):
    fit_small(frames, targets)


def test_schema_reordering_unknown_category_and_forbidden_targets():
  frames, targets = training_fixture()
  model = fit_small(frames, targets)
  expected = model.predict(frames[1])
  reordered = model.predict(frames[1][["industry", "momentum_5"]])
  for actual, wanted in zip(reordered, expected):
    np.testing.assert_array_equal(actual, wanted)
  with pytest.raises(ValueError, match="feature_schema_mismatch"):
    model.predict(frames[1].drop(columns="industry"))
  unseen = frames[1].copy()
  unseen["industry"] = "UNSEEN"
  assert np.isfinite(model.predict(unseen)[1]).all()
  frames[0]["return"] = targets[0]
  with pytest.raises(ValueError, match="label_in_features"):
    fit_small(frames, targets)


@pytest.mark.parametrize("problem", ["nan_target", "infinity", "misaligned_target"])
def test_bad_training_inputs_rejected(problem):
  frames, targets = training_fixture()
  if problem == "nan_target":
    targets[1][0] = np.nan
  elif problem == "infinity":
    frames[0].loc[0, "momentum_5"] = np.inf
  else:
    targets[0] = pd.Series(targets[0], index=np.arange(100)[::-1])
  with pytest.raises(
    ValueError, match="invalid_returns|invalid_features|target_index_mismatch"
  ):
    fit_small(frames, targets)


def test_native_roundtrip_hashes_and_read_only_prediction(tmp_path, monkeypatch):
  from stock_forecaster.research.gradient_boosting import GradientBoostingModel

  frames, targets = training_fixture()
  model = fit_small(frames, targets)
  expected = model.predict(frames[1])
  directory = tmp_path / "artifact"
  artifact_id = model.save(directory)
  manifest = json.loads((directory / "manifest.json").read_text())
  assert manifest["horizon"] == 5
  assert manifest["columns"] == ["momentum_5", "industry"]
  assert manifest["metadata"]["snapshot_id"] == "fixture-only"
  assert manifest["categorical_vocabulary"] == {"industry": ["X", "Y"]}
  assert manifest.pop("artifact_id") == artifact_id
  canonical = json.dumps(
    manifest, sort_keys=True, separators=(",", ":"), allow_nan=False
  )
  assert hashlib.sha256(canonical.encode()).hexdigest() == artifact_id
  assert {path.suffix for path in directory.iterdir()} == {".txt", ".json"}
  assert model.save(tmp_path / "same-content") == artifact_id

  def forbidden(*args, **kwargs):
    raise AssertionError("inference must not fit or train")

  monkeypatch.setattr(GradientBoostingModel, "train", forbidden)
  monkeypatch.setattr(LogisticRegression, "fit", forbidden)
  loaded = GradientBoostingModel.load(directory)
  for actual, wanted in zip(loaded.predict(frames[1]), expected):
    np.testing.assert_allclose(actual, wanted, atol=1e-12)
  with pytest.raises(FileExistsError):
    loaded.save(directory)
  model_file = directory / "regressor.txt"
  model_file.write_text(model_file.read_text() + "\ntampered")
  with pytest.raises(ValueError, match="artifact_hash_mismatch"):
    GradientBoostingModel.load(directory)


def test_manifest_metadata_tampering_rejected(tmp_path):
  from stock_forecaster.research.gradient_boosting import GradientBoostingModel

  frames, targets = training_fixture()
  fit_small(frames, targets).save(tmp_path)
  path = tmp_path / "manifest.json"
  manifest = json.loads(path.read_text())
  manifest["horizon"] = 20
  path.write_text(json.dumps(manifest))
  with pytest.raises(ValueError, match="artifact_hash_mismatch"):
    GradientBoostingModel.load(tmp_path)

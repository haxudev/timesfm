"""Offline LightGBM return/event training and read-only native artifact inference."""

import hashlib
import json
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.special import expit, logit
from sklearn.linear_model import LogisticRegression


def _canonical(value: dict) -> str:
  return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _columns(frame: pd.DataFrame) -> list[str]:
  forbidden = {"return", "returns", "up", "realized_variance", "target_date", "label"}
  if any(str(column).lower() in forbidden for column in frame.columns):
    raise ValueError("label_in_features: target columns cannot enter a model")
  if (
    frame.empty
    or not frame.columns.is_unique
    or any(not isinstance(column, str) for column in frame.columns)
  ):
    raise ValueError(
      "invalid_features: unique string columns and nonempty rows required"
    )
  return list(frame.columns)


def _matrix(frame: pd.DataFrame, columns: list[str], vocabulary: dict) -> pd.DataFrame:
  if set(_columns(frame)) != set(columns):
    raise ValueError("feature_schema_mismatch: expected exactly the trained columns")
  result = frame.loc[:, columns].copy()
  for column in columns:
    if column in vocabulary:
      values = result[column]
      if any(not isinstance(value, str) for value in values.dropna()):
        raise ValueError(f"invalid_features: {column} must contain strings or null")
      result[column] = pd.Categorical(values, categories=vocabulary[column])
    else:
      try:
        result[column] = pd.to_numeric(result[column], errors="raise").astype(float)
      except (TypeError, ValueError) as error:
        raise ValueError(f"invalid_features: {column} must be numeric") from error
      if np.isinf(result[column].to_numpy()).any():
        raise ValueError(f"invalid_features: {column} contains infinity")
  return result


def _returns(values, frame: pd.DataFrame) -> np.ndarray:
  if isinstance(values, pd.Series) and not values.index.equals(frame.index):
    raise ValueError("target_index_mismatch: target Series must match feature index")
  result = np.asarray(values, dtype=float)
  if (
    result.shape != (len(frame),)
    or not np.isfinite(result).all()
    or (result < -1).any()
  ):
    raise ValueError("invalid_returns: finite aligned decimal simple returns required")
  return result


@dataclass(frozen=True)
class GradientBoostingModel:
  """One horizon, two native Boosters, and a calibration-only sigmoid.

  Training is an explicit offline operation. Caller owns chronological/purged
  splits, PIT feature provenance, snapshot metadata and artifact publication.
  Feature preprocessing never learns from validation or calibration. Numeric
  NaN and unseen categories use native LightGBM missing handling, not imputation.
  """

  regressor: lgb.Booster
  classifier: lgb.Booster
  feature_columns: list[str]
  categorical_vocabulary: dict[str, list[str]]
  calibration: dict[str, float]
  horizon: int
  metadata: dict
  num_threads: int = 2

  @classmethod
  def train(
    cls,
    train_x: pd.DataFrame,
    train_returns,
    valid_x: pd.DataFrame,
    valid_returns,
    calibration_x: pd.DataFrame,
    calibration_returns,
    horizon: int,
    metadata: dict,
    *,
    n_estimators: int = 100,
    num_threads: int = 2,
  ) -> "GradientBoostingModel":
    """Fit cumulative simple returns and event return>0; calibrate on held-out data.

    Validation alone drives early stopping. LogisticRegression uses the clipped
    raw probability logit (clip=1e-6), fitted ONLY on calibration. One-class
    training or calibration is an error. num_threads is capped at four;
    n_estimators can be reduced for deterministic, tiny fixture tests.
    Metadata must be JSON-compatible; it is copied, not inferred or invented.
    """
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 1:
      raise ValueError("invalid_horizon: positive integer required")
    if (
      isinstance(n_estimators, bool)
      or not isinstance(n_estimators, int)
      or not 1 <= n_estimators <= 10000
    ):
      raise ValueError("invalid_n_estimators: expected integer in [1,10000]")
    if (
      isinstance(num_threads, bool)
      or not isinstance(num_threads, int)
      or num_threads < 1
    ):
      raise ValueError("invalid_num_threads: positive integer required")
    if not isinstance(metadata, dict):
      raise TypeError("invalid_metadata: JSON object required")
    metadata = json.loads(_canonical(metadata))
    columns = _columns(train_x)
    vocabulary = {}
    for column in columns:
      if not pd.api.types.is_numeric_dtype(train_x[column]):
        values = train_x[column].dropna()
        if any(not isinstance(value, str) for value in values):
          raise ValueError(f"invalid_features: category {column} must contain strings")
        vocabulary[column] = sorted(values.unique().tolist())
    train = _matrix(train_x, columns, vocabulary)
    valid = _matrix(valid_x, columns, vocabulary)
    calibration_frame = _matrix(calibration_x, columns, vocabulary)
    train_target = _returns(train_returns, train_x)
    valid_target = _returns(valid_returns, valid_x)
    calibration_target = _returns(calibration_returns, calibration_x)
    if len(np.unique(train_target > 0)) != 2:
      raise ValueError(
        "one_class_training: both up and non-up training labels required"
      )
    if len(np.unique(calibration_target > 0)) != 2:
      raise ValueError(
        "one_class_calibration: both up and non-up held-out labels required"
      )
    threads = min(num_threads, 4)
    parameters = {
      "n_estimators": n_estimators,
      "num_leaves": 7,
      "max_depth": 3,
      "learning_rate": 0.05,
      "min_child_samples": 5,
      "n_jobs": threads,
      "random_state": 1729,
      "verbosity": -1,
      "deterministic": True,
      "force_col_wise": True,
    }
    regressor = lgb.LGBMRegressor(objective="regression", **parameters)
    classifier = lgb.LGBMClassifier(objective="binary", **parameters)
    regressor.fit(
      train,
      train_target,
      eval_set=[(valid, valid_target)],
      callbacks=[lgb.early_stopping(10, verbose=False)],
    )
    classifier.fit(
      train,
      (train_target > 0).astype(int),
      eval_set=[(valid, (valid_target > 0).astype(int))],
      callbacks=[lgb.early_stopping(10, verbose=False)],
    )
    raw = classifier.booster_.predict(calibration_frame, num_threads=threads)
    logits = logit(np.clip(raw, 1e-6, 1 - 1e-6)).reshape(-1, 1)
    calibrator = LogisticRegression().fit(logits, (calibration_target > 0).astype(int))
    calibration = {
      "coefficient": float(calibrator.coef_[0, 0]),
      "intercept": float(calibrator.intercept_[0]),
      "clip": 1e-6,
    }
    return cls(
      regressor.booster_,
      classifier.booster_,
      columns,
      vocabulary,
      calibration,
      horizon,
      metadata,
      threads,
    )

  def predict(self, features: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Return (decimal cumulative returns, calibrated P(return>0)), in row order."""
    matrix = _matrix(features, self.feature_columns, self.categorical_vocabulary)
    returns = np.asarray(self.regressor.predict(matrix, num_threads=self.num_threads))
    raw = self.classifier.predict(matrix, num_threads=self.num_threads)
    clip = self.calibration["clip"]
    probability = expit(
      self.calibration["coefficient"] * logit(np.clip(raw, clip, 1 - clip))
      + self.calibration["intercept"]
    )
    if not np.isfinite(returns).all() or not np.isfinite(probability).all():
      raise ValueError("invalid_prediction: model emitted nonfinite values")
    return returns, probability

  def save(self, directory: str | Path) -> str:
    """Write regressor.txt/classifier.txt, then canonical manifest.json; return SHA256.

    Existing artifacts are never overwritten. Hashes provide integrity, not
    authenticity: only load trusted local directories. Publication is external.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    names = ("regressor.txt", "classifier.txt", "manifest.json")
    if any((directory / name).exists() for name in names):
      raise FileExistsError("artifact_exists: use a new artifact directory")
    self.regressor.save_model(str(directory / "regressor.txt"))
    self.classifier.save_model(str(directory / "classifier.txt"))
    manifest = {
      "schema_version": 1,
      "model_type": "lightgbm_return_probability",
      "columns": self.feature_columns,
      "categorical_vocabulary": self.categorical_vocabulary,
      "missing_policy": "native_nan_unknown_category",
      "calibration": self.calibration,
      "horizon": self.horizon,
      "metadata": self.metadata,
      "num_threads": self.num_threads,
      "versions": {name: version(name) for name in ("lightgbm", "scikit-learn")},
      "files": {
        name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
        for name in names[:2]
      },
    }
    artifact_id = hashlib.sha256(_canonical(manifest).encode()).hexdigest()
    manifest["artifact_id"] = artifact_id
    (directory / "manifest.json").write_text(_canonical(manifest), encoding="utf-8")
    return artifact_id

  @classmethod
  def load(cls, directory: str | Path) -> "GradientBoostingModel":
    """Validate manifest and both file hashes before loading native Boosters."""
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    artifact_id = manifest.pop("artifact_id", None)
    if hashlib.sha256(_canonical(manifest).encode()).hexdigest() != artifact_id:
      raise ValueError("artifact_hash_mismatch: manifest modified")
    try:
      if (
        manifest["schema_version"] != 1
        or manifest["model_type"] != "lightgbm_return_probability"
        or manifest["missing_policy"] != "native_nan_unknown_category"
        or set(manifest["files"]) != {"regressor.txt", "classifier.txt"}
        or not isinstance(manifest["horizon"], int)
        or manifest["horizon"] < 1
        or not 1 <= manifest["num_threads"] <= 4
      ):
        raise ValueError("invalid_artifact_schema: unsupported manifest")
      columns = manifest["columns"]
      vocabulary = manifest["categorical_vocabulary"]
      if (
        not columns
        or len(columns) != len(set(columns))
        or any(not isinstance(column, str) for column in columns)
        or not set(vocabulary).issubset(columns)
      ):
        raise ValueError("invalid_artifact_schema: invalid feature schema")
      for categories in vocabulary.values():
        if any(not isinstance(value, str) for value in categories) or len(
          set(categories)
        ) != len(categories):
          raise ValueError("invalid_artifact_schema: invalid vocabulary")
      calibration = manifest["calibration"]
      if (
        set(calibration) != {"coefficient", "intercept", "clip"}
        or not np.isfinite(list(calibration.values())).all()
        or not 0 < calibration["clip"] < 0.5
      ):
        raise ValueError("invalid_artifact_schema: invalid calibration")
      for name, expected in manifest["files"].items():
        if hashlib.sha256((directory / name).read_bytes()).hexdigest() != expected:
          raise ValueError(f"artifact_hash_mismatch: {name} modified")
      regressor = lgb.Booster(model_file=str(directory / "regressor.txt"))
      classifier = lgb.Booster(model_file=str(directory / "classifier.txt"))
      if regressor.feature_name() != columns or classifier.feature_name() != columns:
        raise ValueError("invalid_artifact_schema: native feature names differ")
      expected_categories = [
        vocabulary[column] for column in columns if column in vocabulary
      ]
      if (
        regressor.pandas_categorical != expected_categories
        or classifier.pandas_categorical != expected_categories
      ):
        raise ValueError("invalid_artifact_schema: native categories differ")
      return cls(
        regressor,
        classifier,
        columns,
        vocabulary,
        calibration,
        manifest["horizon"],
        manifest["metadata"],
        manifest["num_threads"],
      )
    except (KeyError, TypeError) as error:
      raise ValueError(
        "invalid_artifact_schema: missing or malformed fields"
      ) from error

import importlib
import json

import numpy as np
import pandas as pd
import pytest

from stock_forecaster.forecasting import _china_calendar
from stock_forecaster.research.store import ResearchStore


def training():
  return importlib.import_module("stock_forecaster.research.training")


def verified_dataset(store):
  sessions = _china_calendar().sessions_in_range("2024-01-02", "2025-06-30")[:205]
  random = np.random.default_rng(123)
  returns = random.normal(0, .02, len(sessions))
  records = []
  for ticker, sign in [("600519.SS", 1), ("000001.SZ", -1)]:
    for session, close in zip(sessions, 100 * np.exp(np.cumsum(returns * sign)), strict=True):
      records.append({"ticker": ticker, "date": session, "close": close,
                      "open": close, "high": close * 1.01, "low": close * .99,
                      "volume": 1000., "amount": close * 1000,
                      "known_at": session + pd.Timedelta(hours=15)})
  bars = pd.DataFrame(records)
  index = pd.DataFrame({"date": sessions, "close": 100 * np.exp(np.cumsum(returns / 2)),
                        "known_at": sessions + pd.Timedelta(hours=15)})
  industries = pd.DataFrame({"ticker": ["600519.SS", "000001.SZ"],
                             "industry": ["X", "X"], "effective_from": [sessions[0]] * 2,
                             "known_at": [sessions[0]] * 2})
  provenance = {"source": "synthetic-offline-test", "approved_by": "test-fixture-review",
                "evidence": "fixture-not-real-market-data", "known_at_semantics": "publication_timestamp"}
  identifiers = {}
  for name, frame in [("bars", bars), ("index", index), ("industries", industries)]:
    identifiers[name] = store.save_snapshot(frame, {
      "kind": "industry" if name == "industries" else name,
      "trusted": True, "point_in_time": True, "provenance": provenance,
      "source": "synthetic-offline-test", "adjustment": "qfq" if name == "bars" else "raw",
    })
  return identifiers, sessions


def bounds(sessions):
  return dict(zip(("train_end", "validation_end", "calibration_end", "test_end"),
                  [sessions[position].date().isoformat() for position in (80, 120, 160, 204)], strict=True))


def test_free_data_and_flag_only_metadata_cannot_authorize_training(tmp_path):
  store = ResearchStore(tmp_path)
  with pytest.raises(ValueError, match="historical_industry_publication_unverified"):
    training().train(store)
  ids, _ = verified_dataset(store)
  metadata = store.snapshot_metadata(ids["industries"])
  metadata.pop("provenance")
  ids["industries"] = store.save_snapshot(store.read_snapshot(ids["industries"]), metadata)
  with pytest.raises(ValueError, match="historical_industry_publication_unverified"):
    training().load_dataset(store, **ids)
  assert not (tmp_path / "models" / "lightgbm").exists()


def test_late_bar_publication_and_missing_known_at_rejected(tmp_path):
  store = ResearchStore(tmp_path)
  ids, sessions = verified_dataset(store)
  frame = store.read_snapshot(ids["bars"])
  frame["known_at"] = sessions[-1] + pd.Timedelta(days=1)
  ids["bars"] = store.save_snapshot(frame, store.snapshot_metadata(ids["bars"]))
  with pytest.raises(ValueError, match="known_at_after_session"):
    training().load_dataset(store, **ids)


def test_real_six_head_training_purges_and_publishes_immutable_artifacts(tmp_path):
  from stock_forecaster.research.gradient_boosting import GradientBoostingModel

  store = ResearchStore(tmp_path)
  ids, sessions = verified_dataset(store)
  result = training().run_training(store, "train", **ids, **bounds(sessions), n_estimators=3, num_threads=1)
  assert set(result["artifacts"]) == {"1", "5", "20"}
  assert result["status"] == "research"
  for horizon, identifier in result["artifacts"].items():
    immutable = tmp_path / "models" / "artifacts" / identifier
    active = tmp_path / "models" / "lightgbm" / horizon
    assert (immutable / "manifest.json").read_bytes() == (active / "manifest.json").read_bytes()
    model = GradientBoostingModel.load(active)
    assert model.metadata["trained_through"] == sessions[160].date().isoformat()
    assert len(model.metadata["feature_schema"]) == 64
    assert model.metadata["dataset_ids"] == ids
    assert model.metadata["as_of"]
    assert result["metrics"][horizon]["return"]["n"] > 0
    assert result["metrics"][horizon]["probability"]["n"] > 0
    for name, split in result["splits"][horizon].items():
      assert split["max_target"] <= bounds(sessions)[name + "_end"]
  features = list(importlib.import_module("stock_forecaster.research.pipeline").snapshots(store, "features"))
  assert len(features) == 2
  assert all(meta["trusted"] and meta["point_in_time"] for _, meta in features)
  before = (tmp_path / "models" / "lightgbm" / "1" / "manifest.json").read_bytes()
  with pytest.raises(FileExistsError, match="active_reference_exists"):
    training().train(store, **ids, **bounds(sessions), n_estimators=3)
  assert (tmp_path / "models" / "lightgbm" / "1" / "manifest.json").read_bytes() == before


def test_budget_failure_does_not_publish_partial_models(tmp_path):
  store = ResearchStore(tmp_path)
  ids, sessions = verified_dataset(store)
  with pytest.raises(ValueError, match="training_row_budget_exceeded"):
    training().train(store, **ids, **bounds(sessions), max_rows=10)
  assert not (tmp_path / "models" / "lightgbm").exists()


def test_dataset_directory_requires_registered_matching_snapshots(tmp_path):
  store = ResearchStore(tmp_path / "store")
  directory = tmp_path / "dataset"
  directory.mkdir()
  (directory / "manifest.json").write_text(json.dumps({"trusted": True, "point_in_time": True}))
  with pytest.raises(ValueError, match="verified_snapshot_ids_required"):
    training().load_dataset(store, dataset_dir=directory)


def test_garch_snapshot_fit_publishes_immutable_copy_and_validates_sessions(tmp_path):
  from test_research_garch import returns_fixture

  store = ResearchStore(tmp_path)
  sessions = _china_calendar().sessions_in_range("2023-01-03", "2025-06-30")[-513:]
  prices = 100 * np.exp(np.cumsum(returns_fixture()[-513:]))
  frame = pd.DataFrame({"ticker": ["600519.SS"] * 513, "date": sessions, "close": prices})
  identifier = store.save_snapshot(frame, {"kind": "bars", "source": "fixture",
                                           "adjustment": "qfq", "tickers": ["600519.SS"]})
  result = training().fit_garch_snapshot(store, "600519.SS", identifier)
  active = tmp_path / "models" / "garch" / "600519.SS" / "manifest.json"
  immutable = tmp_path / "models" / "artifacts" / result["artifact_id"] / "manifest.json"
  assert active.read_bytes() == immutable.read_bytes()
  with pytest.raises(FileExistsError, match="active_reference_exists"):
    training().fit_garch_snapshot(store, "600519.SS", identifier)
  broken = store.save_snapshot(frame.drop(index=500), store.snapshot_metadata(identifier))
  with pytest.raises(ValueError, match="non_continuous_sessions"):
    training().fit_garch_snapshot(store, "600519.SS", broken, activate=False)


def test_worker_fit_job_submits_prediction_after_fitting(tmp_path, monkeypatch):
  from test_research_service import make_service

  from stock_forecaster.research.volatility import GarchModel
  from stock_forecaster.research.worker import ResearchWorker

  service, store, _, _ = make_service(tmp_path)
  fits = []
  snapshot, _, metadata, _ = service.freeze("600519")

  def fit(*args, **kwargs):
    fits.append(kwargs)
    assert kwargs["activate"] is False
    stage = tmp_path / "models" / "artifacts" / "staged"
    identifier = GarchModel({"omega": .02, "alpha[1]": .1, "beta[1]": .85, "nu": 8},
      {**metadata, "snapshot_id": snapshot, "trained_through": metadata["origin"]}).save(stage)
    stage.rename(stage.parent / identifier)
    return {"artifact_id": identifier, "activated": False}

  monkeypatch.setattr(training(), "run_training", fit)
  identifier = store.submit_job("fit_garch", {"ticker": "600519.SS", "snapshot_id": snapshot,
                                              "submit_prediction": True}, "fit")
  assert ResearchWorker(store, service).run_once()
  assert store.get_job(identifier)["status"] == "succeeded"
  assert len(fits) == 1
  jobs = [job for job in store.list_jobs() if job["kind"] == "prediction"]
  assert len(jobs) == 1
  assert jobs[0]["payload"]["artifact_revision"] == service.runtime.revision("600519.SS")


def test_training_child_and_cli_fail_closed_without_live_data(tmp_path, monkeypatch, capsys):
  from stock_forecaster.research import cli

  store = ResearchStore(tmp_path)
  with pytest.raises(ValueError, match="historical_industry_publication_unverified"):
    training().run_training(store, "train", timeout=20)
  monkeypatch.setenv("STOCK_FORECASTER_RESEARCH_DATA_DIR", str(tmp_path))
  assert cli.main(["train"]) == 1
  result = json.loads(capsys.readouterr().err)
  assert result["error"]["code"] == "historical_industry_publication_unverified"


def test_cli_import_is_not_model_training_startup():
  import subprocess
  import sys

  completed = subprocess.run([sys.executable, "-c",
    ("import sys; import stock_forecaster.research.cli; "
     "assert 'arch' not in sys.modules; assert 'lightgbm' not in sys.modules")],
    capture_output=True, timeout=20, check=False)
  assert completed.returncode == 0, completed.stderr.decode(errors="replace")


def test_background_fit_failure_keeps_reason_without_submitting_prediction(tmp_path, monkeypatch):
  from test_research_service import make_service

  from stock_forecaster.research.worker import ResearchWorker

  service, store, _, _ = make_service(tmp_path)

  def fail(*args, **kwargs):
    raise ValueError("insufficient_garch_history")

  monkeypatch.setattr(training(), "run_training", fail)
  identifier = store.submit_job("fit_garch", {"ticker": "600519.SS", "snapshot_id": "a" * 64,
                                              "submit_prediction": True}, "fit")
  ResearchWorker(store, service).run_once()
  assert store.get_job(identifier)["error"]["code"] == "insufficient_garch_history"
  assert len([job for job in store.list_jobs() if job["kind"] == "prediction"]) == 0
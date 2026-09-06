import importlib
import json

import pandas as pd
import pytest
from test_research_training import backfilled_dataset, bounds, training

from stock_forecaster.research.store import ResearchStore


def datasets():
  return importlib.import_module("stock_forecaster.research.datasets")


def collection_fixture(store):
  identifiers, sessions = backfilled_dataset(store)
  universe = store.save_snapshot(pd.DataFrame({"ticker": ["600519.SS", "000001.SZ"]}),
                                 {"kind": "universe", "trusted": False})
  snapshots = []
  for name in ("bars", "index"):
    frame = store.read_snapshot(identifiers[name])
    if name == "index":
      frame["ticker"] = "000300.SS"
    metadata = store.snapshot_metadata(identifiers[name])
    snapshots.append(store.save_snapshot(frame, {
      **metadata, "kind": "bars", "universe_snapshot": universe,
      "tickers": frame.ticker.unique().tolist(),
    }))
  report = {"bars_snapshots": snapshots, "universe_snapshot": universe,
            "industry_snapshot": None, "partial_universe": True,
            "as_of": sessions[-1].date().isoformat(), "start": sessions[0].date().isoformat(),
            "errors": [], "blockers": ["historical_universe_unverified"]}
  identifier = store.save_snapshot(pd.DataFrame({"report": [json.dumps(report)]}),
                                   {"kind": "collection_report"})
  return identifier, sessions, report


def test_assemble_uses_only_explicit_collection_and_retains_parent_evidence(tmp_path):
  store = ResearchStore(tmp_path)
  collection, _, source = collection_fixture(store)
  result = datasets().assemble_dataset(store, collection_report=collection)
  assert result["rows"] == {"bars": 410, "index": 205}
  assert result["tickers"] == ["000001.SZ", "600519.SS"]
  assert result["benchmark"] == "000300.SS"
  metadata = store.snapshot_metadata(result["snapshot_ids"]["bars"])
  assert metadata["parent_snapshots"] == source["bars_snapshots"]
  assert metadata["collection_report"] == collection
  assert metadata["trusted"] is False
  assert metadata["point_in_time"] is False
  dataset = datasets().dataset_options(store, result["dataset_snapshot"])
  assert dataset == result["snapshot_ids"]


def test_assemble_rejects_mixed_provider_basis_and_budget(tmp_path):
  store = ResearchStore(tmp_path)
  collection, _, source = collection_fixture(store)
  with pytest.raises(ValueError, match="training_row_budget_exceeded"):
    datasets().assemble_dataset(store, collection_report=collection, max_rows=10)
  index = source["bars_snapshots"][-1]
  source["bars_snapshots"][-1] = store.save_snapshot(store.read_snapshot(index),
    {**store.snapshot_metadata(index), "source": "different-upstream"})
  bad = store.save_snapshot(pd.DataFrame({"report": [json.dumps(source)]}),
                             {"kind": "collection_report"})
  with pytest.raises(ValueError, match="mixed_dataset_basis"):
    datasets().assemble_dataset(store, collection_report=bad)


def test_readiness_counts_actual_purged_origins_and_reports_pit_blockers(tmp_path):
  store = ResearchStore(tmp_path)
  identifiers, sessions = backfilled_dataset(store)
  report = datasets().assess_dataset(store, **identifiers, **bounds(sessions))
  assert report["historical_research"]["ready"] is True
  assert report["strict_pit"]["ready"] is False
  assert report["models"]["timesfm"]["context_eligible_tickers"] == 2
  assert report["models"]["garch"]["history_eligible_tickers"] == 0
  assert report["splits"]["20"]["train"]["n"] == 82
  assert report["splits"]["20"]["test"]["n"] == 48
  assert report["splits"]["20"]["test"]["origins"] == 24
  assert report["statistical_validity"] == "not_established"
  assert report["quality"]["invalid_stock_rows"] == 0
  assert store.snapshot_metadata(report["report_snapshot"])["kind"] == "readiness_report"


def test_research_excludes_zero_volume_and_missing_windows_without_filling(tmp_path):
  store = ResearchStore(tmp_path)
  identifiers, sessions = backfilled_dataset(store)
  bars = store.read_snapshot(identifiers["bars"])
  suspended = (bars.ticker == "600519.SS") & (bars.date == sessions[100])
  bars.loc[suspended, "volume"] = 0
  bars = bars.loc[~((bars.ticker == "600519.SS") & (bars.date == sessions[140]))]
  identifiers["bars"] = store.save_snapshot(bars, store.snapshot_metadata(identifiers["bars"]))
  dataset = training().load_dataset(store, **identifiers, mode="historical_research", feature_set="price_index_v1")
  features, labels, quality = training().prepare_training_data(dataset)
  stock = features.loc[features.ticker == "600519.SS"]
  assert not stock.origin.isin(sessions[100:121]).any()
  assert not stock.origin.isin(sessions[140:161]).any()
  assert len(features.loc[features.ticker == "000001.SZ"]) == 185
  affected = labels.loc[(labels.ticker == "600519.SS") & (labels.origin == sessions[99])]
  assert affected["return"].isna().all()
  assert quality["invalid_stock_rows"] == 1
  assert quality["excluded_feature_rows"] == 42
  assert pd.isna(labels.loc[(labels.ticker == "600519.SS") & (labels.origin == sessions[140]), "return"]).all()


def test_readiness_rejects_one_class_training_without_starting_a_model(tmp_path):
  store = ResearchStore(tmp_path)
  identifiers, sessions = backfilled_dataset(store)
  frame = store.read_snapshot(identifiers["bars"])
  frame["close"] = frame.groupby("ticker").cumcount() + 100.
  frame["open"] = frame["close"]
  frame["high"] = frame["close"] + 1
  frame["low"] = frame["close"] - 1
  identifiers["bars"] = store.save_snapshot(frame, store.snapshot_metadata(identifiers["bars"]))
  result = datasets().assess_dataset(store, **identifiers, **bounds(sessions))
  assert result["historical_research"]["ready"] is False
  assert "one_class_training" in result["historical_research"]["blockers"]
  assert not (tmp_path / "models").exists()


def test_direct_research_snapshots_cannot_mix_provider_basis(tmp_path):
  store = ResearchStore(tmp_path)
  identifiers, _ = backfilled_dataset(store)
  identifiers["index"] = store.save_snapshot(store.read_snapshot(identifiers["index"]),
    {**store.snapshot_metadata(identifiers["index"]), "source": "unrelated-provider"})
  with pytest.raises(ValueError, match="mixed_dataset_basis"):
    training().load_dataset(store, **identifiers, mode="historical_research", feature_set="price_index_v1")


def test_latest_zero_volume_is_not_an_eligible_model_context(tmp_path):
  store = ResearchStore(tmp_path)
  identifiers, sessions = backfilled_dataset(store)
  frame = store.read_snapshot(identifiers["bars"])
  frame.loc[(frame.ticker == "600519.SS") & (frame.date == sessions[-1]), "volume"] = 0
  identifiers["bars"] = store.save_snapshot(frame, store.snapshot_metadata(identifiers["bars"]))
  result = datasets().assess_dataset(store, **identifiers, **bounds(sessions))
  assert result["models"]["timesfm"]["context_eligible_tickers"] == 1
  assert result["models"]["contexts"][1]["consecutive_prices"] == 0


def test_cli_prepares_and_assesses_reproducible_dataset(tmp_path, capsys):
  from stock_forecaster.research import cli

  store = ResearchStore(tmp_path)
  collection, _, _ = collection_fixture(store)
  assert cli.main(["--root", str(tmp_path), "prepare-dataset", "--collection-report", collection]) == 0
  prepared = json.loads(capsys.readouterr().out)
  assert cli.main(["--root", str(tmp_path), "readiness", "--dataset", prepared["dataset_snapshot"]]) == 0
  result = json.loads(capsys.readouterr().out)
  assert result["historical_research"]["ready"] is True
  assert result["strict_pit"]["ready"] is False
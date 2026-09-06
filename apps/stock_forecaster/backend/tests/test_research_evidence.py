import json

import pandas as pd
import pytest
from test_research_api import api_parts

from stock_forecaster.research.store import ResearchStore

__all__ = ["api_parts"]


def save_report(store, kind, document, **metadata):
  return store.save_snapshot(pd.DataFrame({"report": [json.dumps(document)]}),
                             {"kind": kind, **metadata})


def test_evidence_empty_is_read_only_and_does_not_load_models(api_parts):
  client, root, provider, manager, loads, _ = api_parts

  def forbidden(*args, **kwargs):
    raise AssertionError("evidence reads must not collect or infer")

  provider.fetch = forbidden
  manager.predict = forbidden
  response = client.get("/api/v2/evidence")
  assert response.status_code == 200
  assert response.json() == {
    "status": "empty", "usage": "local_research_only", "production_ready": False,
    "source_check": None, "experiment": None,
  }
  assert loads == []
  assert not root.exists()


def test_evidence_matches_dataset_not_newest_readiness_and_excludes_raw_payloads(api_parts):
  client, root, _, _, loads, _ = api_parts
  store = ResearchStore(root)
  identifiers = {"bars": "a" * 64, "index": "b" * 64}
  readiness = save_report(store, "readiness_report", {
    "dataset_ids": identifiers, "as_of": "2026-09-05T12:00:00+00:00",
    "strict_pit": {"ready": False, "blockers": ["verified_dataset_required"]},
    "historical_research": {"ready": True, "blockers": []},
    "history": {"start": "2021-09-01", "end": "2026-09-04", "sessions": 1214, "tickers": 30},
    "quality": {"feature_rows": 35589, "missing_stock_sessions": 37, "excluded_feature_rows": 230},
    "models": {"timesfm": {"context_eligible_tickers": 29}, "garch": {"history_eligible_tickers": 27}},
  }, dataset_ids=identifiers)
  save_report(store, "readiness_report", {"dataset_ids": {"bars": "c" * 64, "index": "d" * 64},
                                         "history": {"tickers": 999}}, dataset_ids={"bars": "c" * 64, "index": "d" * 64})
  training = save_report(store, "training_report", {
    "dataset_ids": identifiers, "as_of": "2026-09-05T13:00:00+00:00",
    "status": "research", "mode": "historical_research", "feature_set": "price_index_v1",
    "artifacts": {"1": "e" * 64}, "private_path": "C:/secret/model", "token": "do-not-expose",
    "metrics": {"1": {"return": {"n": 5380, "mae": .02112618},
                         "zero_return": {"n": 5380, "mae": .02101135},
                         "probability": {"brier": .24819163, "auc": .55391278},
                         "training_prior": {"brier": .24874028}}},
    "splits": {"1": {"test": {"n": 5380, "origins": 182}}},
    "limitations": ["not_point_in_time_backtest", "token=do-not-expose"],
  }, dataset_ids=identifiers)
  source = save_report(store, "collection_report", {
    "mode": "probe", "as_of": "2026-09-04", "start": "2026-08-03",
    "universe_total": 5556, "stock_requested": 2, "index_requested": 2,
    "partial_universe": True,
    "collection": {"status": "partial", "requested": 4, "succeeded": 4, "failed": 0},
    "source_coverage": {"industry": "failed"},
    "errors": [{"operation": "industry", "code": "provider_transient", "raw": "do-not-expose"}],
  })
  private = save_report(store, "provider_response", {"token": "do-not-expose"})
  response = client.get("/api/v2/evidence")
  assert response.status_code == 200
  document = response.json()
  assert document["status"] == "ready"
  assert document["production_ready"] is False
  assert document["source_check"]["snapshot_id"] == source
  experiment = document["experiment"]
  assert experiment["training_snapshot_id"] == training
  assert experiment["readiness_snapshot_id"] == readiness
  assert experiment["history"]["tickers"] == 30
  assert experiment["timesfm_context_eligible"] == 29
  assert experiment["garch_history_eligible"] == 27
  assert experiment["approval"] == "not_approved"
  assert experiment["horizons"][0]["mae"] == .02112618
  assert experiment["horizons"][0]["mae_beats_baseline"] is False
  assert experiment["horizons"][0]["brier_beats_baseline"] is True
  assert len(experiment["horizons"]) == 3
  assert experiment["horizons"][1]["mae"] is None
  assert "do-not-expose" not in response.text
  assert private not in response.text
  assert store.list_jobs() == []
  assert loads == []


def test_missing_matching_readiness_is_partial_not_latest_success(api_parts):
  client, root, _, _, _, _ = api_parts
  store = ResearchStore(root)
  identifiers = {"bars": "a" * 64, "index": "b" * 64}
  save_report(store, "training_report", {"dataset_ids": identifiers, "mode": "historical_research"})
  save_report(store, "readiness_report", {"dataset_ids": {"bars": "c" * 64, "index": "d" * 64},
                                          "historical_research": {"ready": True}},
              dataset_ids={"bars": "c" * 64, "index": "d" * 64})
  response = client.get("/api/v2/evidence")
  assert response.status_code == 200
  assert response.json()["status"] == "partial"
  assert response.json()["experiment"]["readiness_snapshot_id"] is None


def test_readonly_store_cannot_create_snapshot_or_task(tmp_path):
  store = ResearchStore(tmp_path / "original")
  identifier = save_report(store, "collection_report", {"collection": {"requested": 1}})
  reader = ResearchStore(store.root, read_only=True)
  assert reader.read_snapshot(identifier).iloc[0]["report"]
  with pytest.raises(ValueError, match="Read-only"):
    reader.save_snapshot(pd.DataFrame({"report": ["{}"]}), {"kind": "collection_report"})
  with pytest.raises(ValueError, match="Read-only"):
    reader.submit_job("prediction", {}, "read-only-test")
  assert store.list_jobs() == []


def test_matching_training_without_source_probe_is_partial(api_parts):
  client, root, _, _, _, _ = api_parts
  store = ResearchStore(root)
  identifiers = {"bars": "a" * 64, "index": "b" * 64}
  save_report(store, "training_report", {"dataset_ids": identifiers})
  save_report(store, "readiness_report", {"dataset_ids": identifiers}, dataset_ids=identifiers)
  result = client.get("/api/v2/evidence").json()
  assert result["source_check"] is None
  assert result["status"] == "partial"


@pytest.mark.parametrize("private", [{"token": "secret"}, {"errors": [{"private_path": "C:/private"}]}])
def test_preview_refuses_sensitive_reports_before_creating_destination(tmp_path, private):
  from stock_forecaster.research.preview import seed_reports

  source = ResearchStore(tmp_path / "source")
  save_report(source, "collection_report", private)
  destination = tmp_path / "preview"
  with pytest.raises(ValueError, match="sensitive_preview_report"):
    seed_reports(destination, [source.root])
  assert not destination.exists()


def test_preview_copies_only_reports_preserves_identity_and_exposes_no_mutations(tmp_path):
  from fastapi.testclient import TestClient

  from stock_forecaster.research.preview import create_preview_app, seed_reports

  source = ResearchStore(tmp_path / "source")
  report = save_report(source, "collection_report", {"as_of": "2026-09-04", "collection": {"requested": 4}})
  save_report(source, "provider_response", {"private": "do-not-export"})
  source.submit_job("prediction", {}, "must-not-copy")
  with source._connect(write=True) as connection:
    connection.execute("UPDATE snapshots SET created_at=? WHERE id=?", ["2026-09-05T10:00:00+00:00", report])
  destination = tmp_path / "preview"
  result = seed_reports(destination, [source.root])
  assert result == {"imported_reports": 1, "snapshot_ids": [report]}
  reader = ResearchStore(destination, read_only=True)
  assert len(list(reader.snapshots_dir.glob("*.parquet"))) == 1
  assert reader.list_jobs() == []
  assert not (destination / "models").exists()
  client = TestClient(create_preview_app(destination, web_port=5175))
  response = client.get("/api/v2/evidence")
  assert response.status_code == 200
  assert response.json()["source_check"]["checked_at"] == "2026-09-05T10:00:00+00:00"
  assert "do-not-export" not in response.text
  assert client.post("/api/v2/predictions", json={"ticker": "600519"}).status_code == 404
  assert client.post("/api/v2/evidence").status_code == 405
  assert client.get("/api/v1/quotes/600519").status_code == 404
  assert client.get("/api/v1/health").json()["mode"] == "read_only_evidence"
  with pytest.raises(ValueError, match="preview_destination_exists"):
    seed_reports(destination, [source.root])


def test_free_sources_keep_access_and_later_rate_limit_separate(api_parts):
  client, root, _, _, _, _ = api_parts
  store = ResearchStore(root)
  save_report(store, "collection_report", {"provider": "baostock", "collection": {"status": "succeeded", "requested": 4, "succeeded": 4},
    "source_coverage": {"industry": "downloaded_publication_unverified"}}, provider="baostock")
  save_report(store, "provider_capabilities", {"provider": "tushare", "checks": [
    {"check": "daily", "status": "available", "rows": 6},
    {"check": "industry_history", "status": "permission_denied", "rows": 0, "code": "tushare_permission_denied"},
    {"check": "token=secret", "status": "available", "rows": 99},
  ]}, provider="tushare")
  save_report(store, "collection_report", {"provider": "tushare", "collection": {"status": "failed", "requested": 0, "succeeded": 0},
    "errors": [{"code": "tushare_rate_limited", "message": "secret"}]}, provider="tushare")
  response = client.get("/api/v2/evidence")
  assert response.status_code == 200
  sources = {row["provider"]: row for row in response.json()["sources"]}
  assert sources["tushare"]["collection"]["status"] == "failed"
  assert sources["tushare"]["collection"]["error_codes"] == ["tushare_rate_limited"]
  assert sources["tushare"]["access"]["checks"][0]["status"] == "available"
  assert sources["baostock"]["collection"]["succeeded"] == 4
  assert "secret" not in response.text


def test_preview_includes_capability_reports_without_credentials(tmp_path):
  from stock_forecaster.research.preview import seed_reports

  source = ResearchStore(tmp_path / "source")
  identifier = save_report(source, "provider_capabilities", {"provider": "tushare", "checks": [
    {"check": "industry_history", "status": "permission_denied", "rows": 0},
  ]}, provider="tushare")
  result = seed_reports(tmp_path / "preview", [source.root])
  assert result["snapshot_ids"] == [identifier]
import importlib
import json
import sqlite3
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from stock_forecaster.research.providers import ProviderResult
from stock_forecaster.research.store import ResearchStore


def pipeline():
  return importlib.import_module("stock_forecaster.research.pipeline")


class FixtureProvider:
  def __init__(self, count=3):
    self.tickers = [f"{600000 + number}.SS" for number in range(count)]
    self.calls = []

  def fetch(self, operation, **kwargs):
    self.calls.append((operation, kwargs))
    if operation == "universe":
      return ProviderResult(pd.DataFrame({"ticker": self.tickers, "name": self.tickers}),
                            {"kind": "universe", "failed": 0, "point_in_time": False})
    if operation == "industry":
      return ProviderResult(pd.DataFrame({"ticker": self.tickers, "known_at": ["2026-09-05"] * len(self.tickers)}),
                            {"kind": "industry", "point_in_time": False,
                             "blockers": ["historical_industry_publication_unverified"]})
    ticker = kwargs["ticker"]
    if ticker == "600001.SS":
      raise ValueError("password=secret")
    return ProviderResult(pd.DataFrame({
      "ticker": [ticker] * 2, "date": ["2026-09-03", "2026-09-04"],
      "close": [100., 101.], "volume": [1000., 1001.],
    }), {"kind": "bars", "source": "akshare:stock_zh_a_hist_tx",
         "adjustment": kwargs["adjustment"], "ticker": ticker,
         "missing_fields": ["amount"], "failed": 0, "point_in_time": False})


def test_bootstrap_batches_tickers_counts_errors_and_preserves_industry(tmp_path):
  store, provider = ResearchStore(tmp_path), FixtureProvider(6)
  result = pipeline().ResearchPipeline(store, provider, rate_seconds=0).bootstrap(
    start="2026-09-01", end="2026-09-04", limit=4, index_limit=0, batch_size=3,
  )
  assert result["requested"] == 4
  assert result["succeeded"] == 3
  assert result["failed"] == 1
  assert result["partial_universe"] is True
  assert result["universe_total"] == 6
  assert len(result["errors"]) == 2
  assert "secret" not in json.dumps(result)
  assert "historical_industry_publication_unverified" in result["blockers"]
  assert len(result["bars_snapshots"]) == 4
  assert max(len(store.read_snapshot(identifier).ticker.unique())
             for identifier in result["bars_snapshots"]) == 2
  assert store.snapshot_metadata(result["industry_snapshot"])["point_in_time"] is False


def test_probe_defaults_to_two_stocks_and_two_indices(tmp_path):
  provider = FixtureProvider(10)
  result = pipeline().ResearchPipeline(ResearchStore(tmp_path), provider, rate_seconds=0).probe()
  calls = [kwargs for operation, kwargs in provider.calls if operation == "bars"]
  assert len(calls) == 6
  assert result["requested"] == 4
  assert result["partial_universe"] is True
  assert "incomplete_ohlcv_amount" in result["blockers"]


def test_non_trading_collection_rows_are_not_eligible(tmp_path):
  class InactiveProvider(FixtureProvider):
    def fetch(self, operation, **kwargs):
      result = super().fetch(operation, **kwargs)
      if operation == "bars":
        result.metadata.update(non_trading_rows=1, missing_fields=[])
      return result

  report = pipeline().ResearchPipeline(ResearchStore(tmp_path), InactiveProvider(1), rate_seconds=0).bootstrap(
    limit=1, index_limit=0,
  )
  assert report["succeeded"] == 1
  assert report["eligible"] == 0
  assert report["non_trading_rows"] == 2


def test_collection_keeps_raw_revisions_and_earliest_observation(tmp_path):
  from stock_forecaster.research.providers import normalize_universe

  class OriginalProvider:
    def __init__(self):
      self.day = 4

    def fetch(self, operation, **kwargs):
      self.day += 1
      return normalize_universe(pd.DataFrame({"code": ["600519"], "name": ["A"]}),
                                f"2026-09-{self.day:02d}T10:00:00+00:00")

  store = ResearchStore(tmp_path)
  runner = pipeline().ResearchPipeline(store, OriginalProvider(), rate_seconds=0)
  first = runner._fetch("universe")
  second = runner._fetch("universe")
  assert first.metadata["raw_snapshot_id"] != second.metadata["raw_snapshot_id"]
  metadata = store.snapshot_metadata(second.metadata["raw_snapshot_id"])
  assert metadata["first_seen_at"] == "2026-09-05T10:00:00+00:00"
  assert metadata["observed_at"] == "2026-09-06T10:00:00+00:00"
  assert metadata["published_at"] is None
  assert store.read_snapshot(first.metadata["raw_snapshot_id"]).columns.tolist() == ["code", "name"]
  assert first.metadata["point_in_time"] is False


def evaluation_fixture(store, issued="2026-09-02T10:00:00+00:00"):
  frozen = store.save_snapshot(pd.DataFrame({"date": ["2026-09-01", "2026-09-02"],
                                             "price": [99., 100.]}), {
    "kind": "prices", "ticker": "600519.SS", "origin": "2026-09-02",
    "source": "akshare:stock_zh_a_hist_tx", "adjustment": "qfq",
    "adjustment_revision": None,
  })
  job_id = store.submit_job("prediction", {"ticker": "600519.SS"}, "forecast")
  store.claim_job()
  store.complete_job(job_id, {
    "bundle_id": job_id, "ticker": "600519.SS", "origin": "2026-09-02",
    "issued_at": issued, "snapshot_id": frozen,
    "path": [{"date": "2026-09-03"}, {"date": "2026-09-04"}],
    "horizons": [{"horizon": 2, "target_date": "2026-09-04",
                  "timesfm_return": .03, "lightgbm_return": .02,
                  "up_probability": .7, "volatility": .2}],
    "components": {"garch": {"status": "ready", "artifact_id": "garch-fixture"}},
  })
  return job_id


def later_bars(store, anchor=100., source="akshare:stock_zh_a_hist_tx", missing=False):
  frame = pd.DataFrame({"ticker": ["600519.SS"] * 4,
                        "date": ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"],
                        "close": [99., anchor, 102., 101.]})
  if missing:
    frame = frame.drop(index=2)
  return store.save_snapshot(frame, {"kind": "bars", "tickers": ["600519.SS"],
                                      "source": source, "adjustment": "qfq"})


def test_score_maturity_sigma_squared_and_immutable_repeat(tmp_path):
  store = ResearchStore(tmp_path)
  job_id = evaluation_fixture(store)
  realized_id = later_bars(store)
  runner = pipeline().ResearchPipeline(store, FixtureProvider(), rate_seconds=0)
  assert runner.score(as_of="2026-09-03")["scored"] == 0
  result = runner.score(as_of="2026-09-04")
  assert result["scored"] == 1
  snapshot = result["evaluation_snapshots"][0]
  metadata = store.snapshot_metadata(snapshot)
  assert metadata["forecast_id"] == job_id
  assert metadata["realized_snapshot"] == realized_id
  assert metadata["classification"] == "prospective"
  rows = store.read_snapshot(snapshot)
  risk = rows.loc[rows["group"] == "variance"].iloc[0]
  assert risk["predicted"] == pytest.approx(.04)
  assert risk["actual"] == pytest.approx(np.log(102 / 100) ** 2 + np.log(101 / 102) ** 2)
  assert set(rows["group"]) == {"return", "probability", "variance"}
  assert runner.score(as_of="2026-09-04")["scored"] == 0
  pd.testing.assert_frame_equal(store.read_snapshot(snapshot), rows)


@pytest.mark.parametrize("options,reason", [
  ({"anchor": 90.}, "adjustment_unresolved"),
  ({"source": "another-source"}, "adjustment_unresolved"),
  ({"missing": True}, "missing_target_sessions"),
])
def test_score_never_mixes_basis_or_skips_target_sessions(tmp_path, options, reason):
  store = ResearchStore(tmp_path)
  evaluation_fixture(store)
  later_bars(store, **options)
  result = pipeline().ResearchPipeline(store, FixtureProvider()).score(as_of="2026-09-04")
  assert result["scored"] == 0
  assert result["invalid"] == 1
  metadata = store.snapshot_metadata(result["evaluation_snapshots"][0])
  assert metadata["valid"] is False
  assert metadata["reason"] == reason


def test_late_or_pre_origin_issue_is_replay(tmp_path):
  for number, issued in enumerate(["2026-09-03T02:00:00+00:00", "2026-09-01T10:00:00+00:00"]):
    store = ResearchStore(tmp_path / str(number))
    evaluation_fixture(store, issued)
    later_bars(store)
    result = pipeline().ResearchPipeline(store, FixtureProvider()).score(as_of="2026-09-04")
    assert store.snapshot_metadata(result["evaluation_snapshots"][0])["classification"] == "replay"


def test_daily_uses_service_identity_and_yields_between_batches(tmp_path):
  from test_research_service import make_service

  service, store, _, _ = make_service(tmp_path)
  service.clock = lambda: datetime(2026, 9, 4, 10, tzinfo=timezone.utc)
  boundaries = []
  runner = pipeline().ResearchPipeline(store, FixtureProvider(4), rate_seconds=0)
  result = runner.daily(service, limit=4, index_limit=0, batch_size=2,
                        fit_missing=False, on_batch=lambda: boundaries.append(len(store.list_jobs())))
  assert result["submitted"] == 3
  assert boundaries == [1, 3]
  assert all("artifact_revision" in job["payload"] for job in store.list_jobs())
  with sqlite3.connect(store.db_path) as db:
    assert db.execute("SELECT count(*) FROM jobs WHERE priority=0").fetchone()[0] == 3
  assert service.submit("600000", priority=0)["job_id"] in result["prediction_jobs"]
  interactive = service.submit("600009")["job_id"]
  assert store.claim_job()["id"] == interactive


def test_persisted_provider_reads_without_network_and_keeps_source(tmp_path):
  from stock_forecaster.market_data import MarketDataService
  from stock_forecaster.schemas import DateSelection

  store = ResearchStore(tmp_path)
  later_bars(store)
  provider = pipeline().PersistedMarketProvider(store)
  result = MarketDataService(provider, 0).get("600519", DateSelection(period="5y"))
  assert result.source == "akshare:stock_zh_a_hist_tx"
  assert result.observations[-1].price == 101.


def test_score_selects_matching_adjustment_not_latest_raw(tmp_path):
  store = ResearchStore(tmp_path)
  evaluation_fixture(store)
  identifier = later_bars(store)
  raw = store.read_snapshot(identifier)
  raw["close"] *= 10
  store.save_snapshot(raw, {**store.snapshot_metadata(identifier), "adjustment": "raw"})
  result = pipeline().ResearchPipeline(store, FixtureProvider()).score(as_of="2026-09-04")
  assert result["scored"] == 1
  assert store.snapshot_metadata(result["evaluation_snapshots"][0])["realized_snapshot"] == identifier


def test_score_future_as_of_cannot_mature_a_forecast_early(tmp_path):
  store = ResearchStore(tmp_path)
  evaluation_fixture(store)
  later_bars(store)
  runner = pipeline().ResearchPipeline(store, FixtureProvider())
  runner.clock = lambda: datetime(2026, 9, 3, 10, tzinfo=timezone.utc)
  assert runner.score(as_of="2026-09-10")["scored"] == 0


def test_cli_probe_and_score_route_to_pipeline_with_sample_defaults(tmp_path, monkeypatch, capsys):
  from stock_forecaster.research import cli, providers

  monkeypatch.setenv("STOCK_FORECASTER_RESEARCH_DATA_DIR", str(tmp_path))
  monkeypatch.setattr(providers.ResearchProvider, "fetch", FixtureProvider(5).fetch)
  assert cli.main(["probe", "--rate-seconds", "0"]) == 0
  result = json.loads(capsys.readouterr().out)
  assert result["requested"] == 4
  assert result["partial_universe"] is True
  assert cli.main(["score", "--as-of", "2026-09-04"]) == 0
  assert json.loads(capsys.readouterr().out)["scored"] == 0


def test_daily_cli_processes_each_persisted_batch_without_refetch(tmp_path, monkeypatch, capsys):
  from test_research_service import RecordedProvider

  from stock_forecaster.research import cli, providers
  from stock_forecaster.research.runtime import HttpTimesFMAdapter

  recorded = RecordedProvider(80)

  class FullProvider(FixtureProvider):
    def fetch(self, operation, **kwargs):
      if operation != "bars":
        return super().fetch(operation, **kwargs)
      frame = recorded.frame.reset_index().rename(columns={"index": "date", "Close": "close", "Volume": "volume"})
      frame["date"] = recorded.frame.index.strftime("%Y-%m-%d")
      frame["ticker"] = kwargs["ticker"]
      return ProviderResult(frame, {"kind": "bars", "source": "akshare:stock_zh_a_hist_tx",
                                    "ticker": kwargs["ticker"], "adjustment": kwargs["adjustment"]})

  def forbidden(*args, **kwargs):
    raise AssertionError("must not refetch persisted daily data")

  monkeypatch.setenv("STOCK_FORECASTER_RESEARCH_DATA_DIR", str(tmp_path))
  monkeypatch.setattr(providers.ResearchProvider, "fetch", FullProvider(2).fetch)
  monkeypatch.setattr(cli.RoutedMarketDataProvider, "fetch", forbidden)
  monkeypatch.setattr(HttpTimesFMAdapter, "predict", forbidden)
  assert cli.main(["daily", "--limit", "2", "--index-limit", "0", "--batch-size", "1",
                   "--no-fit", "--rate-seconds", "0"]) == 0
  result = json.loads(capsys.readouterr().out)
  assert result["submitted"] == 2
  assert all(ResearchStore(tmp_path).get_job(identifier)["status"] == "partial"
             for identifier in result["prediction_jobs"])


def test_collection_counts_industry_row_errors_and_incomplete_fields(tmp_path):
  class BadIndustryProvider(FixtureProvider):
    def fetch(self, operation, **kwargs):
      result = super().fetch(operation, **kwargs)
      if operation == "industry":
        result.metadata["failed"] = 2
      return result

  result = pipeline().ResearchPipeline(ResearchStore(tmp_path), BadIndustryProvider(1),
                                       rate_seconds=0).bootstrap(limit=1, index_limit=0)
  assert result["invalid_rows"] == 2
  assert result["error_count"] == 2
  assert result["eligible"] == 0


def test_missing_all_model_signals_is_not_a_successful_score(tmp_path):
  store = ResearchStore(tmp_path)
  identifier = evaluation_fixture(store)
  later_bars(store)
  with sqlite3.connect(store.db_path) as db:
    bundle = json.loads(db.execute("SELECT result FROM jobs WHERE id=?", [identifier]).fetchone()[0])
    for key in ("timesfm_return", "lightgbm_return", "up_probability", "volatility"):
      bundle["horizons"][0][key] = None
    db.execute("UPDATE jobs SET result=? WHERE id=?", [json.dumps(bundle), identifier])
  result = pipeline().ResearchPipeline(store).score(as_of="2026-09-04")
  assert result["scored"] == 0
  assert result["invalid"] == 1
  assert store.snapshot_metadata(result["evaluation_snapshots"][0])["reason"] == "no_available_signals"


def test_universe_failure_persists_daily_report_without_success_watermark(tmp_path):
  from test_research_service import make_service

  service, store, _, _ = make_service(tmp_path)
  provider = FixtureProvider()

  def fail(*args, **kwargs):
    raise ValueError("token=secret")

  provider.fetch = fail
  result = pipeline().ResearchPipeline(store, provider, rate_seconds=0).daily(service)
  assert result["collection"]["status"] == "failed"
  assert result["collection"]["complete"] is False
  assert result["collection"]["success"] is False
  assert result["execution"]["predicted"] == 0
  assert "secret" not in json.dumps(result)
  restored = json.loads(store.read_snapshot(result["report_snapshot"]).iloc[0]["report"])
  assert restored["collection"] == result["collection"]
  assert store.snapshot_metadata(result["report_snapshot"])["kind"] == "daily_report"


def test_final_daily_snapshot_distinguishes_collection_from_queued_execution(tmp_path):
  from test_research_service import make_service

  service, store, _, _ = make_service(tmp_path)
  result = pipeline().ResearchPipeline(store, FixtureProvider(1), rate_seconds=0).daily(
    service, limit=1, index_limit=0, fit_missing=False,
  )
  assert result["collection"]["succeeded"] == 1
  assert result["execution"]["predicted"] == 0
  assert result["execution"]["prediction"]["pending"] == 1
  restored = json.loads(store.read_snapshot(result["report_snapshot"]).iloc[0]["report"])
  assert restored["prediction_jobs"] == result["prediction_jobs"]
  assert restored["execution"] == result["execution"]


def test_daily_score_failure_still_persists_final_execution_report(tmp_path, monkeypatch):
  from test_research_service import make_service

  service, store, _, _ = make_service(tmp_path)
  runner = pipeline().ResearchPipeline(store, FixtureProvider(1), rate_seconds=0)

  def fail(**kwargs):
    raise RuntimeError("password=secret")

  monkeypatch.setattr(runner, "score", fail)
  result = runner.daily(service, limit=1, index_limit=0, fit_missing=False)
  assert result["evaluation"]["status"] == "failed"
  assert "secret" not in json.dumps(result)
  assert store.snapshot_metadata(result["report_snapshot"])["kind"] == "daily_report"


@pytest.mark.parametrize("horizon", [1, 2, 5, 20])
@pytest.mark.parametrize("prior", [None, .6])
def test_due_scores_save_independent_frozen_baselines_for_identical_labels(tmp_path, horizon, prior):
  from stock_forecaster.forecasting import future_business_days

  store = ResearchStore(tmp_path)
  identifier = evaluation_fixture(store)
  job = store.get_job(identifier)
  basis = store.snapshot_metadata(job["result"]["snapshot_id"])
  historical_prices = [98., 101., 100.]
  historical_dates = ["2026-08-31", "2026-09-01", "2026-09-02"]
  basis["model_provenance"] = {"lightgbm": {str(horizon): {
    "training_refs": {"bars": "original_training_data"},
    "trained_through": "2026-08-01", "training_up_prior": prior,
  }}}
  frozen = store.save_snapshot(pd.DataFrame({"date": historical_dates, "price": historical_prices}), basis)
  targets = future_business_days(pd.Timestamp("2026-09-02").date(), horizon, "600519.SS")
  later_prices = [100. * np.exp(.01 * number) for number in range(1, horizon + 1)]
  realized = pd.DataFrame({"ticker": ["600519.SS"] * (3 + horizon),
    "date": historical_dates + [day.isoformat() for day in targets],
    "close": historical_prices + later_prices})
  store.save_snapshot(realized, {"kind": "bars", "tickers": ["600519.SS"],
    "source": basis["source"], "adjustment": basis["adjustment"]})
  bundle = job["result"]
  bundle["snapshot_id"] = frozen
  bundle["horizons"][0].update(horizon=horizon, target_date=targets[-1].isoformat())
  with store._connect(write=True) as connection:
    connection.execute("UPDATE jobs SET result=? WHERE id=?", [json.dumps(bundle), identifier])
  runner = pipeline().ResearchPipeline(store)
  runner.clock = lambda: datetime(2026, 11, 3, 10, tzinfo=timezone.utc)
  result = runner.score(as_of=targets[-1].isoformat())
  assert result["scored"] == 1
  rows = store.read_snapshot(result["evaluation_snapshots"][0]).set_index("model")
  assert {"zero_return", "historical_mean", "historical_variance", "ewma"} <= set(rows.index)
  returns = np.diff(np.log(historical_prices))
  assert rows.loc["zero_return", "predicted"] == 0
  assert rows.loc["historical_mean", "predicted"] == pytest.approx(np.expm1(returns.mean() * horizon))
  assert rows.loc["historical_variance", "predicted"] == pytest.approx(np.var(returns) * horizon)
  expected_ewma = .94 * returns[0] ** 2 + .06 * returns[1] ** 2
  assert rows.loc["ewma", "predicted"] == pytest.approx(expected_ewma * horizon)
  for group in ("return", "variance", "probability"):
    assert rows.loc[rows.group == group, "actual"].nunique() == 1
  assert ("training_prior" in rows.index) == (prior is not None)
  if prior is not None:
    assert rows.loc["training_prior", "predicted"] == prior
  assert all("rank" not in json.loads(value) for value in rows.metrics)
  assert store.snapshot_metadata(result["evaluation_snapshots"][0])["baseline_config"]["ewma_decay"] == .94


def test_daily_partial_inference_is_not_reported_as_success(tmp_path):
  from test_research_service import make_service

  service, store, _, _ = make_service(tmp_path)

  def process():
    job = store.claim_job()
    ticker = job["payload"]["ticker"]
    snapshot = store.save_snapshot(pd.DataFrame({"date": ["2026-09-03", "2026-09-04"],
                                                "price": [100., 101.]}), {
      "kind": "prices", "ticker": ticker, "origin": "2026-09-04",
      "source": "akshare:stock_zh_a_hist_tx", "adjustment": "qfq",
    })
    store.complete_job(job["id"], {"status": "partial", "ticker": ticker,
      "snapshot_id": snapshot, "origin": "2026-09-04", "issued_at": "2026-09-04T10:00:00+00:00",
      "horizons": [{"horizon": 1, "target_date": "2026-09-07"}],
    })

  result = pipeline().ResearchPipeline(store, FixtureProvider(1), rate_seconds=0).daily(
    service, limit=1, index_limit=0, fit_missing=False, on_batch=process,
  )
  assert result["collection"]["success"] is True
  assert result["execution"]["predicted"] == 0
  assert result["execution"]["prediction"]["partial"] == 1
  assert result["evaluation"]["errors"] == []
  assert result["status"] == "partial"


def test_old_evaluation_gets_new_immutable_baseline_revision(tmp_path):
  store = ResearchStore(tmp_path)
  job = evaluation_fixture(store)
  realized = later_bars(store)
  old = store.save_snapshot(pd.DataFrame({"old_score": [1]}), {
    "kind": "evaluation", "forecast_id": job, "horizon": 2,
    "realized_snapshot": realized, "schema_version": 1,
  })
  result = pipeline().ResearchPipeline(store).score(as_of="2026-09-04")
  assert result["scored"] == 1
  assert result["evaluation_snapshots"][0] != old
  assert store.read_snapshot(old).columns.tolist() == ["old_score"]
  assert pipeline().ResearchPipeline(store).score(as_of="2026-09-04")["scored"] == 0
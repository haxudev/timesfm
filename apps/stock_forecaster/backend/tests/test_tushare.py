import importlib
import json

import httpx
import pandas as pd
import pytest

from stock_forecaster.config import Settings


def tushare():
  return importlib.import_module("stock_forecaster.research.tushare")


def test_secret_configuration_reads_dotenv_alias_without_serializing(tmp_path, monkeypatch):
  monkeypatch.chdir(tmp_path)
  (tmp_path / ".env").write_text("tushare_api_key=test-only-tushare-key\n", encoding="utf-8")
  settings = Settings()
  assert settings.tushare_api_key.get_secret_value() == "test-only-tushare-key"
  assert "test-only-tushare-key" not in repr(settings)
  assert "tushare_api_key" not in settings.model_dump()
  monkeypatch.setenv("TUSHARE_API_KEY", "environment-test-key")
  assert Settings().tushare_api_key.get_secret_value() == "environment-test-key"


def test_client_posts_only_to_official_https_with_secret_in_body():
  def respond(request):
    assert str(request.url) == "https://api.tushare.pro"
    assert request.method == "POST"
    assert json.loads(request.content) == {
      "api_name": "daily", "token": "test-token", "params": {"ts_code": "600519.SH", "trade_date": "20260904"},
      "fields": "ts_code,trade_date,close",
    }
    return httpx.Response(200, json={"code": 0, "data": {
      "fields": ["ts_code", "trade_date", "close"], "items": [["600519.SH", "20260904", 1200.]],
    }})

  with httpx.Client(transport=httpx.MockTransport(respond)) as client:
    result = tushare().TushareClient("test-token", client=client).query(
      "daily", {"ts_code": "600519.SH", "trade_date": "20260904"}, "ts_code,trade_date,close")
  assert result.iloc[0].close == 1200.


@pytest.mark.parametrize("payload,expected", [
  ({"code": 2002, "msg": "permission denied test-token"}, "tushare_permission_denied"),
  ({"code": -1, "msg": "token不正确 test-token"}, "tushare_auth_failed"),
  ({"code": -1, "msg": "每分钟最多访问此接口5次 test-token"}, "tushare_rate_limited"),
  ({"code": -1, "msg": "internal exception test-token"}, "provider_failed"),
  ({"code": 0, "data": {"fields": ["close"], "items": [[1, 2]]}}, "invalid_response"),
  ({"code": 0, "data": {"fields": ["close", "close"], "items": []}}, "invalid_response"),
])
def test_provider_errors_are_sanitized(payload, expected):
  with (
    httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as client,
    pytest.raises(ValueError, match=expected) as error,
  ):
    tushare().TushareClient("test-token", client=client).query("daily", {}, "close")
  assert "test-token" not in str(error.value)


def test_client_rejects_redirect_large_response_and_truncation():
  for response, maximum, expected in [
    (httpx.Response(302, headers={"Location": "https://example.com"}), 1024, "provider_failed"),
    (httpx.Response(200, content=b"x" * 100), 32, "response_too_large"),
    (httpx.Response(200, json={"code": 0, "data": {"fields": ["close"], "items": [[1], [2]]}}), 1024, "tushare_response_truncated"),
  ]:
    with (
      httpx.Client(transport=httpx.MockTransport(lambda request, reply=response: reply)) as client,
      pytest.raises(ValueError, match=expected),
    ):
      tushare().TushareClient("test-token", client=client, max_bytes=maximum).query("daily", {}, "close", row_limit=2)


def test_missing_token_and_unknown_api_fail_before_network():
  with pytest.raises(ValueError, match="tushare_not_configured"):
    tushare().TushareClient(None)
  with pytest.raises(ValueError, match="invalid_operation"):
    tushare().TushareClient("test-token").query("unknown_api", {}, "")


def test_client_refuses_unrequested_response_fields():
  payload = {"code": 0, "data": {"fields": ["close", "token"], "items": [[1, "private"]]}}
  with (
    httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as client,
    pytest.raises(ValueError, match="invalid_response"),
  ):
    tushare().TushareClient("test-token", client=client).query("daily", {}, "close")


def test_capability_probe_records_permission_and_empty_without_fallback(tmp_path):
  from stock_forecaster.research.providers import ProviderError
  from stock_forecaster.research.store import ResearchStore

  class ProbeClient:
    def query(self, api_name, params, fields):
      if api_name == "adj_factor":
        raise ProviderError("tushare_permission_denied")
      columns = fields.split(",")
      if api_name == "daily":
        return pd.DataFrame([["000001.SZ", "20260904", 10, 11, 9, 10, 9.8, 100, 1000]], columns=columns)
      return pd.DataFrame(columns=columns)

  store = ResearchStore(tmp_path)
  report = tushare().probe_access(store, client=ProbeClient(), rate_seconds=0)
  endpoints = {row["check"]: row for row in report["checks"]}
  assert endpoints["daily"]["status"] == "available"
  assert endpoints["adj_factor"]["status"] == "permission_denied"
  assert endpoints["industry_history"]["status"] == "empty"
  assert report["provider"] == "tushare"
  assert report["subscription_purchased"] is False
  assert report["historical_pit_verified"] is False
  assert store.snapshot_metadata(report["report_snapshot"])["kind"] == "provider_capabilities"
  assert store.list_jobs() == []


def raw_bars():
  return pd.DataFrame({"ts_code": ["600519.SH"] * 2, "trade_date": ["20260903", "20260904"],
                        "open": [10., 5.], "high": [11., 5.5], "low": [9., 4.5],
                        "close": [10., 5.], "vol": [12., 20.], "amount": [24., 10.]})


def test_tushare_bar_units_and_qfq_anchor_preserve_both_original_inputs():
  factors = pd.DataFrame({"ts_code": ["600519.SH"] * 2, "trade_date": ["20260903", "20260904"], "adj_factor": [1., 2.]})
  result = tushare().normalize_prices(raw_bars(), "600519.SS", "qfq", "2026-09-06T02:00:00+00:00", factors=factors)
  assert result.frame.close.tolist() == [5., 5.]
  assert result.frame.volume.tolist() == [1200., 2000.]
  assert result.frame.amount.tolist() == [24000., 10000.]
  assert result.metadata["source"] == "tushare"
  assert result.metadata["adjustment_anchor"] == "2026-09-04"
  assert result.metadata["point_in_time"] is False
  assert set(result.raw_frame.record_type) == {"daily", "adj_factor"}
  assert not result.frame.published_at.notna().any()
  assert "akshare" not in json.dumps(result.metadata)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "nonpositive", "wrong_ticker"])
def test_qfq_never_fills_or_uses_mismatched_factors(mutation):
  factors = pd.DataFrame({"ts_code": ["600519.SH"] * 2, "trade_date": ["20260903", "20260904"], "adj_factor": [1., 2.]})
  if mutation == "missing":
    factors = factors.iloc[:1]
  elif mutation == "duplicate":
    factors = pd.concat([factors, factors.iloc[:1]])
  elif mutation == "nonpositive":
    factors.loc[0, "adj_factor"] = 0
  else:
    factors.loc[0, "ts_code"] = "000001.SZ"
  with pytest.raises(ValueError, match="invalid_adjustment_factors"):
    tushare().normalize_prices(raw_bars(), "600519.SS", "qfq", "2026-09-06T02:00:00+00:00", factors=factors)


def test_tushare_provider_selection_is_explicit_and_never_passes_credentials(monkeypatch):
  from stock_forecaster.research import providers

  commands = []

  def run(command, timeout, max_bytes):
    commands.append(command)
    return b'{"rows":[],"metadata":{"source":"tushare"}}'

  monkeypatch.setattr(providers, "run_process", run)
  result = providers.ResearchProvider(provider="tushare").fetch("bars", ticker="600519.SS", start="2026-09-01", end="2026-09-04", adjustment="raw")
  assert result.metadata["source"] == "tushare"
  assert commands[0][-2:] == ["--provider", "tushare"]
  assert all("token" not in argument and "api_key" not in argument for argument in commands[0])


def test_cli_explicit_tushare_selection_uses_same_snapshot_pipeline(tmp_path, monkeypatch, capsys):
  from test_research_pipeline import FixtureProvider

  from stock_forecaster.research import cli, providers

  chosen = []

  def provider_factory(**kwargs):
    chosen.append(kwargs.get("provider"))
    return FixtureProvider(1)

  monkeypatch.setattr(providers, "ResearchProvider", provider_factory)
  assert cli.main(["--root", str(tmp_path), "probe", "--provider", "tushare", "--limit", "1", "--index-limit", "0", "--rate-seconds", "0"]) == 0
  assert chosen == ["tushare"]
  assert json.loads(capsys.readouterr().out)["requested"] == 1
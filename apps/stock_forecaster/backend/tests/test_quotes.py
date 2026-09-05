import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient

from stock_forecaster.errors import AppError
from stock_forecaster.main import create_app
from stock_forecaster.quotes import QuoteService, parse_sina, parse_tencent

TENCENT = (
  'v_sh600519="1~\u8d35\u5dde\u8305\u53f0~600519~1330.00~1298.88~1295.88~45416~27411~18005'
  '~1329.82~1~1329.59~22~1329.58~1~1329.50~2~1329.49~3'
  '~1330.00~46~1330.01~2~1330.02~29~1330.03~23~1330.06~1'
  '~~20260904161433~31.12~2.40~1338.86~1295.60~1330.00/45416/6022594729'
  '~45416~602259~0.36~20.42~~1338.86~1295.60~3.33~16626.09~16626.09~6.62";'
)
SINA = (
  'var hq_str_sh600519="\u8d35\u5dde\u8305\u53f0,1295.880,1298.880,1330.000,1338.860,1295.600,'
  '1329.820,1330.000,4541564,6022594729.000,'
  '100,1329.820,2200,1329.590,100,1329.580,200,1329.500,300,1329.490,'
  '4626,1330.000,200,1330.010,2900,1330.020,2300,1330.030,100,1330.060,'
  '2026-09-04,15:34:59,00";'
)


def test_tencent_parses_five_levels_and_converts_units():
  quote = parse_tencent(TENCENT, "600519.SS")
  assert quote.name == "\u8d35\u5dde\u8305\u53f0"
  assert quote.last == 1330
  assert quote.bids[0].volume == 100
  assert quote.asks[0].volume == 4600
  assert len(quote.bids) == len(quote.asks) == 5
  assert quote.volume == 4541600
  assert quote.amount == 6022590000
  assert quote.pe_ratio == 20.42
  assert quote.pb_ratio == 6.62
  assert quote.market_cap == pytest.approx(1662609000000)
  assert quote.as_of.isoformat() == "2026-09-04T16:14:33+08:00"


def test_sina_preserves_shares_and_marks_missing_valuations():
  quote = parse_sina(SINA, "sh600519")
  assert quote.ticker == "600519.SS"
  assert quote.bids[0].volume == 100
  assert quote.asks[0].volume == 4626
  assert quote.volume == 4541564
  assert quote.change == pytest.approx(31.12)
  assert quote.change_percent == pytest.approx(2.39591, rel=1e-4)
  assert quote.pe_ratio is None
  assert quote.pb_ratio is None
  assert quote.market_cap is None
  assert quote.as_of.isoformat() == "2026-09-04T15:34:59+08:00"


@pytest.mark.parametrize(("parser", "payload"), [(parse_tencent, TENCENT), (parse_sina, SINA)])
def test_index_snapshots_have_no_stock_order_book(parser, payload):
  payload = payload.replace("sh600519", "sh000001").replace("600519", "000001")
  quote = parser(payload, "sh000001")
  assert quote.instrument_type == "index"
  assert quote.bids == quote.asks == []
  assert quote.pe_ratio is quote.pb_ratio is quote.market_cap is None
  assert quote.volume == (45416 if parser is parse_tencent else 4541564)


def test_missing_nonfinite_or_zero_book_values_are_not_fabricated():
  quote = parse_tencent(
    TENCENT.replace("1329.82~1", "0~0").replace("~20.42~", "~NaN~"),
    "600519",
  )
  assert quote.bids[0].price is None
  assert quote.pe_ratio is None


@pytest.mark.parametrize("source", ["tencent", "sina"])
def test_recorded_shanghai_index_quote_has_points_and_share_volume(source):
  tencent = (
    'v_sh000001="1~上证指数~000001~3930.12~3942.09~3955.55~537286161~0~0'
    '~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0'
    '~~20260904161403~-11.97~-0.30~3980.20~3915.22~3930.12/537286161/938255187184'
    '~537286161~93825519~1.11~17.06~~3980.20~3915.22~1.65~614925.05~694637.13~0.00";'
  )
  sina = (
    'var hq_str_sh000001="上证指数,3955.5489,3942.0879,3930.1164,3980.2022,'
    '3915.2213,0,0,537286161,938255187184,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,'
    '2026-09-04,15:35:31,00,";'
  )
  quote = (
    parse_tencent(tencent, "sh000001")
    if source == "tencent" else parse_sina(sina, "sh000001")
  )
  assert quote.name == "上证指数"
  assert quote.last == pytest.approx(3930.12, abs=0.01)
  assert quote.amount == pytest.approx(938255187184, rel=1e-6)
  assert quote.volume == 537286161
  assert quote.bids == quote.asks == []


@pytest.mark.parametrize("payload", ['v_pv_none_match="1";', TENCENT[:40]])
def test_malformed_quotes_are_rejected(payload):
  with pytest.raises(ValueError):
    parse_tencent(payload, "600519.SS")


def test_quote_api_falls_back_to_sina_and_caches_aliases():
  calls = []

  def upstream(request):
    calls.append(request.url.host)
    if request.url.host == "qt.gtimg.cn":
      return httpx.Response(503)
    assert request.headers["Referer"] == "https://finance.sina.com.cn/"
    return httpx.Response(200, content=SINA.encode("gbk"))

  service = QuoteService(transport=httpx.MockTransport(upstream))
  client = TestClient(create_app(quote_service=service))
  response = client.get("/api/v1/quotes/600519")
  assert response.status_code == 200
  assert response.json()["source"] == "sina"
  assert response.json()["name"] == "\u8d35\u5dde\u8305\u53f0"
  assert response.json()["warnings"]
  assert client.get("/api/v1/quotes/sh600519").json() == response.json()
  assert calls == ["qt.gtimg.cn", "hq.sinajs.cn"]


def test_explicit_source_does_not_silently_switch():
  service = QuoteService(transport=httpx.MockTransport(lambda _: httpx.Response(503)))
  with pytest.raises(AppError) as caught:
    service.get("600519", source="sina")
  assert caught.value.code == "quote_unavailable"


def test_exhausted_sources_return_error_and_overseas_is_unsupported():
  service = QuoteService(transport=httpx.MockTransport(lambda _: httpx.Response(200, text='')))
  client = TestClient(create_app(quote_service=service))
  response = client.get("/api/v1/quotes/600519")
  assert response.status_code == 502
  assert response.json()["error"]["code"] == "quote_unavailable"
  assert client.get("/api/v1/quotes/SPY").status_code == 422


def test_slow_quote_does_not_block_another_cached_symbol():
  started = threading.Event()
  release = threading.Event()

  def upstream(request):
    if "sh600519" in str(request.url):
      started.set()
      assert release.wait(timeout=5)
      text = TENCENT
    else:
      text = TENCENT.replace("sh600519", "sz000001").replace("600519", "000001")
    return httpx.Response(200, content=text.encode("gbk"))

  service = QuoteService(transport=httpx.MockTransport(upstream))
  service.get("000001")
  with ThreadPoolExecutor(max_workers=2) as executor:
    slow = executor.submit(service.get, "600519")
    try:
      assert started.wait(timeout=2)
      cached = executor.submit(service.get, "000001")
      assert cached.result(timeout=0.5).ticker == "000001.SZ"
    finally:
      release.set()
    assert slow.result(timeout=2).ticker == "600519.SS"
from stock_forecaster.config import Settings


def test_cors_origins_accept_json_or_comma_separated(monkeypatch):
  monkeypatch.setenv(
    "STOCK_FORECASTER_CORS_ORIGINS",
    '["http://localhost:5173","https://example.test"]',
  )
  assert Settings().cors_origins == [
    "http://localhost:5173",
    "https://example.test",
  ]
  monkeypatch.setenv(
    "STOCK_FORECASTER_CORS_ORIGINS",
    "http://localhost:5173,https://example.test",
  )
  assert Settings().cors_origins == [
    "http://localhost:5173",
    "https://example.test",
  ]

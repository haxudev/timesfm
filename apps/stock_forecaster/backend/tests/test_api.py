def test_health_does_not_load_model(app_parts):
  client, _, _, loads = app_parts
  response = client.get("/api/v1/health")
  assert response.status_code == 200
  assert response.json()["model_state"] == "not_loaded"
  assert loads == []


def test_market_data_is_cached_and_normalized(app_parts):
  client, provider, _, _ = app_parts
  first = client.get("/api/v1/market-data/%20spy%20?period=1y&include_volume=true")
  second = client.get("/api/v1/market-data/SPY?period=1y")
  assert first.status_code == 200
  assert first.json()["ticker"] == "SPY"
  assert first.json()["price_column"] == "Adj Close"
  assert second.status_code == 200
  assert provider.calls == 1


def test_forecast_endpoint_uses_trusted_checkpoint_and_quantile_mapping(app_parts):
  client, _, adapter, loads = app_parts
  response = client.post(
    "/api/v1/forecasts",
    json={
      "ticker": "spy",
      "period": "1y",
      "horizon": 3,
      "context_length": 32,
      "target": "log_return",
      "device": "cpu",
    },
  )
  assert response.status_code == 200, response.text
  body = response.json()
  assert body["model"]["checkpoint"] == "trusted/checkpoint"
  assert body["forecasts"][0]["quantiles"]["q0.1"] == -0.02
  assert body["forecasts"][0]["quantiles"]["q0.9"] == 0.02
  assert body["context"]["used_length"] == 32
  assert len(adapter.contexts) == 1
  assert loads == ["cpu"]


def test_validation_errors_have_request_id(app_parts):
  client, _, _, _ = app_parts
  response = client.post(
    "/api/v1/forecasts",
    headers={"X-Request-ID": "test-request"},
    json={"ticker": "../bad", "horizon": 61},
  )
  assert response.status_code == 422
  error = response.json()["error"]
  assert error["code"] == "validation_error"
  assert error["request_id"] == "test-request"


def test_model_unavailable_uses_consistent_error(app_parts, monkeypatch):
  client, _, _, _ = app_parts
  monkeypatch.setattr("stock_forecaster.model.cuda_available", lambda: False)
  response = client.post(
    "/api/v1/forecasts",
    json={
      "ticker": "SPY",
      "period": "1y",
      "horizon": 2,
      "context_length": 32,
      "device": "cuda",
    },
  )
  assert response.status_code == 422
  assert response.json()["error"]["code"] == "device_unavailable"


def test_invalid_market_date_range_uses_consistent_error(app_parts):
  client, _, _, _ = app_parts
  response = client.get("/api/v1/market-data/SPY?start=2024-01-01")
  assert response.status_code == 422
  assert response.json()["error"]["code"] == "validation_error"

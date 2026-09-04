import numpy as np
from conftest import FakeAdapter, FakeProvider

from stock_forecaster.backtesting import BacktestService, calculate_metrics
from stock_forecaster.market_data import MarketDataService, ProviderResult
from stock_forecaster.model import ModelManager
from stock_forecaster.schemas import BacktestRequest


def test_metric_calculations_and_baseline_direction():
  actual = np.array([0.1, -0.1])
  predicted = np.array([0.05, -0.05])
  actual_prices = np.array([110.517, 100.0])
  metrics = calculate_metrics(
    actual,
    predicted,
    100,
    actual_prices,
    np.array([0.0, -0.2]),
    np.array([0.2, 0.0]),
  )
  assert metrics.return_mae == 0.05
  assert metrics.directional_accuracy == 1
  assert metrics.interval_coverage == 1
  assert metrics.return_rmse == 0.05


def test_walk_forward_context_excludes_future(market_frame):
  provider = FakeProvider(ProviderResult(market_frame))
  adapter = FakeAdapter()
  manager = ModelManager("checkpoint", factory=lambda *_: adapter)
  service = BacktestService(MarketDataService(provider, 0), manager)
  request = BacktestRequest(
    ticker="SPY",
    period="1y",
    horizon=3,
    context_length=32,
    evaluation_windows=3,
    step_size=2,
    device="cpu",
  )
  response = service.run(request)
  all_returns = np.diff(np.log(market_frame["Adj Close"].to_numpy()))
  last_origin = len(all_returns) - request.horizon
  expected_origins = sorted(
    list(range(last_origin, request.context_length - 1, -request.step_size))[:3]
  )
  assert response.window_count == 3
  for context, origin in zip(adapter.contexts, expected_origins):
    np.testing.assert_allclose(
      context,
      all_returns[origin - request.context_length : origin],
      rtol=1e-6,
    )
  assert set(response.aggregate) == {
    "timesfm",
    "zero_return",
    "historical_mean",
    "random_walk",
  }


def test_backtest_request_bounds():
  try:
    BacktestRequest(evaluation_windows=101)
  except ValueError:
    pass
  else:
    raise AssertionError("evaluation_windows above 100 must be rejected")

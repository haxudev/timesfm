from __future__ import annotations

import numpy as np

from .errors import AppError
from .features import log_returns, returns_to_prices, validate_prices
from .market_data import MarketDataService
from .model import ModelManager
from .schemas import (
  BacktestRequest,
  BacktestResponse,
  BacktestWindow,
  MetricSet,
)


def calculate_metrics(
  actual_returns: np.ndarray,
  predicted_returns: np.ndarray,
  last_price: float,
  actual_prices: np.ndarray,
  lower_returns: np.ndarray | None = None,
  upper_returns: np.ndarray | None = None,
) -> MetricSet:
  errors = predicted_returns - actual_returns
  predicted_prices = returns_to_prices(last_price, predicted_returns)
  coverage = None
  width = None
  if lower_returns is not None and upper_returns is not None:
    coverage = float(
      np.mean((actual_returns >= lower_returns) & (actual_returns <= upper_returns))
    )
    lower_prices = returns_to_prices(last_price, lower_returns)
    upper_prices = returns_to_prices(last_price, upper_returns)
    width = float(np.mean(upper_prices - lower_prices))
  return MetricSet(
    return_mae=float(np.mean(np.abs(errors))),
    return_rmse=float(np.sqrt(np.mean(np.square(errors)))),
    directional_accuracy=float(
      np.mean(np.sign(predicted_returns) == np.sign(actual_returns))
    ),
    price_mae=float(np.mean(np.abs(predicted_prices - actual_prices))),
    interval_coverage=coverage,
    average_interval_width=width,
  )


def aggregate_metrics(windows: list[dict[str, MetricSet]]) -> dict[str, MetricSet]:
  result: dict[str, MetricSet] = {}
  for name in windows[0]:
    metrics = [window[name] for window in windows]
    result[name] = MetricSet(
      return_mae=float(np.mean([item.return_mae for item in metrics])),
      return_rmse=float(
        np.sqrt(np.mean([item.return_rmse**2 for item in metrics]))
      ),
      directional_accuracy=float(
        np.mean([item.directional_accuracy for item in metrics])
      ),
      price_mae=float(np.mean([item.price_mae for item in metrics])),
      interval_coverage=(
        float(np.mean([item.interval_coverage for item in metrics]))
        if metrics[0].interval_coverage is not None
        else None
      ),
      average_interval_width=(
        float(np.mean([item.average_interval_width for item in metrics]))
        if metrics[0].average_interval_width is not None
        else None
      ),
    )
  return result


class BacktestService:
  def __init__(self, market: MarketDataService, model: ModelManager) -> None:
    self.market = market
    self.model = model

  def run(self, request: BacktestRequest) -> BacktestResponse:
    history = self.market.get(request.ticker, request)
    prices = validate_prices(
      np.array([observation.price for observation in history.observations])
    )
    dates = [observation.date for observation in history.observations]
    returns = log_returns(prices)
    first_origin = request.context_length
    last_origin = len(returns) - request.horizon
    if last_origin < first_origin:
      raise AppError(
        "insufficient_history",
        "History is too short for the requested backtest.",
        422,
      )
    origins = list(range(last_origin, first_origin - 1, -request.step_size))
    origins = sorted(origins[: request.evaluation_windows])
    windows: list[BacktestWindow] = []
    metric_windows: list[dict[str, MetricSet]] = []
    for origin in origins:
      context = returns[origin - request.context_length : origin]
      actual = returns[origin : origin + request.horizon]
      actual_prices = prices[origin + 1 : origin + request.horizon + 1]
      last_price = float(prices[origin])
      output, _, _ = self.model.predict(context, request.horizon, request.device)
      zero = np.zeros(request.horizon)
      mean = np.full(request.horizon, float(np.mean(context)))
      metrics = {
        "timesfm": calculate_metrics(
          actual,
          output.point,
          last_price,
          actual_prices,
          output.quantiles[:, 0],
          output.quantiles[:, 8],
        ),
        "zero_return": calculate_metrics(
          actual, zero, last_price, actual_prices
        ),
        "historical_mean": calculate_metrics(
          actual, mean, last_price, actual_prices
        ),
        "random_walk": calculate_metrics(
          actual, zero, last_price, actual_prices
        ),
      }
      metric_windows.append(metrics)
      windows.append(
        BacktestWindow(
          origin=dates[origin],
          context_start=dates[origin - request.context_length],
          context_end=dates[origin],
          forecast_end=dates[origin + request.horizon],
          metrics=metrics,
        )
      )
    return BacktestResponse(
      ticker=history.ticker,
      window_count=len(windows),
      observation_count=history.count,
      aggregate=aggregate_metrics(metric_windows),
      windows=windows,
      warning=(
        "Historical evaluation does not imply future performance or profitability."
      ),
    )

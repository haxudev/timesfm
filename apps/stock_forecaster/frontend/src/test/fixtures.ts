import type { BacktestResponse, ForecastResponse } from '../types'

export const forecastFixture: ForecastResponse = {
  ticker: 'SPY',
  target: 'log_return',
  history: {
    ticker: 'SPY',
    price_column: 'Adj Close',
    start: '2024-01-01',
    end: '2024-06-28',
    count: 2,
    currency: 'USD',
    observations: [
      { date: '2024-06-27', price: 99, volume: 1000 },
      { date: '2024-06-28', price: 100, volume: 1200 },
    ],
  },
  context: {
    requested_length: 512,
    used_length: 120,
    observation_count: 121,
    origin: '2024-06-28',
  },
  model: {
    checkpoint: 'google/timesfm-3.0-pytorch',
    device: 'cpu',
    state: 'ready',
  },
  forecasts: [{
    date: '2024-07-01',
    point_return: 0.01,
    quantiles: {
      'q0.1': -0.02,
      'q0.2': -0.01,
      'q0.3': -0.005,
      'q0.4': 0,
      'q0.5': 0.01,
      'q0.6': 0.015,
      'q0.7': 0.02,
      'q0.8': 0.025,
      'q0.9': 0.03,
    },
    point_price: 101.005,
    lower_price: 98.02,
    upper_price: 103.05,
  }],
  warnings: ['Approximate paths are not joint confidence intervals.'],
  summary: {
    latest_price: 100,
    predicted_final_price: 101.005,
    cumulative_return: 0.01,
    final_lower_price: 98.02,
    final_upper_price: 103.05,
    horizon: 1,
  },
}

const metric = {
  return_mae: 0.01,
  return_rmse: 0.02,
  directional_accuracy: 0.6,
  price_mae: 1.2,
  interval_coverage: null,
  average_interval_width: null,
}

export const backtestFixture: BacktestResponse = {
  ticker: 'SPY',
  window_count: 4,
  observation_count: 100,
  aggregate: {
    timesfm: { ...metric, interval_coverage: 0.8, average_interval_width: 4 },
    zero_return: metric,
    historical_mean: metric,
    random_walk: metric,
  },
  warning: 'Historical evaluation does not imply future performance.',
}

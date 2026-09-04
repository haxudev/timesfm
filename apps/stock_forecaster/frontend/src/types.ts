export type Device = 'auto' | 'cpu' | 'cuda'
export type Target = 'log_return' | 'price'

export interface ForecastRequest {
  ticker: string
  period: string | null
  start?: string
  end?: string
  horizon: number
  context_length: number
  target: Target
  device: Device
}

export interface MarketObservation {
  date: string
  price: number
  volume: number | null
}

export interface MarketData {
  ticker: string
  price_column: string
  start: string
  end: string
  count: number
  currency: string | null
  observations: MarketObservation[]
}

export interface ForecastPoint {
  date: string
  point_return: number
  quantiles: Record<string, number>
  point_price: number
  lower_price: number
  upper_price: number
}

export interface ForecastResponse {
  ticker: string
  target: Target
  history: MarketData
  context: {
    requested_length: number
    used_length: number
    observation_count: number
    origin: string
  }
  model: { checkpoint: string; device: string; state: string }
  forecasts: ForecastPoint[]
  warnings: string[]
  summary: {
    latest_price: number
    predicted_final_price: number
    cumulative_return: number
    final_lower_price: number
    final_upper_price: number
    horizon: number
  }
}

export interface MetricSet {
  return_mae: number
  return_rmse: number
  directional_accuracy: number
  price_mae: number
  interval_coverage: number | null
  average_interval_width: number | null
}

export interface BacktestResponse {
  ticker: string
  window_count: number
  observation_count: number
  aggregate: Record<string, MetricSet>
  warning: string
}

export interface HealthResponse {
  status: string
  device: string
  version: string
  model_state: string
}

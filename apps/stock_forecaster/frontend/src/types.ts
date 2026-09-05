export type Device = 'auto' | 'cpu' | 'cuda'
export type Target = 'log_return' | 'price'
export type QuoteSource = 'auto' | 'tencent' | 'sina'

export interface QuoteLevel {
  price: number | null
  volume: number | null
}

export interface QuoteResponse {
  ticker: string
  instrument_type?: 'stock' | 'index'
  name: string
  currency: 'CNY'
  source: 'tencent' | 'sina'
  as_of: string
  last: number | null
  previous_close: number | null
  open: number | null
  high: number | null
  low: number | null
  change: number | null
  change_percent: number | null
  volume: number | null
  amount: number | null
  bids: QuoteLevel[]
  asks: QuoteLevel[]
  pe_ratio: number | null
  pb_ratio: number | null
  market_cap: number | null
  float_market_cap: number | null
  turnover_rate: number | null
  warnings: string[]
}

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
  instrument_type?: 'stock' | 'index'
  name?: string | null
  price_column: string
  start: string
  end: string
  count: number
  currency: string | null
  source?: string
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

export type PredictionHorizon = 1 | 5 | 20
export type JobStatus = 'pending' | 'running' | 'succeeded' | 'partial' | 'failed' | 'cancelled'

export interface ResearchSymbol {
  ticker: string
  name: string | null
  instrument_type: 'stock' | 'index'
}

export interface ComponentState {
  status: 'ready' | 'not_ready' | 'unavailable' | 'not_supported'
  reason: string | null
  artifact_id: string | null
}

export interface HorizonPrediction {
  horizon: PredictionHorizon
  target_date: string
  timesfm_return: number | null
  lightgbm_return: number | null
  up_probability: number | null
  volatility: number | null
}

export interface PredictionBundle extends ResearchSymbol {
  schema_version: 2
  bundle_id: string
  origin: string
  issued_at: string
  snapshot_id: string
  status: 'succeeded' | 'partial'
  horizons: HorizonPrediction[]
  path: Array<{ date: string; cumulative_return: number }>
  history: Array<{ date: string; price: number }>
  components: Record<'timesfm' | 'lightgbm' | 'garch', ComponentState>
  warnings: string[]
}

export interface SubmittedJob {
  job_id: string
  status: JobStatus
}

export interface PredictionJob {
  id: string
  status: JobStatus
  cancellation_requested: boolean
  result: PredictionBundle | null
  error: { code: string; message: string } | null
}
